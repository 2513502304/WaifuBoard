"""
Booru Image Board API implementation.
"""

import asyncio
import logging
import os
import ssl
import sys
import typing
from typing import Any, Callable
from urllib.parse import quote, unquote

import aiofiles
import httpx
import pandas as pd
from aiofiles import os as aioos
from aiofiles import tempfile as aiotempfile
from fake_useragent import UserAgent
from httpx._client import EventHook
from httpx._config import (
    Limits,
    Proxy,
    Timeout,
)
from httpx._transports.base import AsyncBaseTransport
from httpx._types import (
    AuthTypes,
    CertTypes,
    CookieTypes,
    HeaderTypes,
    ProxyTypes,
    QueryParamTypes,
    TimeoutTypes,
)
from httpx._urls import URL
from tenacity import AsyncRetrying, RetryCallState, RetryError, TryAgain, retry
from tenacity.after import after_log
from tenacity.before import before_log
from tenacity.before_sleep import before_sleep_log
from tenacity.nap import sleep
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential

from .utils import INVALID_CHARS_PATTERN, logger

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
        max_clients: int | None = None,
        directory: str = "./downloads",
        auth: AuthTypes | None = None,
        headers: HeaderTypes | None = None,
        params: QueryParamTypes | None = None,
        cookies: CookieTypes | None = None,
        verify: ssl.SSLContext | str | bool = True,
        cert: CertTypes | None = None,
        http1: bool = True,
        http2: bool = True,
        proxy_url: URL | str | None = None,
        proxy_auth: tuple[str, str] | None = None,
        proxy_headers: HeaderTypes | None = None,
        proxy_ssl_context: ssl.SSLContext | None = None,
        mounts: None | (typing.Mapping[str, AsyncBaseTransport | None]) = None,
        timeout: TimeoutTypes = Timeout(timeout=30.0),
        follow_redirects: bool = True,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        keepalive_expiry: float = 30.0,
        max_attempt_number: int = 5,
        max_redirects: int = 30,
        event_hooks: None | (typing.Mapping[str, list[EventHook]]) = None,
        base_url: URL | str = "",
        transport: AsyncBaseTransport | None = None,
        trust_env: bool = True,
        default_headers: bool = True,
        default_encoding: str | typing.Callable[[bytes], str] = "utf-8",
        logger_level: int = logging.INFO,
    ):
        """
        包装了 httpx.AsyncClient 的客户端类型，提供了更友好的 API 接口

        Args:
            max_clients (int | None, optional): 最大客户端数量，用以限制全局并发请求数量的上限，这会影响并发率。若为 None 或一个非正数，则不限制该上限. Defaults to None.
            directory (str, optional): 当前客户端平台的存储文件根目录. Defaults to "./downloads".
            auth (AuthTypes | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            headers (HeaderTypes | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            params (QueryParamTypes | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            cookies (CookieTypes | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            verify (ssl.SSLContext | str | bool, optional): See httpx.AsyncClient for more details. Defaults to True.
            cert (CertTypes | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            http1 (bool, optional): See httpx.AsyncClient for more details. Defaults to True.
            http2 (bool, optional): See httpx.AsyncClient for more details. Defaults to True.
            proxy_url (URL | str | None, optional): 连接代理服务器时使用的 URL。URL 的 scheme 必须为 "http", "https", "socks5", "socks5h" 之一，URL 的形式为 {scheme}://{[username]:[password]@}{host}:{port}/ 或 {scheme}://{host}:{port}/，例如 "http://127.0.0.1:8080/"。Defaults to None.
            proxy_auth (tuple[str, str] | None, optional): 任何代理认证信息，格式为 (username, password) 的 two-tuple。可以是 bytes 类型或仅含 ASCII 字符的 str 类型。注意：优先使用 proxy_url 中解析出的 auth 参数，若 proxy_url 中解析不出任何 auth，且 proxy_auth 参数不为 None，则使用 proxy_auth 参数为 proxy_url 添加身份验证凭据。Defaults to None.
            proxy_headers (HeaderTypes | None, optional): 用于代理请求的任何 HTTP 头部信息。例如 {"Proxy-Authorization": "Basic <username>:<password>"}。Defaults to None.
            proxy_ssl_context (ssl.SSLContext | None, optional): 用于验证连接代理服务器的 SSL 上下文。如果未指定，将使用默认的 httpcore.default_ssl_context()。Defaults to None.
            mounts (None |, optional): See httpx.AsyncClient for more details. Defaults to None.
            timeout (TimeoutTypes, optional): See httpx.AsyncClient for more details. Defaults to Timeout(timeout=30.0).
            follow_redirects (bool, optional): See httpx.AsyncClient for more details. Defaults to True.
            max_connections (int, optional): 可建立的最大并发连接数. Defaults to 100.
            max_keepalive_connections (int, optional): 允许连接池在此数值以下维持长连接的数量。该值应小于或等于 max_connections. Defaults to 20.
            keepalive_expiry (float, optional): 空闲长连接的时间限制（以秒为单位）. Defaults to 30.0.
            max_attempt_number (int, optional): 最大尝试次数. Defaults to 5.
            max_redirects (int, optional): See httpx.AsyncClient for more details. Defaults to 30.
            event_hooks (None |, optional): See httpx.AsyncClient for more details. Defaults to None.
            base_url (URL | str, optional): See httpx.AsyncClient for more details. Defaults to "".
            transport (AsyncBaseTransport | None, optional): See httpx.AsyncClient for more details. Defaults to None.
            trust_env (bool, optional): See httpx.AsyncClient for more details. Defaults to True.
            default_headers (bool, optional): 是否设置默认浏览器 headers. Defaults to True.
            default_encoding (str | typing.Callable[[bytes], str], optional): See httpx.AsyncClient for more details. Defaults to "utf-8".
            logger_level (int, optional): 日志级别. Defaults to logging.INFO.
        """
        # 最大客户端数量
        self.max_clients = (
            max_clients if max_clients is not None and max_clients > 0 else sys.maxsize
        )
        self.semaphore = asyncio.Semaphore(self.max_clients)

        # 当前客户端平台的存储文件根目录
        self.directory = directory
        
        # 代理配置
        if proxy_url is None:
            proxy = None
        else:
            proxy = Proxy(
                url=proxy_url,
                auth=proxy_auth,
                headers=proxy_headers,
                ssl_context=proxy_ssl_context,
            )

        # 各种客户端行为限制的配置
        limits = Limits(
            max_connections=max_connections,
            max_keepalive_connections=(
                max_keepalive_connections
                if max_connections >= max_keepalive_connections
                else max_connections
            ),
            keepalive_expiry=keepalive_expiry,
        )

        # 最大尝试次数
        self.max_attempt_number = max_attempt_number if max_attempt_number > 0 else 1

        # 是否设置默认浏览器 headers
        if headers is None and default_headers:
            headers = {
                "User-Agent": UserAgent().random,
            }

        # 创建底层 httpx 客户端
        self.client = httpx.AsyncClient(
            auth=auth,
            headers=headers,
            params=params,
            cookies=cookies,
            verify=verify,
            cert=cert,
            http1=http1,
            http2=http2,
            proxy=proxy,
            mounts=mounts,
            timeout=timeout,
            follow_redirects=follow_redirects,
            limits=limits,
            max_redirects=max_redirects,
            event_hooks=event_hooks,
            base_url=base_url,
            transport=transport,
            trust_env=trust_env,
            default_encoding=default_encoding,
        )

        # 设置日志级别
        logging.getLogger("WaifuBoard").setLevel(logger_level)
        logging.getLogger("httpx").setLevel(logger_level)

    @property
    def auth(self):
        """
        发送请求时使用的身份验证类
        返回底层 httpx 客户端的 auth 属性
        """
        return self.client.auth

    @auth.setter
    def auth(self, auth):
        self.client.auth = auth
        logger.info(f"{self.__class__.__name__} auth set to: {auth}")

    @property
    def base_url(self):
        """
        发送相对 URL 请求时使用的基础 URL
        返回底层 httpx 客户端的 base_url 属性
        """
        return self.client.base_url

    @base_url.setter
    def base_url(self, url: str):
        """
        设置发送相对 URL 请求时使用的基础 URL
        将传递给底层 httpx 客户端的 base_url 属性

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
        max_attempt_number: int | None = None,
        accept_encoding: str | None = None,
        referer: str | None = None,
        **kwargs,
    ) -> httpx.Response:
        """
        使用底层 httpx 客户端发送请求

        Args:
            method (str): 请求方法
            url (str): 请求 URL
            max_attempt_number (int, optional): 最大尝试次数. Defaults to 5.
            accept_encoding (str, optional): 设置请求头中的 Accept-Encoding 字段的快捷方式. Defaults to None.
            referer (str, optional): 设置请求头中的 Referer 字段的快捷方式. Defaults to None.

        Returns:
            httpx.Response: 响应对象
        """
        if max_attempt_number is None:
            max_attempt_number = self.max_attempt_number

        headers = self.client.headers.copy()
        if "headers" in kwargs:
            headers.update(kwargs["headers"])
            kwargs.pop("headers")
        if accept_encoding:
            headers.update({"Accept-Encoding": accept_encoding})
        if referer:
            headers.update({"Referer": referer})

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempt_number),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(Exception),
            before=before_log(logger, logging.DEBUG),
            after=after_log(logger, logging.DEBUG),
            before_sleep=before_sleep_log(logger, logging.INFO),
            reraise=True,
        ):
            with attempt:
                async with self.semaphore:
                    response = await self.client.request(
                        method=method, url=url, headers=headers, **kwargs
                    )
                    response.raise_for_status()
                    return response

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 GET 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: _description_
        """
        return await self.request("GET", url, **kwargs)

    async def options(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 OPTIONS 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("OPTIONS", url, **kwargs)

    async def head(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 HEAD 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("HEAD", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 POST 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 PUT 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 PATCH 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 DELETE 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.request("DELETE", url, **kwargs)

    async def download_file(
        self,
        url: str,
        filepath: str,
    ) -> tuple[str, str]:
        """
        下载单个文件到指定路径

        Args:
            url (str): 文件 URL
            filepath (str): 文件存储路径

        Returns:
            tuple[str, str]. 若下载成功，则返回对应的 (url, filepath) 序列；若下载失败，则返回 None
        """
        try:
            # 下载文件
            response = await self.get(url)
            response.raise_for_status()
            # 保存文件
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(response.content)
            return (url, filepath)
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")
            return None

    async def concurrent_download_file(
        self,
        urls: pd.Series,
        directory: str,
        extract_pattern: Callable[[str], str] = os.path.basename,
    ) -> list[tuple[str, str]]:
        """
        并发下载文件到指定目录，忽略已存在的文件
        文件名默认为 urls 中 url 的基础名称（即 url 的最后一个组件），也可以传递可调用对象给 extract_pattern 参数，以指定从 url 中提取文件名的规则

        Args:
            urls (pd.Series): 文件 URLs
            directory (str): 文件存储目录
            extract_pattern (Callable[[str], str], optional): 可调用对象，指定从 url 中提取文件名的规则. Defaults to os.path.basename.

        Returns:
            list[tuple[str, str]]: 下载结果列表，每个元素为 (url, filepath) （下载成功）或 None（下载失败）
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
            return []
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
        result: list[tuple[str, str]] = await asyncio.gather(
            *tasks, return_exceptions=True
        )
        return result

    async def save_raws(
        self,
        raws: pd.DataFrame,
        filepath: str,
    ) -> tuple[str, str]:
        """
        保存单个元数据到指定路径

        Args:
            raws (pd.DataFrame): 元数据内容
            filepath (str): 文件存储路径

        Returns:
            tuple[str, str]. 若保存成功，则返回对应的 (raws, filepath) 序列；若保存失败，则返回 None
        """
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
            return (raws, filepath)
        except OSError as exc:
            logger.error(f"{exc.__class__.__name__} for {filepath} - {exc}")
            return None

    async def concurrent_save_raws(
        self,
        raws: list[pd.DataFrame],
        directory: str,
        filenames: pd.Series,
    ) -> list[tuple[str, str]]:
        """
        并发保存元数据到指定目录，忽略已存在的文件

        Args:
            raws (list[pd.DataFrame]): 元数据内容，必须与 filenames 保持相同形状且一一对应
            directory (str): 文件存储目录
            filenames (pd.Series): 文件名，必须与 raws 保持相同形状且一一对应

        Returns:
            list[tuple[str, str]]: 保存结果列表，每个元素为 (raws, filepath) （保存成功）或 None（保存失败）
        """
        if len(raws) != filenames.size:
            logger.error("Raws and filenames must have the same shape")
            return []
        # 创建目录
        if not await aioos.path.exists(directory):
            await aioos.makedirs(directory)
        # 若存在已有文件，则将其过滤
        else:
            # 获取已有文件列表
            files = await aioos.listdir(directory)
            # 批 raws 大小
            patch_size = len(raws)
            # 过滤已有文件
            filenames = filenames[filenames.isin(files)]
            raws = [raws[index] for index in filenames.index]
            # 已过滤文件数量
            filter_size = patch_size - len(raws)
            if filter_size > 0:
                logger.info(
                    f"Filtered {filter_size} existing files from {patch_size} raws"
                )
        # 检查 raws 是否为空
        if not raws or filenames.empty:
            return
        # 创建异步任务列表
        tasks = [
            self.save_raws(
                raws=raw,
                filepath=os.path.join(
                    directory,  # 文件夹目录
                    filename,  # 文件名
                ),
            )
            for raw, filename in zip(raws, filenames)
        ]
        # 并发执行保存任务
        result: list[tuple[str, str]] = await asyncio.gather(
            *tasks, return_exceptions=True
        )
        return result

    async def save_tags(
        self,
        tag: str,
        filepath: str,
        callback: Callable[[str], str] = lambda x: x.replace(" ", ", ").replace(
            "_", " "
        ),
    ) -> tuple[str, str]:
        """
        保存单个标签到指定路径

        Args:
            tag (str): 标签内容
            filepath (str): 文件存储路径
            callback (Callable[[str], str], optional): 可调用对象，用于后处理标签内容. Defaults to lambda x: x.replace(' ', ', ').replace('_', ' ').

        Returns:
            tuple[str, str]. 若保存成功，则返回对应的 (tags, filepat) 序列；若保存失败，则返回 None
        """
        try:
            # 处理标签内容
            if callback:
                tag = callback(tag)
            # 保存文件
            async with aiofiles.open(filepath, "w") as f:
                await f.write(tag)
            return (tag, filepath)
        except OSError as exc:
            logger.error(f"{exc.__class__.__name__} for {filepath} - {exc}")
            return None

    async def concurrent_save_tags(
        self,
        tags: pd.Series,
        directory: str,
        filenames: pd.Series,
        callback: Callable[[str], str] = lambda x: x.replace(" ", ", ").replace(
            "_", " "
        ),
    ) -> list[tuple[str, str]]:
        """
        并发保存标签到指定目录，忽略已存在的文件

        Args:
            tags (pd.Series): 标签内容，必须与 filenames 保持相同形状且一一对应
            directory (str): 文件存储目录
            filenames (pd.Series): 文件名，必须与 tags 保持相同形状且一一对应
            callback (Callable[[str], str], optional): 可调用对象，用于后处理标签内容. Defaults to lambda x: x.replace(' ', ', ').replace('_', ' ').

        Returns:
            list[tuple[str, str]]: 保存结果列表，每个元素为 (tags, filepath) （保存成功）或 None（保存失败）
        """
        if tags.size != filenames.size:
            logger.error("Tags and filenames must have the same shape")
            return []
        # 创建目录
        if not await aioos.path.exists(directory):
            await aioos.makedirs(directory)
        # 若存在已有文件，则将其过滤
        else:
            # 获取已有文件列表
            files = await aioos.listdir(directory)
            # 批 tags 大小
            patch_size = tags.size
            # 过滤已有文件
            filenames = filenames[filenames.isin(files)]
            tags = tags[filenames.index]
            # 已过滤文件数量
            filter_size = patch_size - tags.size
            if filter_size > 0:
                logger.info(
                    f"Filtered {filter_size} existing files from {patch_size} tags"
                )
        # 检查 tags 是否为空
        if tags.empty or filenames.empty:
            return
        # 创建异步任务列表
        tasks = [
            self.save_tags(
                tag=tag,
                filepath=os.path.join(
                    directory,  # 文件夹目录
                    filename,  # 文件名
                ),
                callback=callback,
            )
            for tag, filename in zip(tags, filenames)
        ]
        # 并发执行保存任务
        result: list[tuple[str, str]] = await asyncio.gather(
            *tasks, return_exceptions=True
        )
        return result

    async def fetch_page(
        self,
        api: str,
        headers: dict,
        params: dict,
        callback: Callable[[Any], Any] | None = None,
        **kwargs,
    ) -> list[dict]:
        """
        获取某一页帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict): 请求头
            params (dict): 请求参数
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 httpx.AsyncClient.request 的其它关键字参数

        Returns:
            list[dict]: 帖子内容列表
        """
        try:
            # 获取帖子内容
            response = await self.get(api, headers=headers, params=params, **kwargs)
            response.raise_for_status()
            content = response.json()
            # 处理回调
            if callback:
                content = callback(content)
            if isinstance(content, list):  # 多个帖子
                return content
            else:  # 单个帖子
                return [content]
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")
            return []

    async def concurrent_fetch_page(
        self,
        api: str,
        headers: dict,
        params: dict,
        start_page: int,
        end_page: int,
        page_key: str,
        callback: Callable[[Any], Any] | None = None,
        **kwargs,
    ) -> list[dict]:
        """
        并发获取多个页面的帖子内容

        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict): 请求头
            params (dict): 请求参数
            start_page (int): 查询起始页码
            end_page (int): 查询结束页码
            page_key (str): 页码参数的名称，用于在传递的 params 参数中设置页码
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 httpx.AsyncClient.request 的其它关键字参数

        Returns:
            list[dict]: 帖子内容列表
        """
        # 结果列表
        result: list[dict] = []
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
        task_result: list[list[dict]] = await asyncio.gather(
            *tasks, return_exceptions=True
        )
        for content in task_result:
            if content:
                result.extend(content)
        return result

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
            remove_invalid_characters (bool, optional): 是否移除文件名中无效的 Windows/MacOS/Linux 路径字符. Defaults to True.

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
        # 移除文件名中无效的 Windows/MacOS/Linux 路径字符
        if remove_invalid_characters:
            filename = INVALID_CHARS_PATTERN.sub("", filename)
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
