"""
Booru Image Board API implementation.
"""

import asyncio
import logging
import os
import random
from http.cookiejar import CookieJar
from typing import (
    Any,
    Literal,
    Callable,
    Coroutine,
    Iterable,
    AsyncIterable,
    TypeAlias,
    cast,
)
from urllib.parse import urlparse, parse_qs, parse_qsl, quote, unquote

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
    BodyType,
    CacheLayerAltSvcType,
    # CookiesType,
    HeadersType,
    HttpAuthenticationType,
    HttpMethodType,
    MultiPartFilesAltType,
    MultiPartFilesType,
    # ProxyType,
    QueryParameterType,
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
from urllib3.util.retry import Retry
from urllib3.util.timeout import Timeout

from .utils import normalize_filepath, logger

CookiesType: TypeAlias = dict[str, str] | RequestsCookieJar | CookieJar
ProxyType: TypeAlias = dict[str, str] | str
ProxiesType: TypeAlias = (
    tuple[dict[str, str], ...] | tuple[str, ...] | dict[str, str] | str
)

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
        proxies: ProxyType | None = None,
        trust_env: bool = True,
        max_redirects: int = 30,
        retries: RetryType = 5,
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
        hooks: (
            AsyncHookType[PreparedRequest | Response | AsyncResponse] | None
        ) = None,
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
            params (QueryParameterType, optional): Dictionary of querystring data to attach to each Request <Request>. The dictionary values may be lists for representing multivalued query parameters. Defaults to None.
            cookies (CookiesType, optional): A CookieJar containing all currently outstanding cookies set on this session. By default it is a RequestsCookieJar <requests.cookies.RequestsCookieJar>, but may be any other cookielib.CookieJar compatible object. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Default authentication tuple or object to attach to every request emitted. Defaults to None.
            proxies (ProxyType, optional): Dictionary mapping protocol or protocol and host to the URL of the proxy (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to be used on each Request <Request>. If a single string is provided, it will be used for both http and https. Defaults to None.
            trust_env (bool, optional): Trust environment settings for proxy configuration, default authentication and similar. Defaults to True.
            max_redirects (int, optional): Maximum number of redirects allowed. If the request exceeds this limit, a TooManyRedirects exception is raised. This defaults to requests.models.DEFAULT_REDIRECT_LIMIT, which is 30. Defaults to 30.
            retries (RetryType, optional): Configure a number of times a request must be automatically retried before giving up. Defaults to 5.
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

        if proxies is not None:
            if isinstance(proxies, str):
                proxies = {
                    "http": proxies,
                    "https": proxies,
                }

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
        self.client.proxies = proxies if proxies is not None else {}
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
                parse_qs(parsed_url.query) | params
            )  # 获取 URL 中的请求参数，并将其与 params 参数合并
        #!requests/httpx 无法*正确处理* dict 类型的请求参数，需要将其转换为 JSON 字符串
        for key, value in params.items():
            if isinstance(value, dict):
                params[key] = orjson.dumps(value).decode("utf-8")

        if proxies is not None:
            if isinstance(proxies, tuple):
                proxies = random.choice(proxies)
            if isinstance(proxies, str):
                proxies = {
                    "http": proxies,
                    "https": proxies,
                }

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
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
        )
        await self.client.gather(response)

        # 统一为 sync Response：
        # - await 一次 .content 把 body 读进 _content 缓存，再把 __class__ 降回 Response，调用方访问 .text / .content 就不必 await
        if isinstance(response, AsyncResponse):
            await response.content
            response.__class__ = Response

        response: Response = cast(Response, response)

        logger.info(
            f'{response.request.method} {response.request.url} "{repr(response).replace("Response ", "")} {response.reason}"',
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
        proxies: ProxiesType | None = None,
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
            params (QueryParameterType, optional): Dictionary or bytes to be sent in the query string for the Request. Defaults to None.
            data (BodyType | AsyncBodyType, optional): Dictionary, list of tuples, bytes, or file-like object to send in the body of the Request. Defaults to None.
            cookies (CookiesType, optional): Dict or CookieJar object to send with the Request. Defaults to None.
            files (MultiPartFilesType | MultiPartFilesAltType, optional): Dictionary of 'filename': file-like-objects for multipart encoding upload. Defaults to None.
            auth (HttpAuthenticationType | AsyncHttpAuthenticationType, optional): Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth. Defaults to None.
            timeout (TimeoutType, optional): How long to wait for the server to send data before giving up, as a float, or a :ref:(connect timeout, read timeout) <timeouts> tuple. Defaults to None.
            allow_redirects (bool, optional): Set to True by default. Defaults to True.
            proxies (ProxiesType, optional): Dictionary mapping protocol or protocol and hostname to the URL of the proxy. If a single string is provided, it will be used for both http and https. It can also be a tuple containing the above two types. If provided, an element will be randomly selected from this tuple to serve as the proxies. Defaults to None.
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
    ) -> tuple[str, str, str] | None:
        """
        保存单个元数据到指定路径

        Args:
            raws (pd.DataFrame): 元数据内容
            directory (str): 文件存储目录
            filename (str): 文件名
            overwrite (bool, optional): 是否覆盖已有同名文件. Defaults to False.

        Returns:
            tuple[str, str, str]. 若保存成功，则返回对应的 (raws, directory, filename)；若保存失败，则返回 None
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
