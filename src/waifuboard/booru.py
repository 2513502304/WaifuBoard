"""
Booru Image Board API implementation.
"""

import asyncio
import logging
import os
import random
import time
from http.cookiejar import CookieJar
from typing import (
    Any,
    Literal,
    IO,
    TypeAlias,
    cast,
)
from collections.abc import (
    Callable,
    Collection,
    Coroutine,
    Iterable,
    AsyncIterable,
    Mapping,
)
from urllib.parse import urlparse, parse_qs, parse_qsl, quote, unquote
from urllib.request import getproxies

import aiofiles
import orjson
import pandas as pd
from aiofiles import os as aioos
from aiofiles import tempfile as aiotempfile
from fake_useragent import UserAgent
from niquests import AsyncSession
from niquests.adapters import AsyncBaseAdapter, AsyncHTTPAdapter
from niquests.cookies import (
    RequestsCookieJar,
    cookiejar_from_dict,
    extract_cookies_to_jar,
    merge_cookies,
)
from niquests.models import AsyncResponse, PreparedRequest, Request, Response
from niquests.typing import (
    ASGIApp,
    AsyncBodyType,
    AsyncHookType,
    AsyncHttpAuthenticationType,
    AsyncResolverType,
    # BodyType,
    CacheLayerAltSvcType,
    # CookiesType,
    HeadersType,
    HttpAuthenticationType,
    HttpMethodType,
    MultiPartFilesAltType,
    MultiPartFilesType,
    # ProxyType,
    # QueryParameterType,
    RetryType,
    TimeoutType,
    TLSClientCertType,
    TLSVerifyType,
)
from niquests.extensions.revocation import RevocationConfiguration
from niquests.hooks import (
    AsyncLifeCycleHook,
    AsyncLeakyBucketLimiter,
    AsyncTokenBucketLimiter,
)
from niquests.exceptions import RequestException
from tenacity import AsyncRetrying, RetryError, TryAgain, retry
from tenacity.after import after_log
from tenacity.before import before_log
# from tenacity.before_sleep import before_sleep_log
from tenacity.nap import sleep
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential_jitter
from urllib3.util.retry import Retry
from urllib3.util.timeout import Timeout

from .observability import (
    logger,
    format_response_metrics,
    format_retry_log,
    format_elapsed,
    get_body_size,
    before_sleep_log,
)
from .proxy import ProxyCooldownTracker, normalize_proxy, resolve_proxy
from .utils import normalize_filepath

# niquests intentionally keeps its public typing narrower than some runtime-accepted
# values; keep WaifuBoard's wrapper types explicit when we rely on that behavior.
# Reference: https://github.com/jawah/niquests/pull/399
BodyFormValueType: TypeAlias = str | bytes | int | float | bool | None
BodyFormType: TypeAlias = (
    list[tuple[str, BodyFormValueType | list[BodyFormValueType] | tuple[BodyFormValueType, ...]]]
    | dict[str, BodyFormValueType | list[BodyFormValueType] | tuple[BodyFormValueType, ...]]
)
BodyType: TypeAlias = (
    str
    | bytes
    | bytearray
    | IO[bytes]
    | IO[str]
    | BodyFormType
    | Iterable[bytes]
    | Iterable[str]
)
CookiesType: TypeAlias = dict[str, str] | RequestsCookieJar | CookieJar
ProxyType: TypeAlias = dict[str, str] | str
ProxiesType: TypeAlias = (
    tuple[dict[str, str], ...] | tuple[str, ...] | dict[str, str] | str
)
QueryParameterScalarType: TypeAlias = str | bytes | int | float | bool | None
QueryParameterValueType: TypeAlias = (
    QueryParameterScalarType
    | list[QueryParameterScalarType]
    | tuple[QueryParameterScalarType, ...]
    | dict[str, Any]
)
QueryParameterType: TypeAlias = Mapping[str, QueryParameterValueType]


class UnsetType:
    """Sentinel type indicating a parameter was not explicitly passed."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()


__all__ = [
    "Booru",
    "BooruComponent",
]


class Booru:
    """
    Base Booru Image Board API
    """

    def __init__(
        self,
        *,
        directory: str = "./downloads",
        default_headers: bool = True,
        logger_level: int | str = logging.INFO,
        base_url: str | None = None,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        cookies: CookiesType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        proxies: ProxiesType | None = None,
        trust_env: bool = True,
        max_redirects: int = 30,
        retries: RetryType = 3,
        max_attempt_number: int | None = 3,
        proxy_cooldown_threshold: int | None = None,
        proxy_cooldown: int | float = 600,
        proxy_cooldown_statuses: Collection[int] | None = (429, 502, 503, 504),
        rate_limit: int | float | None = 10.0,
        timeout: TimeoutType | None = None,
        multiplexed: bool = True,
        disable_http1: bool = False,
        disable_http2: bool = False,
        disable_http3: bool = False,
        disable_ipv6: bool = False,
        disable_ipv4: bool = False,
        pool_connections: int = 10,
        pool_maxsize: int = 30,
        happy_eyeballs: bool | int = False,
        keepalive_delay: float | int | None = 3600.0,
        keepalive_idle_window: float | int | None = 60.0,
        hooks: AsyncHookType[PreparedRequest | Response | AsyncResponse] | None = None,
        verify: TLSVerifyType = True,
        cert: TLSClientCertType | None = None,
        resolver: AsyncResolverType | None = None,
        source_address: tuple[str, int] | None = None,
        quic_cache_layer: CacheLayerAltSvcType | None = None,
        revocation_configuration: (
            RevocationConfiguration | None
        ) = RevocationConfiguration(),
        app: ASGIApp | None = None,
    ):
        """
        Wraps the niquests.AsyncSession client type, providing a more friendly API interface

        Args:
            directory (str, optional): The root directory of the storage files for the current client platform. Defaults to "./downloads".
            default_headers (bool, optional): Whether to set default browser headers. Defaults to True.
            logger_level (int | str, optional): The log level. Defaults to logging.INFO.
            base_url (str, optional): Automatically set a URL prefix (or base url) on every request emitted if applicable. Defaults to None.
            headers (HeadersType, optional): Default headers to be used on every request emitted. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to attach to each Request <Request>. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued query parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            cookies (CookiesType, optional): A CookieJar containing all currently outstanding cookies set on this session. By default it is a RequestsCookieJar <requests.cookies.RequestsCookieJar>, but may be any other cookielib.CookieJar compatible object. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Default authentication tuple or object to attach to every request emitted. Defaults to None.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and host to the URL of the proxy (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to be used on each Request <Request>. If a single string is provided, it will be used for both http and https. It can also be a tuple of such values; an element will be randomly selected per request. When not provided and trust_env is True, the process environment's proxy settings are captured as the default, giving an effective priority of request > session > env. Defaults to None.
            trust_env (bool, optional): Trust environment settings for proxy configuration, default authentication and similar. Defaults to True.
            max_redirects (int, optional): Maximum number of redirects allowed. If the request exceeds this limit, a TooManyRedirects exception is raised. This defaults to requests.models.DEFAULT_REDIRECT_LIMIT, which is 30. Defaults to 30.
            retries (RetryType, optional): Configure a number of times a request must be automatically retried before giving up. Defaults to 3.
            max_attempt_number (int, optional): Default outer retry budget (tenacity-level) for request methods. Used when a request method does not pass its own max_attempt_number. If both this and the request-level value are None, the underlying call falls back to a single attempt. Defaults to 3.
            proxy_cooldown_threshold (int, optional): Consecutive per-proxy failures before temporarily cooling down that proxy. Set to None to disable proxy cooldown. Defaults to None.
            proxy_cooldown (int | float, optional): Seconds a failed proxy stays unavailable after reaching proxy_cooldown_threshold. Defaults to 600.
            proxy_cooldown_statuses (Collection[int], optional): HTTP statuses that count as proxy failures when proxy cooldown is enabled. Set to None to count only transport exceptions. Defaults to (429, 502, 503, 504).
            rate_limit (int | float, optional): Maximum requests per second. Defaults to 10.0.
            timeout (TimeoutType, optional): Default timeout configuration to be used if no timeout is provided in exposed methods. Defaults to None.
            multiplexed (bool, optional): Enable or disable concurrent request when the remote host support HTTP/2 onward. Defaults to True.
            disable_http1 (bool, optional): Toggle to disable negotiating HTTP/1 with remote peers. Set it to True so that you may be able to force HTTP/2 over cleartext (h2c). Defaults to False.
            disable_http2 (bool, optional): Toggle to disable negotiating HTTP/2 with remote peers. Defaults to False.
            disable_http3 (bool, optional): Toggle to disable negotiating HTTP/3 with remote peers. Defaults to False.
            disable_ipv6 (bool, optional): Toggle to disable using IPv6 even if the remote host supports IPv6. Defaults to False.
            disable_ipv4 (bool, optional): Toggle to disable using IPv4 even if the remote host supports IPv4. Defaults to False.
            pool_connections (int, optional): Number of concurrent hosts to be kept alive by this Session at a maximum. Defaults to 10.
            pool_maxsize (int, optional): Maximum number of concurrent connections per (single) host at a time. Defaults to 30.
            happy_eyeballs (bool | int, optional): Use IETF Happy Eyeballs algorithm when trying to connect to a remote host by issuing concurrent connection using available IPs. Tries IPv6/IPv4 at the same time or multiple IPv6 / IPv4. The domain name must yield multiple A or AAAA records for this to be used. Defaults to False.
            keepalive_delay (float | int, optional): Delay expressed in seconds, in which we should keep a connection alive by sending PING frame. This only applies to HTTP/2 onward. Defaults to 3600.0.
            keepalive_idle_window (float | int, optional): Delay expressed in seconds, in which we should send a PING frame after the connection being completely idle. This only applies to HTTP/2 onward. Defaults to 60.0.
            hooks (AsyncHookType[PreparedRequest | Response | AsyncResponse], optional): Default hooks to be used on every request emitted. Can be a dictionary mapping hook names to lists of callables, or a LifeCycleHook instance. Defaults to None.
            verify (TLSVerifyType, optional): SSL Verification default. Defaults to True, requiring requests to verify the TLS certificate at the remote end. If verify is set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Only set this to False for testing. Defaults to True.
            cert (TLSClientCertType, optional): SSL client certificate default, if String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            resolver (AsyncResolverType, optional): Specify a DNS resolver that should be used within this Session. Defaults to None.
            source_address (tuple[str, int], optional): Bind Session to a specific network adapter and/or port so that all outgoing requests. Defaults to None.
            quic_cache_layer (CacheLayerAltSvcType, optional): Provide an external cache mechanism to store HTTP/3 host capabilities. Defaults to None.
            revocation_configuration (RevocationConfiguration, optional): How should that session do the certificate revocation check. Set it as None to disable this additional security measure. Defaults to RevocationConfiguration().
            app (ASGIApp, optional): A WSGI (e.g. Flask) or ASGI (e.g. FastAPI) app to be mounted automatically. Defaults to None.
        """
        # 当前客户端平台的存储文件根目录
        self.directory = directory

        if headers is None and default_headers:
            headers = {
                "User-Agent": UserAgent().random,
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "*/*",
                "Connection": "keep-alive",
            }

        if cookies is not None:
            if isinstance(cookies, dict):
                cookies = cookiejar_from_dict(cookies, thread_free=True)

        # 预吸收环境变量代理，让最终优先级从 niquests 的 request > env > session 变为 request > session > env
        # niquests 在 Session.send 里会先看 request 级 proxies，没有才 fallback 到 env（当 trust_env=True）
        # 最后才是 session.proxies — 所以 session 配置天然弱于环境变量，无法通过参数直接翻转
        # 我们的做法：若用户未显式提供 proxies 且 trust_env=True，就在这里把 env 代理抓出来当作 session 默认
        # 同时 self.client.proxies 始终置空，真正的代理值全部由 request() 在调用时以 request 级形式注入
        # 这样 request 级永远压过 env，等价于得到 request > session > env 的优先级
        if proxies is None and trust_env:
            proxies = getproxies() or None

        if retries is not None:
            if isinstance(retries, int):
                retries = Retry(
                    total=retries,
                    redirect=True,
                    allowed_methods=frozenset(
                        ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"]
                    ),
                    status_forcelist=frozenset([413, 429, 503]),
                    backoff_factor=1,
                    backoff_max=10,
                    raise_on_redirect=True,
                    raise_on_status=True,
                    history=None,
                    respect_retry_after_header=True,
                    remove_headers_on_redirect=frozenset(
                        {"Proxy-Authorization", "Cookie", "Authorization"}
                    ),
                    backoff_jitter=3,
                    retry_after_max=21600,
                )

        if rate_limit is not None:
            if isinstance(rate_limit, (int, float)):
                limiter = AsyncLeakyBucketLimiter(rate=rate_limit)
            else:
                raise ValueError("rate_limit must be a int or float")
            if hooks is not None:
                if isinstance(hooks, dict):
                    if pre_request := hooks.get("pre_request"):
                        if isinstance(pre_request, list):
                            pre_request.append(limiter.pre_request)
                        else:
                            hooks["pre_request"] = [pre_request, limiter.pre_request]
                    else:
                        hooks["pre_request"] = [limiter.pre_request]
                elif isinstance(hooks, AsyncLifeCycleHook):
                    hooks += limiter
                else:
                    raise ValueError("hooks must be a dictionary or LifeCycleHook")
            else:
                hooks = limiter

        # 创建底层 niquests 客户端
        self.client = AsyncSession(
            resolver=resolver,
            source_address=source_address,
            quic_cache_layer=quic_cache_layer,
            retries=retries,
            multiplexed=multiplexed,
            disable_http1=disable_http1,
            disable_http2=disable_http2,
            disable_http3=disable_http3,
            disable_ipv6=disable_ipv6,
            disable_ipv4=disable_ipv4,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            happy_eyeballs=happy_eyeballs,
            keepalive_delay=keepalive_delay,
            keepalive_idle_window=keepalive_idle_window,
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            auth=auth,
            hooks=hooks,
            revocation_configuration=revocation_configuration,
            app=app,
        )
        self.client.params = params if params is not None else {}
        self.client.cookies = (
            cookies
            if cookies is not None
            else cookiejar_from_dict({}, thread_free=True)
        )
        self.client.proxies = {}
        self._proxies: ProxiesType | None = proxies
        self._max_attempt_number: int | None = max_attempt_number
        self._proxy_cooldown = ProxyCooldownTracker(
            threshold=proxy_cooldown_threshold,
            cooldown=proxy_cooldown,
        )
        self._proxy_cooldown_statuses = set(proxy_cooldown_statuses or ())
        self.client.trust_env = trust_env
        self.client.max_redirects = max_redirects
        self.client.verify = verify
        self.client.cert = cert

        # 设置日志级别
        logging.getLogger("WaifuBoard").setLevel(logger_level)

    @property
    def auth(self):
        """
        发送请求时使用的身份验证类
        返回底层 niquests 客户端的 auth 属性
        """
        return self.client.auth

    @auth.setter
    def auth(self, auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None):
        """
        设置发送请求时使用的身份验证类
        将传递给底层 niquests 客户端的 auth 属性

        Args:
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType | None): 身份验证类
        """
        self.client.auth = auth
        logger.info(f"{self.__class__.__name__} auth set to: {auth}")

    @property
    def base_url(self):
        """
        发送相对 URL 请求时使用的基础 URL
        返回底层 niquests 客户端的 base_url 属性
        """
        return self.client.base_url

    @base_url.setter
    def base_url(self, url: str):
        """
        设置发送相对 URL 请求时使用的基础 URL
        将传递给底层 niquests 客户端的 base_url 属性

        Args:
            url (str): 基础 URL
        """
        self.client.base_url = url
        logger.info(f"{self.__class__.__name__} base url set to: {url}")

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Constructs a Request <Request>, prepares it and sends it. Returns Response <Response> object.

        Args:
            method (str): Method for the new Request object.
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. This does not override niquests' inner Retry status_forcelist. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        parsed_url = urlparse(url)

        if headers is None:
            headers = {}
        if accept_encoding:
            headers.update({"Accept-Encoding": accept_encoding})
        if referer:
            headers.update({"Referer": referer})

        #!Fix httpx issue [当 URL 包含请求参数且设置了 params 参数时，URL 中的请求参数会意外消失](https://github.com/encode/httpx/issues/3621)
        #!这里保留该操作仅为为了兼容 httpx
        if params is None:
            params = {}
        else:
            params = (
                parse_qs(parsed_url.query) | dict(params)
            )  # 获取 URL 中的请求参数，并将其与 params 参数合并
        #!requests/httpx 无法*正确处理* dict 类型的请求参数，需要将其转换为 JSON 字符串
        for key, value in params.items():
            if isinstance(value, dict):
                params[key] = orjson.dumps(value).decode("utf-8")

        async def select_request_proxies(
            value: ProxiesType | None | UnsetType,
        ) -> tuple[dict[str, str], str | None, str | None]:
            """Select request-level proxies and return niquests proxies plus tracking metadata."""
            # UNSET: 未传入，继承 Booru 实例配置；若实例配置是 tuple，同样会走下方
            #        tuple 候选流程，跳过 cooldown 中的代理后再现挑一个。
            # None : 显式禁用，request-level no_proxy="*" 压过 env，且避免 niquests 空代理 URL 触发 KeyError
            # 其他 : 显式覆盖；若是 tuple，也会跳过正在 cooldown 的 proxy 后再现挑。
            if isinstance(value, UnsetType):
                value = self._proxies or {}
            elif value is None:
                value = {"no_proxy": "*"}

            if isinstance(value, tuple):
                # tuple 中的候选可以是 str 或 dict；每个候选只做一次 normalize + resolve，
                # 同时得到 raw key 与 redacted log，避免 key/log 两条路径重复 select_proxy。
                candidates = list(value)
                if not candidates:
                    return {}, None, None

                def resolve_candidate(candidate: ProxyType):
                    """Normalize and resolve one proxy candidate exactly once."""
                    normalized = normalize_proxy(candidate)
                    return (
                        normalized,
                        resolve_proxy(url, normalized, self.client.base_url),
                    )

                resolved_candidates = [
                    resolve_candidate(candidate)
                    for candidate in candidates
                ]

                while True:
                    available = []
                    unavailable_keys = []
                    for candidate, proxy_resolution in resolved_candidates:
                        if self._proxy_cooldown.is_available(proxy_resolution.key):
                            available.append((candidate, proxy_resolution))
                        else:
                            remaining = self._proxy_cooldown.remaining(
                                proxy_resolution.key or ""
                            )
                            logger.debug(
                                f"proxy.skip proxy={proxy_resolution.log} reason=cooldown "
                                f"remaining={format_elapsed(remaining)}"
                            )
                            if proxy_resolution.key is not None:
                                unavailable_keys.append(proxy_resolution.key)

                    if available:
                        selected, proxy_resolution = random.choice(available)
                        return selected, proxy_resolution.key, proxy_resolution.log

                    wait_seconds = self._proxy_cooldown.next_available_in(unavailable_keys)
                    logger.warning(
                        "All proxies are cooling down; waiting "
                        f"{format_elapsed(wait_seconds)} before retrying proxy selection."
                    )
                    await asyncio.sleep(wait_seconds)

            # 单个 str/dict 没有可替代候选；仍然先归一化，再按 raw key 等待 cooldown 结束。
            selected = normalize_proxy(cast(dict[str, str] | str, value))
            proxy_resolution = resolve_proxy(url, selected, self.client.base_url)
            while not self._proxy_cooldown.is_available(proxy_resolution.key):
                remaining = self._proxy_cooldown.remaining(proxy_resolution.key or "")
                logger.warning(
                    f"Proxy {proxy_resolution.log} is cooling down; waiting "
                    f"{format_elapsed(remaining)} before retrying proxy selection."
                )
                await asyncio.sleep(remaining)

            return selected, proxy_resolution.key, proxy_resolution.log

        # 两态级联：未传则继承 Booru 实例配置，仍未配置则回落到单次尝试
        if max_attempt_number is None:
            max_attempt_number = self._max_attempt_number
        if max_attempt_number is None:
            max_attempt_number = 1
        max_attempt_number = max(max_attempt_number, 1)

        # 有些站点会把业务状态编码到非 2xx/429 状态码里；命中时不触发 Booru 外层 status retry。
        expected_status_codes = set(expected_statuses or ())

        proxy_key: str | None = None
        proxy_log: str | None = None

        def record_proxy_outcome(*, failed: bool) -> None:
            """Record the selected proxy outcome and emit a cooldown warning when needed."""
            # proxy_key 是未脱敏的内部身份，proxy_log 是脱敏后的日志值；两者不能混用。
            cooled_down = self._proxy_cooldown.record(proxy_key, failed=failed)
            if cooled_down:
                logger.warning(
                    f"proxy.cooldown proxy={proxy_log} "
                    f"failures={self._proxy_cooldown.threshold} "
                    f"cooldown={format_elapsed(self._proxy_cooldown.cooldown)}"
                )

        def format_request_retry_log(retry_state) -> str:
            """Format the outer tenacity retry log with request and proxy context."""
            if retry_state.outcome is None:
                raise RuntimeError("format_request_retry_log() called before outcome was set")
            if retry_state.next_action is None:
                raise RuntimeError(
                    "format_request_retry_log() called before next_action was set"
                )

            # before_sleep 既可能收到异常，也可能收到 retry predicate 触发的返回值。
            # 当前 Booru 外层 retry 只配置 exception retry，但这里保留上游 before_sleep_log 的完整分支。
            if retry_state.outcome.failed:
                exc = retry_state.outcome.exception()
                reason = f"{exc.__class__.__name__}: {exc}"
            else:
                reason = f"returned {retry_state.outcome.result()}"

            return format_retry_log(
                method=method,
                url=url,
                proxy_log=proxy_log,
                next_attempt=retry_state.attempt_number + 1,
                max_attempt_number=max_attempt_number,
                sleep_seconds=retry_state.next_action.sleep,
                reason=reason,
            )

        # niquests 的 Retry 仍然负责 HTTP/transport-level retry。外层 tenacity 只兜底旧版 niquests
        # 可能抛出的 Python-level exception，避免这些异常直接打断批量请求流程。
        async for attempt in AsyncRetrying(
            sleep=asyncio.sleep,
            stop=stop_after_attempt(max_attempt_number),
            wait=wait_exponential_jitter(initial=1, max=10, jitter=3),
            retry=retry_if_exception_type(Exception),
            before=before_log(logger, logging.DEBUG),
            after=after_log(logger, logging.DEBUG),
            before_sleep=before_sleep_log(
                logger,
                logging.WARNING,
                formatter=format_request_retry_log,
            ),
            reraise=True,
        ):
            with attempt:
                selected_proxies, proxy_key, proxy_log = await select_request_proxies(proxies)
                start_time = time.perf_counter()
                try:
                    response: Response | AsyncResponse = await self.client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        data=data,
                        cookies=cookies,
                        files=files,
                        auth=auth,
                        timeout=timeout,
                        allow_redirects=allow_redirects,
                        proxies=selected_proxies,
                        hooks=hooks,
                        stream=stream,
                        verify=verify,
                        cert=cert,
                        json=json,
                    )
                    await self.client.gather(response)
                except Exception:
                    record_proxy_outcome(failed=True)
                    raise
                elapsed = time.perf_counter() - start_time

                status_code = getattr(response, "status_code", None)
                is_expected_status = status_code in expected_status_codes
                failed_status = (
                    isinstance(status_code, int)
                    and status_code in self._proxy_cooldown_statuses
                    and not is_expected_status
                )
                record_proxy_outcome(failed=failed_status)

                # 统一为 sync Response：
                # - await 一次 .content 把 body 读进 _content 缓存，再把 __class__ 降回 Response，调用方访问 .text / .content 就不必 await
                if isinstance(response, AsyncResponse):
                    await response.content
                    response.__class__ = Response

                response: Response = cast(Response, response)
                content = getattr(response, "_content", None)
                if content is None:
                    content = getattr(response, "content", None)
                body_size = get_body_size(content)
                redirects = len(getattr(response, "history", []) or [])

                logger.info(
                    " ".join(
                        [
                            f'{response.request.method} {response.request.url} "{repr(response).replace("Response ", "")} {response.reason}"',
                            f"via {proxy_log}" if proxy_log else "",
                            format_response_metrics(
                                attempt_number=attempt.retry_state.attempt_number,
                                max_attempt_number=max_attempt_number,
                                elapsed=elapsed,
                                body_size=body_size,
                                redirects=redirects,
                                expected_statuses=(
                                    expected_status_codes if is_expected_status else None
                                ),
                            ),
                        ]
                    ).strip(),
                )

                return response

    async def get(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a GET request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "GET",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def options(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a OPTIONS request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "OPTIONS",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def head(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a HEAD request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "HEAD",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def post(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a POST request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "POST",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def put(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a PUT request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "PUT",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def patch(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a PATCH request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "PATCH",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def delete(
        self,
        url: str,
        *,
        headers: HeadersType | None = None,
        params: QueryParameterType | None = None,
        data: BodyType | AsyncBodyType | None = None,
        cookies: CookiesType | None = None,
        files: MultiPartFilesType | MultiPartFilesAltType | None = None,
        auth: HttpAuthenticationType | AsyncHttpAuthenticationType | None = None,
        timeout: TimeoutType | None = None,
        allow_redirects: bool = True,
        proxies: ProxiesType | None | UnsetType = UNSET,
        max_attempt_number: int | None = None,
        expected_statuses: Collection[int] | None = None,
        hooks: AsyncHookType[PreparedRequest | Response] | None = None,
        stream: bool | None = None,
        verify: TLSVerifyType | None = None,
        cert: TLSClientCertType | None = None,
        json: Any | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
    ) -> Response:
        """
        Sends a DELETE request. Returns Response object.

        Args:
            url (str): URL for the new Request object.
            headers (HeadersType, optional): Dictionary of HTTP Headers to send with the Request. Defaults to None.
            params (QueryParameterType, optional): Mapping of querystring data to send with the Request. Values may be strings, bytes, numbers, booleans, None, or lists/tuples of those scalar values for multivalued parameters. Numeric and boolean scalar values are encoded by niquests as strings; nested dict values are compactly JSON-serialized by WaifuBoard before the request is prepared. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If the effective value is a tuple, whether inherited from the Booru instance or explicitly passed on this request, one available candidate is selected after skipping candidates that are cooling down. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of attempts to make. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that should be returned as expected business states after niquests transport retry has returned. Defaults to None.
            hooks (AsyncHookType[PreparedRequest | Response], optional): Dictionary mapping hook name to one event or list of events, event must be callable. Defaults to None.
            stream (bool, optional): Whether to immediately download the response content. Defaults to False. Defaults to None.
            verify (TLSVerifyType, optional): Either a boolean, in which case it controls whether we verify the server's TLS certificate, or a path passed as a string or os.Pathlike object, in which case it must be a path to a CA bundle to use. Defaults to True. When set to False, requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks. Setting verify to False may be useful during local development or testing. It is also possible to put the certificates (directly) in a string or bytes. Defaults to None.
            cert (TLSClientCertType, optional): If String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair, or ('cert', 'key', 'key_password'). Defaults to None.
            json (Any, optional): JSON to send in the body of the Request. Defaults to None.
            accept_encoding (str, optional): A shortcut for setting the Accept-Encoding field in the request headers. Defaults to None.
            referer (str, optional): A shortcut for setting the Referer field in the request headers. Defaults to None.

        Returns:
            Response: Response object.
        """
        return await self.request(
            "DELETE",
            url,
            headers=headers,
            params=params,
            data=data,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            max_attempt_number=max_attempt_number,
            expected_statuses=expected_statuses,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
            accept_encoding=accept_encoding,
            referer=referer,
        )

    async def stream_process_tasks(
        self,
        tasks: list[Coroutine],
    ) -> AsyncIterable[Any]:
        for t in asyncio.as_completed(tasks):
            try:
                result = await t
                yield result
            except Exception as exc:
                logger.error(f"{exc.__class__.__name__}: {exc}")
                yield None

    async def batch_process_tasks(
        self,
        tasks: list[Coroutine],
    ) -> list[Any]:
        results: list = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"{res.__class__.__name__}: {res}")
                results[i] = None
        return results

    async def download_file(
        self,
        url: str,
        filepath: str,
    ) -> tuple[str, str] | None:
        """
        下载单个文件到指定路径

        Args:
            url (str): 文件 URL
            filepath (str): 文件存储路径

        Returns:
            tuple[str, str] | None. 若下载成功，则返回对应的 (url, filepath)；若下载失败，则返回 None
        """
        try:
            # 下载文件
            response = await self.get(url)
            # 保存文件
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(response.content)
            return (url, filepath)
        except RequestException as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")
            return None

    async def concurrent_download_file(
        self,
        urls: pd.Series,
        directory: str,
        extract_pattern: Callable[[str], str] = os.path.basename,
    ) -> AsyncIterable[tuple[str, str] | None]:
        """
        并发下载文件到指定目录，忽略已存在的文件
        文件名默认为 urls 中 url 的基础名称（即 url 的最后一个组件），也可以传递可调用对象给 extract_pattern 参数，以指定从 url 中提取文件名的规则

        Args:
            urls (pd.Series): 文件 URLs
            directory (str): 文件存储目录
            extract_pattern (Callable[[str], str], optional): 可调用对象，指定从 url 中提取文件名的规则. Defaults to os.path.basename.

        Yields:
            tuple[str, str] | None. 若下载成功，则返回对应的 (url, filepath)；若下载失败，则返回 None
        """
        # 预处理 urls 中的空值
        urls = urls.dropna(axis=0, inplace=False, ignore_index=False)
        # 创建目录
        if not await aioos.path.exists(directory):
            await aioos.makedirs(directory)
        # 若存在已有文件，则将其过滤
        else:
            # 获取已有文件列表
            files = await aioos.listdir(directory)
            # 批 URLs 大小
            patch_size = urls.size
            # 过滤已有文件
            urls = urls[~urls.apply(lambda x: extract_pattern(x) in files)]
            # 已过滤文件数量
            filter_size = patch_size - urls.size
            if filter_size > 0:
                logger.info(
                    f"Filtered {filter_size} existing files from {patch_size} URLs"
                )
        # 检查 URLs 是否为空
        if urls.empty:
            return
        # 创建异步任务列表
        tasks = [
            self.download_file(
                url=url,
                filepath=os.path.join(
                    directory,
                    extract_pattern(url),
                ),
            )
            for url in urls
        ]
        # 并发执行下载任务
        async for res in self.stream_process_tasks(tasks):
            yield res

    async def save_raws(
        self,
        raws: pd.DataFrame,
        directory: str,
        filename: str,
        overwrite: bool = False,
    ) -> tuple[pd.DataFrame, str, str] | None:
        """
        保存单个元数据到指定路径

        Args:
            raws (pd.DataFrame): 元数据内容
            directory (str): 文件存储目录
            filename (str): 文件名
            overwrite (bool, optional): 是否覆盖已有同名文件. Defaults to False.

        Returns:
            tuple[pd.DataFrame, str, str]. 若保存成功，则返回对应的 (raws, directory, filename)；若保存失败，则返回 None
        """
        # 创建目录
        if not await aioos.path.exists(directory):
            await aioos.makedirs(directory)
        # 若存在已有文件，则根据 overwrite 参数决定是否覆盖
        else:
            if not overwrite:
                # 获取已有文件列表
                files = await aioos.listdir(directory)
                if filename in files:
                    logger.warning(f"File {filename} already exists in {directory}")
                    return None

        filepath = os.path.join(directory, filename)
        try:
            # 保存文件
            async with aiofiles.open(filepath, "w") as f:
                await f.write(
                    raws.to_json(
                        orient="records",
                        indent=4,
                        lines=False,
                        mode="w",
                    )
                )
            return (raws, directory, filename)
        except OSError as exc:
            logger.error(f"{exc.__class__.__name__} for {filepath} - {exc}")
            return None

    async def save_tags(
        self,
        tag: str,
        directory: str,
        filename: str,
        overwrite: bool = False,
        callback: Callable[[str], str] = lambda x: x.replace(" ", ", ").replace(
            "_", " "
        ),
    ) -> tuple[str, str, str] | None:
        """
        保存单个标签到指定路径

        Args:
            tag (str): 标签内容
            directory (str): 文件存储目录
            filename (str): 文件名
            overwrite (bool, optional): 是否覆盖已有同名文件. Defaults to False.
            callback (Callable[[str], str], optional): 可调用对象，用于后处理标签内容. Defaults to lambda x: x.replace(' ', ', ').replace('_', ' ').

        Returns:
            tuple[str, str, str]. 若保存成功，则返回对应的 (tags, directory, filename)；若保存失败，则返回 None
        """
        # 创建目录
        if not await aioos.path.exists(directory):
            await aioos.makedirs(directory)
        # 若存在已有文件，则根据 overwrite 参数决定是否覆盖
        else:
            if not overwrite:
                # 获取已有文件列表
                files = await aioos.listdir(directory)
                if filename in files:
                    logger.warning(f"File {filename} already exists in {directory}")
                    return None

        filepath = os.path.join(directory, filename)
        try:
            # 处理标签内容
            if callback:
                tag = callback(tag)
            # 保存文件
            async with aiofiles.open(filepath, "w") as f:
                await f.write(tag)
            return (tag, directory, filename)
        except OSError as exc:
            logger.error(f"{exc.__class__.__name__} for {filepath} - {exc}")
            return None

    async def fetch_page(
        self,
        api: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        callback: Callable[[Any], Any] | None = None,
        **kwargs,
    ) -> list[dict]:
        """
        获取某一页帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict, optional): 请求头. Defaults to None.
            params (dict, optional): 请求参数. Defaults to None.
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 niquests.AsyncSession.request 的其它关键字参数

        Returns:
            list[dict] | None. 若获取成功，则返回对应的帖子内容列表；若获取失败，则返回 None
        """
        try:
            # 获取帖子内容
            response = await self.get(api, headers=headers, params=params, **kwargs)
            content = response.json()
            # 处理回调
            if callback:
                content = callback(content)
            if isinstance(content, list):  # 多个帖子
                return content
            else:  # 单个帖子
                return [content]
        except RequestException as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")
            return []

    async def concurrent_fetch_page(
        self,
        api: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        start_page: int,
        end_page: int,
        page_key: str,
        callback: Callable[[Any], Any] | None = None,
        **kwargs,
    ) -> AsyncIterable[list[dict] | None]:
        """
        并发获取多个页面的帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict, optional): 请求头. Defaults to None.
            params (dict, optional): 请求参数. Defaults to None.
            start_page (int): 查询起始页码
            end_page (int): 查询结束页码
            page_key (str): 页码参数的名称，用于在传递的 params 参数中设置页码
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 niquests.AsyncSession.request 的其它关键字参数

        Yields:
            list[dict] | None. 若获取成功，则返回对应的帖子内容列表；若获取失败，则返回 None
        """
        if headers is None:
            headers = {}
        if params is None:
            params = {}
        # 创建异步任务列表
        tasks = []
        # 获取指定页码的帖子列表
        for page in range(start_page, end_page + 1):
            params.update({page_key: page})
            tasks.append(
                self.fetch_page(
                    api,
                    headers=headers,
                    params=params.copy(),
                    callback=callback,
                    **kwargs,
                )
            )
        # 并发执行下载任务
        async for res in self.stream_process_tasks(tasks):
            yield res

    @staticmethod
    def parse_url(
        url: str,
        *,
        extract_pattern: Callable[[str], str] = os.path.basename,
        remove_invalid_characters: bool = True,
    ) -> str:
        """
        从 url 中提取文件名，并将其转换为用户可读的规范化名称

        Args:
            url (str): 文件 URL
            extract_pattern (Callable[[str], str], optional): 可调用对象，指定从 url 中提取文件名的规则. Defaults to os.path.basename.
            remove_invalid_characters (bool, optional): 是否移除文件名中无效的路径字符. Defaults to True.

        Returns:
            str: 用户可读的规范化名称

        Example:
            Yande.re 平台：

            帖子链接：https://yande.re/post/show/1023280
            帖子标签：horiguchi_yukiko k-on! akiyama_mio hirasawa_yui kotobuki_tsumugi nakano_azusa tainaka_ritsu cleavage disc_cover dress summer_dress screening
            帖子下载链接：https://files.yande.re/image/c0abd1a95b5e9f9ed845e24ffb0f663d/yande.re%201023280%20akiyama_mio%20cleavage%20disc_cover%20dress%20hirasawa_yui%20horiguchi_yukiko%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20screening%20summer_dress%20tainaka_ritsu.jpg

            处理过程：
            - 获取帖子下载链接的基础名称（即帖子下载链接的最后一个组件）：yande.re%201023280%20akiyama_mio%20cleavage%20disc_cover%20dress%20hirasawa_yui%20horiguchi_yukiko%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20screening%20summer_dress%20tainaka_ritsu.jpg
            - 解码经过 url 编码后的基础名称：yande.re 1023280 akiyama_mio cleavage disc_cover dress hirasawa_yui horiguchi_yukiko k-on! kotobuki_tsumugi nakano_azusa screening summer_dress tainaka_ritsu.jpg，由此可见 yandere 文件命名规则为：yande.re {帖子 ID} {按照 a-z 排序后的标签}.文件后缀名

        Note:
            若 remove_invalid_characters 为 False，则永远不要使用该方法返回的规范化名称作为存储文件的文件名，因为解码经过 url 编码后的基础名称中，可能包含非法字符（在按照 a-z 排序后的标签中，可能包含 ： < > : " / \\ | ? * 等 Windows 系统中的非法字符，从而引发 OSError: [WinError 123] 文件名、目录名或卷标语法不正确）
        """
        # 提取帖子下载链接的文件名
        filename = extract_pattern(url)
        # 解码 url 编码后的文件名
        filename = unquote(filename)
        # 移除文件名中无效的路径字符
        if remove_invalid_characters:
            filename = normalize_filepath(filename)
        return filename


class BooruComponent:
    """
    Base Booru Image Board Component
    """

    def __init__(self, client: Booru):
        # 当前客户端平台主体
        self.client = client
        # 当前客户端平台标识
        self.platform = self.client.__class__.__name__
        # 当前调用组件的功能标识
        self.type = self.__class__.__name__
        # 当前调用组件的存储文件根目录
        self.directory = os.path.join(self.client.directory, self.platform, self.type)
