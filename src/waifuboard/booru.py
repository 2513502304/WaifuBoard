"""
Booru Image Board API implementation.
"""

import asyncio
import logging
import os
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
from niquests.exceptions import JSONDecodeError, RequestException
from tenacity import AsyncRetrying, RetryCallState, RetryError, TryAgain, retry
from tenacity.after import after_log
from tenacity.before import before_log
# from tenacity.before_sleep import before_sleep_log
from tenacity.nap import sleep
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential_jitter
from urllib3.util.retry import Retry
from urllib3.util.timeout import Timeout

from .utils import (
    PreparedProxyPool,
    ProxyCooldownTracker,
    ProxySelection,
    logger,
    format_response_metrics,
    format_retry_log,
    format_elapsed,
    get_body_size,
    is_immutable_proxy_pool,
    before_sleep_log,
    normalize_filepath,
    prepare_proxy_pool,
    resolve_outcome_proxy,
)
from .typing import DownloadItem, DownloadResult, PageResult

# niquests intentionally keeps its public typing narrower than some runtime-accepted
# values; keep WaifuBoard's wrapper types explicit when we rely on that behavior.
# Reference: https://github.com/jawah/niquests/pull/399
BodyFormValueType: TypeAlias = str | bytes | int | float | bool | None
BodyFormType: TypeAlias = (
    list[
        tuple[
            str,
            BodyFormValueType | list[BodyFormValueType] | tuple[BodyFormValueType, ...],
        ]
    ]
    | dict[
        str, BodyFormValueType | list[BodyFormValueType] | tuple[BodyFormValueType, ...]
    ]
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


DEFAULT_PROXY_COOLDOWN_STATUSES = frozenset({429, 502, 503, 504})


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
        proxy_cooldown_seconds: int | float = 600,
        proxy_cooldown_statuses: (
            Collection[int] | None
        ) = DEFAULT_PROXY_COOLDOWN_STATUSES,
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
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and host to the URL of the proxy (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to be used on each Request <Request>. If a single string is provided, it will be used for both http and https. A tuple acts as a proxy pool: each outer attempt uses an available candidate that has not yet been tried by the current request, cycling only after all available candidates have been used. When not provided and trust_env is True, the process environment's proxy settings are captured as the default, giving an effective priority of request > session > env. Defaults to None.
            trust_env (bool, optional): Trust environment settings for proxy configuration, default authentication and similar. Defaults to True.
            max_redirects (int, optional): Maximum number of redirects allowed. If the request exceeds this limit, a TooManyRedirects exception is raised. This defaults to requests.models.DEFAULT_REDIRECT_LIMIT, which is 30. Defaults to 30.
            retries (RetryType, optional): Configure a number of times a request must be automatically retried before giving up. Defaults to 3.
            max_attempt_number (int, optional): Default outer retry budget (tenacity-level) for request methods. Used when a request method does not pass its own max_attempt_number. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If both this and the request-level value are None, the underlying call falls back to a single attempt. Defaults to 3.
            proxy_cooldown_threshold (int, optional): Consecutive per-proxy failures before temporarily cooling down that proxy. Set to None to disable proxy cooldown. Defaults to None.
            proxy_cooldown_seconds (int | float, optional): Seconds a failed proxy stays unavailable after reaching proxy_cooldown_threshold. When every configured proxy is cooling, proxy selection waits until the earliest cooldown expires before starting the HTTP request; this wait is outside the HTTP timeout and does not consume a tenacity attempt. Defaults to 600.
            proxy_cooldown_statuses (Collection[int], optional): HTTP statuses that count as proxy failures when proxy cooldown is enabled. This is a proxy/IP health policy, not niquests' HTTP retry policy: 429 can indicate IP-level throttling, while 502/503/504 often indicate gateway or proxy-path failures. 413 is intentionally excluded because a too-large request is not a proxy health signal. Set to None to disable HTTP-status-based proxy cooldown; transport exceptions can still trigger cooldown when proxy_cooldown_threshold is set. Defaults to (429, 502, 503, 504).
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
                # 这是 HTTP retry 策略：urllib3/niquests 把 413/429/503 视为 Retry-After 相关状态码；它和 proxy cooldown 的健康判断不是同一层语义
                retries = Retry(
                    total=retries,
                    redirect=True,
                    allowed_methods=frozenset(
                        ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"]
                    ),
                    status_forcelist=Retry.RETRY_AFTER_STATUS_CODES,
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
        # 实例级代理配置通常会被每个 request 复用；初始化时建立不可变 prepared pool，避免高频请求重复 normalize 整个代理池
        # selector 的已尝试索引不保存在 prepared pool 中，每个逻辑请求仍会创建独立状态，因此并发请求不会互相消耗代理候选
        self._prepared_proxy_pool = prepare_proxy_pool(proxies or {})
        # str 与 tuple[str] 不可能原地变化，可在 request 热路径直接 O(1) 复用；dict 或 tuple[dict] 仍需按内容重新取 cache，以保留调用方构造后修改 mapping 的既有行为
        self._proxy_config_is_immutable = is_immutable_proxy_pool(proxies)
        # 大型 request override 即使命中全局 prepared LRU，也需要 O(pool_size) 生成内容 key；保留最近一次不可变对象可让同一调用点连续复用时按 identity O(1) 命中
        # 这里只缓存单项以控制状态复杂度；不同但内容相等的对象仍由全局内容缓存复用，可变 dict/tuple[dict] 则绝不走 identity 快路径
        self._request_proxy_pool_cache: (
            tuple[ProxiesType, PreparedProxyPool] | None
        ) = None
        self._max_attempt_number: int | None = max_attempt_number
        self._proxy_cooldown = ProxyCooldownTracker(
            threshold=proxy_cooldown_threshold,
            cooldown_seconds=proxy_cooldown_seconds,
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

    def _record_proxy_outcome(
        self,
        selection: ProxySelection,
        *,
        failed: bool,
    ) -> None:
        """Record one proxy outcome and log when it starts a cooldown window.

        Args:
            selection (ProxySelection): Proxy metadata associated with the completed attempt.
            failed (bool): Whether the attempt counts as a proxy-health failure.

        Returns:
            None: The shared cooldown tracker and logger are updated in place.
        """
        # selection.key 保留凭据用于区分代理身份，selection.log 只用于日志；任何日志调用都不能使用未脱敏 key
        cooled_down = self._proxy_cooldown.record(selection.key, failed=failed)
        if cooled_down:
            logger.warning(
                f"proxy.cooldown proxy={selection.log} "
                f"failures={self._proxy_cooldown.threshold} "
                f"cooldown={format_elapsed(self._proxy_cooldown.cooldown_seconds)}"
            )

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
            proxies (ProxiesType, UnsetType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. A tuple acts as a proxy pool: each outer attempt skips cooling candidates and prefers one not yet tried by the current request. A selected string proxy is normalized to the dict shape required by niquests. If left as UNSET, falls back to the proxies configured on the Booru instance. Pass None to explicitly bypass any proxy for this request. Defaults to UNSET.
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. Each outer attempt starts only after the current niquests retry sequence returns an unexpected HTTP error response or raises an exception, and a proxy pool is reselected between attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
        if accept_encoding or referer:
            # request shortcut 需要修改 headers 时先复制，既避免污染调用方在并发请求间复用的 mapping，也避免没有 shortcut 时创建无用空 dict
            headers = dict(headers or {})
            if accept_encoding:
                headers["Accept-Encoding"] = accept_encoding
            if referer:
                headers["Referer"] = referer

        #!Fix httpx issue [当 URL 包含请求参数且设置了 params 参数时，URL 中的请求参数会意外消失](https://github.com/encode/httpx/issues/3621)
        #!这里保留该操作仅为为了兼容 httpx
        if params is None:
            params = {}
        else:
            # 只有显式 params 才需要解析 URL query；普通下载请求保留完整 URL 交给 niquests，跳过无用 urlparse
            parsed_url = urlparse(url)
            params = parse_qs(parsed_url.query) | dict(
                params
            )  # 获取 URL 中的请求参数，并将其与 params 参数合并
        #!requests/httpx 无法*正确处理* dict 类型的请求参数，需要将其转换为 JSON 字符串
        for key, value in params.items():
            if isinstance(value, dict):
                params[key] = orjson.dumps(value).decode("utf-8")

        # UNSET: 未传入，直接复用 Booru 初始化时预构建的代理池；若实例配置是 tuple，每个 request 仍有独立 selector 跳过 cooldown 并轮换未尝试候选
        # None : 显式禁用，request-level no_proxy="*" 压过 env，且避免 niquests 空代理 URL 触发 KeyError
        # 其他 : 显式覆盖；根据配置内容命中有界 prepared-pool cache，若是 tuple，同样跳过正在 cooldown 的 proxy 后再现挑
        if isinstance(proxies, UnsetType):
            if self._proxy_config_is_immutable:
                prepared_proxy_pool = self._prepared_proxy_pool
            else:
                # 可变全局 mapping 每次按当前内容查询 LRU；未变化时只产生轻量 hash key，变化时则得到新的 prepared snapshot，避免长期使用陈旧代理
                prepared_proxy_pool = prepare_proxy_pool(self._proxies or {})
        elif proxies is None:
            # niquests.utils 中的 select_proxy 对该值返回 None 并赋值给实际使用的 proxy，因此将会走 proxy is None 的分支，不会进入 self.proxy_manager[proxy] 筛选 proxy 的分支
            prepared_proxy_pool = prepare_proxy_pool({"no_proxy": "*"})
        else:
            request_proxy_pool_cache = self._request_proxy_pool_cache
            if (
                request_proxy_pool_cache is not None
                and request_proxy_pool_cache[0] is proxies
            ):
                # 同一不可变对象连续使用时直接复用 prepared pool；identity 判断不会把内容相等但生命周期不同的对象错误当成同一配置
                prepared_proxy_pool = request_proxy_pool_cache[1]
            else:
                prepared_proxy_pool = prepare_proxy_pool(proxies)
                if is_immutable_proxy_pool(proxies):
                    # 赋值发生在第一个 await 之前，异步并发只能覆盖“最近一项”而不会让当前请求拿到另一配置对应的 pool；局部 prepared_proxy_pool 已经固定
                    self._request_proxy_pool_cache = (proxies, prepared_proxy_pool)

        # 一次逻辑请求开始后固定使用同一 trust_env 快照，使初始代理解析与 redirect outcome 遵循同一规则；底层 session 配置不应在请求执行期间并发修改
        trust_env = bool(getattr(self.client, "trust_env", False))
        # prepared pool 只共享不可变代理配置与 URL route 解析结果；request-local selector 每次新建，确保外层 retry 的“不放回”状态不会跨并发请求传播
        proxy_selector = prepared_proxy_pool.selector(
            url=url,
            tracker=self._proxy_cooldown,
            base_url=self.client.base_url,
            trust_env=trust_env,
        )

        # 两态级联：未传则继承 Booru 实例配置，仍未配置则回落到单次尝试
        if max_attempt_number is None:
            max_attempt_number = self._max_attempt_number
        if max_attempt_number is None:
            max_attempt_number = 1
        max_attempt_number = max(max_attempt_number, 1)

        # expected_statuses 只用于“响应本身就是终态业务结果”的状态码；niquests 返回命中响应后，Booru 不再进行外层 retry、代理轮换或代理健康失败计数
        # 若站点把 404 等错误码临时用作限流信号，则不能列入这里，否则会把本可通过后续 attempt 恢复的失败误判为终态并提前返回；需要影响代理健康时还应把该状态加入实例级 proxy_cooldown_statuses
        expected_status_codes = set(expected_statuses or ())

        # before_sleep formatter 通过闭包读取刚失败 attempt 的代理；首次 select 前先放入空值，避免 callback 闭包捕获一个尚未绑定的局部变量
        proxy_selection = ProxySelection(proxies={}, key=None, log=None)

        def format_request_retry_log(retry_state) -> str:
            """Format the outer tenacity retry log with request and proxy context.

            Args:
                retry_state (RetryCallState): Tenacity state for the failed attempt and upcoming sleep.

            Returns:
                str: Retry message containing request, proxy, progress, sleep, and failure context.
            """
            if retry_state.outcome is None:
                raise RuntimeError(
                    "format_request_retry_log() called before outcome was set"
                )
            if retry_state.next_action is None:
                raise RuntimeError(
                    "format_request_retry_log() called before next_action was set"
                )

            # before_sleep 既可能收到异常，也可能收到 retry predicate 触发的返回值；当前实现通过 raise_for_status 把 unexpected HTTP status 转为异常，但仍保留上游完整分支以免未来 predicate 改为 result-based 后日志失真
            if retry_state.outcome.failed:
                exc = retry_state.outcome.exception()
                reason = f"{exc.__class__.__name__}: {exc}"
            else:
                reason = f"returned {retry_state.outcome.result()}"

            return format_retry_log(
                method=method,
                url=url,
                proxy_log=proxy_selection.log,
                next_attempt=retry_state.attempt_number + 1,
                max_attempt_number=max_attempt_number,
                sleep_seconds=retry_state.next_action.sleep,
                reason=reason,
            )

        # niquests 内层 Retry 先在当前选中代理上处理 status_forcelist 与 transport retry；只有它返回 unexpected error response 或最终抛出 Python exception 后，tenacity 外层才开始下一次 attempt 并让 selector 换代理
        # 双层 retry 的边界不能合并：niquests 内层固定使用同一个 outer attempt 已选中的代理，tenacity 外层既兜住底层泄漏的 Python-level exception，也负责在 unexpected HTTP error 后重新选择代理
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
                # 每轮外层 attempt 重新选择代理；代理池 selector 会优先选择本次 request 尚未尝试过且不在 cooldown 的候选
                proxy_selection = await proxy_selector.select()
                should_log_response = logger.isEnabledFor(logging.INFO)
                start_time = time.perf_counter() if should_log_response else None
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
                        proxies=proxy_selection.proxies,
                        hooks=hooks,
                        stream=stream,
                        verify=verify,
                        cert=cert,
                        json=json,
                    )
                    await self.client.gather(response)
                except Exception as exc:
                    failed_request_url = getattr(
                        getattr(exc, "request", None),
                        "url",
                        None,
                    )
                    if (
                        isinstance(failed_request_url, str)
                        and failed_request_url != url
                    ):
                        # redirect 后的 transport exception 关联最终 PreparedRequest；按该 URL 重算 identity，避免把失败记到初始 host 使用的健康代理
                        proxy_selection = resolve_outcome_proxy(
                            proxy_selection,
                            failed_request_url,
                            trust_env=trust_env,
                        )
                    # transport、adapter、gather 或 hook 抛出的异常都表示当前代理未完成请求；先记入健康状态，再交给 tenacity 决定是否还有外层预算
                    self._record_proxy_outcome(proxy_selection, failed=True)
                    raise

                status_code = getattr(response, "status_code", None)
                final_request_url = getattr(
                    getattr(response, "request", None),
                    "url",
                    None,
                )
                if (
                    isinstance(final_request_url, str)
                    and (
                        final_request_url != url
                        or bool(getattr(response, "history", None))
                    )
                ):
                    # niquests 会在 redirect 时按新 host/scheme 重新选择 proxy；状态、日志与 cooldown 必须归属最终响应实际使用的 route，而不是初始 URL 的 route
                    proxy_selection = resolve_outcome_proxy(
                        proxy_selection,
                        final_request_url,
                        trust_env=trust_env,
                    )
                is_expected_status = status_code in expected_status_codes
                failed_status = (
                    isinstance(status_code, int)
                    and status_code in self._proxy_cooldown_statuses
                    and not is_expected_status
                )
                # 命中 expected_statuses 表示调用方接受该响应作为终态业务结果，不应污染代理健康；其他状态只有显式列入 cooldown policy 才累计失败
                self._record_proxy_outcome(proxy_selection, failed=failed_status)

                # 非最后一次 attempt 对 unexpected 4xx/5xx 调用 raise_for_status，使 niquests status_forcelist 之外的错误也能消耗外层 retry 并更换代理；最后一次则返回响应供调用方自行检查，不在封装层强制抛异常
                if (
                    attempt.retry_state.attempt_number < max_attempt_number
                    and not is_expected_status
                ):
                    response.raise_for_status()

                # 统一为 sync Response：
                # - await 一次 .content 把 body 读进 _content 缓存，再把 __class__ 降回 Response，调用方访问 .text / .content 就不必 await
                if isinstance(response, AsyncResponse):
                    await response.content
                    response.__class__ = Response

                response: Response = cast(Response, response)

                # INFO 被关闭时跳过 repr、history、body size 与格式化，避免批量下载在热路径为不可见日志支付额外成本
                if should_log_response:
                    assert start_time is not None
                    content = getattr(response, "_content", None)
                    if content is None:
                        content = getattr(response, "content", None)
                    body_size = get_body_size(content)
                    redirects = len(getattr(response, "history", []) or [])
                    elapsed = time.perf_counter() - start_time

                    logger.info(
                        " ".join(
                            [
                                f'{response.request.method} {response.request.url} "{repr(response).replace("Response ", "")} {response.reason}"',
                                (
                                    f"via {proxy_selection.log}"
                                    if proxy_selection.log
                                    else ""
                                ),
                                format_response_metrics(
                                    attempt_number=attempt.retry_state.attempt_number,
                                    max_attempt_number=max_attempt_number,
                                    elapsed=elapsed,
                                    body_size=body_size,
                                    redirects=redirects,
                                    expected_statuses=(
                                        expected_status_codes
                                        if is_expected_status
                                        else None
                                    ),
                                ),
                            ]
                        ).strip(),
                    )

                return response

        # stop_after_attempt 与 reraise=True 理论上保证循环只能 return 或抛异常；保留显式终止用于记录该不变量并满足静态分析器
        raise RuntimeError("request retry loop exited without a response")

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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
            max_attempt_number (int, optional): Maximum number of outer tenacity attempts. If the final attempt returns an unexpected 4xx/5xx response, the response is returned without raising HTTPError so the caller can inspect it. If None, falls back to the Booru instance's max_attempt_number; if that is also None, a single attempt is made. Defaults to None.
            expected_statuses (Collection[int], optional): HTTP statuses that represent terminal business outcomes after niquests returns a response. Matched statuses bypass outer status retry, proxy rotation, and proxy-health failure recording; unmatched 4xx/5xx responses consume the available outer attempts, while the final response is returned without raising HTTPError when the budget is exhausted. Do not include a transient failure signal, such as a site-specific 404 used for rate limiting, when it should recover through outer retry; leave it unexpected and include it in the instance's proxy_cooldown_statuses when it should also count against proxy health. This option does not override niquests' inner Retry status_forcelist. Defaults to None.
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
        tasks: Iterable[Coroutine[Any, Any, Any]],
    ) -> AsyncIterable[Any]:
        """Yield task results in completion order.

        This is suitable for streaming work as soon as possible, but callers must
        not infer the original input index from the yielded order.

        Args:
            tasks: Coroutines to execute concurrently.

        Yields:
            Each result in completion order, or None when its coroutine raises.
        """
        for t in asyncio.as_completed(tasks):
            try:
                result = await t
                yield result
            except Exception as exc:
                logger.error(f"{exc.__class__.__name__}: {exc}")
                yield None

    async def batch_process_tasks(
        self,
        tasks: Iterable[Coroutine[Any, Any, Any]],
    ) -> list[Any]:
        """Return task results in the same order as the input tasks.

        Args:
            tasks: Coroutines to execute concurrently.

        Returns:
            Results in input order, with raised exceptions replaced by None.
        """
        results: list = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"{res.__class__.__name__}: {res}")
                results[i] = None
        return results

    async def download_file(
        self,
        item: DownloadItem,
    ) -> DownloadResult | None:
        """
        下载单个文件到指定路径

        Args:
            item (DownloadItem): 文件下载任务，包含 URL、存储路径、请求头、Referer 与可选 sidecar 元数据.

        Returns:
            DownloadResult | None. 若下载成功，则返回携带原始任务的结果；若下载失败，则返回 None
        """
        temporary_filepath: str | None = None
        try:
            # DownloadItem 接受 niquests 的完整 HeadersType；按具体容器复制并在副本中追加 Referer，既避免修改调用方对象，也保留 list-of-tuples 与 kiss-headers 的兼容性
            request_headers: Any = item.headers
            if request_headers is not None:
                if isinstance(request_headers, list) and item.referer is not None:
                    # Booru.request 的 Referer 快捷参数需要可更新映射；仅在使用该快捷参数时把 tuple 列表正规化，普通下载仍保留 niquests 支持的原始列表形式
                    request_headers = dict(request_headers)
                elif isinstance(request_headers, list):
                    request_headers = request_headers.copy()
                elif hasattr(request_headers, "to_dict"):
                    request_headers = request_headers.to_dict()
                else:
                    request_headers = request_headers.copy()

            # 请求成功不代表响应体有效；空响应若直接写入会留下难以察觉且后续被误判为已下载的 0 字节文件
            response = await self.get(
                item.url,
                headers=cast(HeadersType | None, request_headers),
                referer=item.referer,
            )
            # 基础 HTTP verb 保留最终错误 response 供调用方检查；下载 helper 必须在消费正文前单独拒绝 4xx/5xx，避免把维护页或限流页保存成图片
            response.raise_for_status()
            if not response.content:
                logger.error(f"Empty response body for {item.url}")
                return None

            # 先写入同目录的唯一临时文件，再用原子替换发布最终文件；下载或写盘中断时不会留下会被续跑逻辑跳过的半成品
            directory = os.path.dirname(item.filepath) or "."
            async with aiotempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=f".{os.path.basename(item.filepath)}.",
                suffix=".part",
                delete=False,
            ) as f:
                temporary_filepath = f.name
                await f.write(response.content)
                await f.flush()
            await aioos.replace(temporary_filepath, item.filepath)
            temporary_filepath = None
            return DownloadResult(item=item, filepath=item.filepath)
        except RequestException as exc:
            request_url = getattr(exc.request, "url", item.url)
            logger.error(f"{exc.__class__.__name__} for {request_url} - {exc}")
            return None
        except OSError as exc:
            logger.error(f"{exc.__class__.__name__} for {item.filepath} - {exc}")
            return None
        finally:
            # replace 成功后 temporary_filepath 已清空；其余失败路径只清理本次调用创建的唯一临时文件，不会误删其他并发任务
            if temporary_filepath is not None:
                try:
                    await aioos.remove(temporary_filepath)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    # 清理失败不应覆盖最初的下载或写盘错误；保留临时文件名便于用户依据日志手动处理
                    logger.warning(
                        f"{exc.__class__.__name__} while removing temporary file {temporary_filepath} - {exc}"
                    )

    async def concurrent_download_file(
        self,
        items: Iterable[DownloadItem],
    ) -> AsyncIterable[DownloadResult | None]:
        """
        并发下载文件，忽略已存在且非空的文件

        Args:
            items (Iterable[DownloadItem]): 文件下载任务集合。每个任务自行携带 URL、存储路径、Referer 与可选 sidecar 元数据，避免并发完成顺序破坏业务数据关联。

        Yields:
            DownloadResult | None. 若下载成功，则返回携带原始任务的结果；若下载失败，则返回 None
        """
        input_items = list(items)

        # 同一批次内若多个任务指向相同路径，只保留最先出现的业务项，避免重复下载并发覆盖同一个文件后产生错误的 sidecar 关联
        download_items_by_filepath: dict[str, DownloadItem] = {}
        for item in input_items:
            download_items_by_filepath.setdefault(item.filepath, item)
        download_items = list(download_items_by_filepath.values())
        duplicate_size = len(input_items) - len(download_items)
        if duplicate_size > 0:
            logger.warning(
                f"Filtered {duplicate_size} duplicate destination paths from {len(input_items)} items"
            )

        # filepath 由 DownloadItem 提供，允许同一批任务写入不同目录；exist_ok=True 同时消除一次 exists 系统调用和并发建目录竞态
        directories = {os.path.dirname(item.filepath) for item in download_items}
        await asyncio.gather(
            *(aioos.makedirs(directory, exist_ok=True) for directory in directories if directory)
        )

        async def should_download(item: DownloadItem) -> bool:
            """Return whether the destination is missing or contains no data.

            Args:
                item: Download item whose final filepath should be inspected.

            Returns:
                True when the path is absent or zero bytes; otherwise False.
            """
            try:
                file_stat = await aioos.stat(item.filepath)
            except FileNotFoundError:
                return True
            # 旧版本或外部中断可能留下 0 字节文件；这类文件必须重新下载，不能沿用普通 exists 过滤
            return file_stat.st_size == 0

        # 每个目标只执行一次 stat，并发提交给 aiofiles 的有界线程池，避免大批次逐个 await 文件系统往返
        download_states = await asyncio.gather(
            *(should_download(item) for item in download_items)
        )
        patch_size = len(download_items)
        download_items = [
            item
            for item, needs_download in zip(download_items, download_states)
            if needs_download
        ]
        filter_size = patch_size - len(download_items)
        if filter_size > 0:
            logger.info(f"Filtered {filter_size} existing files from {patch_size} items")

        # 检查下载任务是否为空
        if not download_items:
            return

        # 创建异步任务列表
        tasks = [self.download_file(item) for item in download_items]
        # 并发执行下载任务，结果按照完成顺序返回，业务关联由 DownloadResult.item 保留。
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
        # exist_ok=True 避免并发 sidecar 任务在 exists 与 makedirs 之间发生 TOCTOU 竞态，同时少一次文件系统查询
        await aioos.makedirs(directory, exist_ok=True)

        filepath = os.path.join(directory, filename)
        # 只检查目标文件，避免每次保存 sidecar 时扫描整个目录。
        if not overwrite and await aioos.path.exists(filepath):
            logger.warning(f"File {filename} already exists in {directory}")
            return None
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
        # exist_ok=True 避免并发 sidecar 任务在 exists 与 makedirs 之间发生 TOCTOU 竞态，同时少一次文件系统查询
        await aioos.makedirs(directory, exist_ok=True)

        filepath = os.path.join(directory, filename)
        # 只检查目标文件，避免每次保存 sidecar 时扫描整个目录。
        if not overwrite and await aioos.path.exists(filepath):
            logger.warning(f"File {filename} already exists in {directory}")
            return None
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
    ) -> list[dict] | None:
        """
        获取某一页帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict, optional): 请求头. Defaults to None.
            params (dict, optional): 请求参数. Defaults to None.
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 niquests.AsyncSession.request 的其它关键字参数

        Returns:
            list[dict] | None. 若获取成功，则返回对应的帖子内容列表（合法空页返回空列表）；若请求失败，则返回 None

        Note:
            Empty or malformed HTTP 200 JSON responses are retried with the same bounded attempt setting as the request. A valid JSON empty list is returned immediately and is not retried.
        """
        fetch_attempts = kwargs.get("max_attempt_number")
        if fetch_attempts is None:
            fetch_attempts = self._max_attempt_number
        if fetch_attempts is None:
            fetch_attempts = 1
        fetch_attempts = max(fetch_attempts, 1)

        try:
            # HTTP 200 的空正文或临时 HTML 拦截页不会触发请求层 status retry；只对 JSON 解码失败额外重取整个页面，合法 JSON 空列表仍会立即返回
            async for attempt in AsyncRetrying(
                sleep=asyncio.sleep,
                stop=stop_after_attempt(fetch_attempts),
                wait=wait_exponential_jitter(initial=1, max=10, jitter=3),
                retry=retry_if_exception_type(JSONDecodeError),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    response = await self.get(
                        api,
                        headers=headers,
                        params=params,
                        **kwargs,
                    )
                    # 与 download_file 相同，解析 JSON 前拒绝最终错误 response；fetch_page 的返回值只表示有效业务 JSON 或显式失败
                    response.raise_for_status()
                    content = response.json()

            # 处理回调
            if callback:
                content = callback(content)
            if isinstance(content, list):  # 多个帖子
                return content
            else:  # 单个帖子
                return [content]
        except JSONDecodeError as exc:
            logger.error(f"{exc.__class__.__name__} for {api} - {exc}")
            return None
        except RequestException as exc:
            request_url = getattr(exc.request, "url", api)
            logger.error(f"{exc.__class__.__name__} for {request_url} - {exc}")
            # None 是显式失败状态，不能与服务器成功返回的空 JSON 列表混为同一个分页终止信号
            return None

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
    ) -> AsyncIterable[PageResult | None]:
        """
        并发获取多个页面的帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict, optional): 请求头. Defaults to None.
            params (dict, optional): 请求参数. Defaults to None.
            start_page (int): 查询起始页码
            end_page (int): 查询结束页码
            page_key (str): 页码参数的名称，用于在传递的 params 参数中设置页码
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 niquests.AsyncSession.request 的其它关键字参数

        Yields:
            PageResult | None. 请求完成时返回携带页码的结果，其中 content=None 明确表示请求失败；任务在结果构造前抛出其它异常时返回 None
        """
        if headers is None:
            headers = {}
        base_params = {} if params is None else params.copy()

        async def fetch_page_result(page: int) -> PageResult:
            # 每个并发任务使用独立 params，避免修改调用方传入的字典。
            page_params = base_params.copy()
            page_params.update({page_key: page})
            content = await self.fetch_page(
                api,
                headers=headers,
                params=page_params,
                callback=callback,
                **kwargs,
            )
            return PageResult(page=page, content=content)

        # 创建异步任务列表
        tasks = [fetch_page_result(page) for page in range(start_page, end_page + 1)]
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

    def build_download_items(
        self,
        posts: pd.DataFrame,
        images_directory: str,
        *,
        tag_column: str,
        referer_factory: Callable[[pd.Series], str | None] | None = None,
        extract_pattern: Callable[[str], str] = os.path.basename,
        include_raw: bool = True,
        include_tags: bool = True,
    ) -> list[DownloadItem]:
        """Build DownloadItem objects from a posts dataframe.

        Keeping this conversion at the component boundary preserves the existing
        pandas-based implementation while giving the lower-level downloader a
        stable item/result contract.

        Args:
            posts: Source posts dataframe containing file URLs and metadata.
            images_directory: Directory used to construct final image paths.
            tag_column: Column containing the post tags.
            referer_factory: Optional callable that derives a Referer from one row.
            extract_pattern: Callable that derives a filename from a file URL.
            include_raw: Whether to attach a one-row dataframe for raw sidecars.
            include_tags: Whether to attach tag text for tag sidecars.

        Returns:
            Download items preserving each URL's request and sidecar context.
        """
        items: list[DownloadItem] = []
        for position, (_, post) in enumerate(posts.iterrows()):
            url = post.get("file_url")
            if bool(pd.isna(url)):
                continue

            url = str(url)
            if not url.strip():
                logger.warning(f"Skipping a post with an empty file_url at position {position}")
                continue

            tag = post.get(tag_column) if include_tags and tag_column in post else None
            if tag is not None and bool(pd.isna(tag)):
                tag = None

            # 每个 item 同时携带下载参数和 sidecar 数据，避免并发完成顺序导致错位。
            items.append(
                DownloadItem(
                    url=url,
                    filepath=os.path.join(images_directory, extract_pattern(url)),
                    referer=referer_factory(post) if referer_factory else None,
                    # DataFrame 切片会分配新对象；仅在调用方确实要保存 raw sidecar 时构造，避免默认图片下载为每一行支付额外复制成本
                    raw=posts.iloc[[position]] if include_raw else None,
                    tags=str(tag) if tag is not None else None,
                )
            )
        return items
