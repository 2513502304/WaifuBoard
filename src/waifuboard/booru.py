"""
Booru Image Board API implementation.
"""

import asyncio
import os
import re
from typing import Any, Callable
from urllib.parse import quote, unquote

import aiofiles
import httpx
import pandas as pd
from aiofiles import os as aioos
from aiofiles import tempfile as aiotempfile
from fake_useragent import UserAgent
from httpx._types import AuthTypes

from .utils import logger

__all__ = [
    'Booru',
    'BooruComponent',
]

# 提取文件名中的无效 Windows/MacOS/Linux 路径字符规则
invalid_chars_pattern = re.compile(r'[\\/:*?"<>|]')


class Booru():
    """
    Base Booru Image Board API
    """

    def __init__(self, *, directory: str = './downloads'):
        # 当前客户端平台的存储文件根目录
        self.directory = directory

        self.headers = {
            'User-Agent': UserAgent().random,
        }
        self.params = {}
        self.client = httpx.AsyncClient(
            headers=self.headers,
            params=self.params,
            http1=True,
            http2=True,
            follow_redirects=True,
            base_url='',
            timeout=30,
        )

    @property
    def auth(self):
        """
        发送请求时使用的身份验证类
        返回底层 httpx 客户端的 auth 属性
        """
        return self.client.auth

    @auth.setter
    def auth(self, auth: AuthTypes):
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
        **kwargs,
    ) -> httpx.Response:
        """
        使用底层 httpx 客户端发送请求

        Args:
            method (str): 请求方法
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return self.client.request(method=method, url=url, **kwargs)

    async def get(
        self,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 GET 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: _description_
        """
        return await self.client.get(url=url, **kwargs)

    async def options(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 OPTIONS 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.options(url=url, **kwargs)

    async def head(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 HEAD 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.head(url=url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 POST 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.post(url=url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 PUT 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.put(url=url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 PATCH 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.patch(url=url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """
        使用底层 httpx 客户端发送 DELETE 请求

        Args:
            url (str): 请求 URL

        Returns:
            httpx.Response: 响应对象
        """
        return await self.client.delete(url=url, **kwargs)

    async def download_file(
        self,
        url: str,
        filepath: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        """
        下载单个文件到指定路径

        Args:
            url (str): 文件 URL
            filepath (str): 文件存储路径
            semaphore (asyncio.Semaphore): 信号量，用于控制并发下载的数量
            
        Returns:
            tuple[str, str]. 若下载成功，则返回对应的 (url, filepath) 序列；若下载失败，则返回 None
        """
        # 使用信号量控制并发下载
        async with semaphore:
            try:
                # 下载文件
                response = await self.get(url)
                response.raise_for_status()
                # 保存文件
                async with aiofiles.open(filepath, 'wb') as f:
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
        concurrency: int = 8,
    ) -> list[tuple[str, str]]:
        """
        并发下载文件到指定目录，忽略已存在的文件  
        文件名默认为 urls 中 url 的基础名称（即 url 的最后一个组件），也可以传递可调用对象给 extract_pattern 参数，以指定从 url 中提取文件名的规则

        Args:
            urls (pd.Series): 文件 URLs
            directory (str): 文件存储目录
            extract_pattern (Callable[[str], str], optional): 可调用对象，指定从 url 中提取文件名的规则. Defaults to os.path.basename.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
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
                logger.info(f"Filtered {filter_size} existing files from {patch_size} URLs")
        # 检查 URLs 是否为空
        if urls.empty:
            return []
        # 信号量
        semaphore = asyncio.Semaphore(concurrency)
        # 创建异步任务列表
        tasks = [self.download_file(
            url=url,
            filepath=os.path.join(
                directory,
                extract_pattern(url),
            ),
            semaphore=semaphore,
        ) for url in urls]
        # 并发执行下载任务
        result: list[tuple[str, str]] = await asyncio.gather(*tasks, return_exceptions=True)
        return result

    async def save_raws(
        self,
        raws: pd.DataFrame,
        filepath: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        """
        保存单个元数据到指定路径

        Args:
            raws (pd.DataFrame): 元数据内容
            filepath (str): 文件存储路径
            semaphore (asyncio.Semaphore): 信号量，用于控制并发保存的数量
            
        Returns:
            tuple[str, str]. 若保存成功，则返回对应的 (raws, filepath) 序列；若保存失败，则返回 None
        """
        # 使用信号量控制并发保存
        async with semaphore:
            try:
                # 保存文件
                async with aiofiles.open(filepath, 'w') as f:
                    await f.write(raws.to_json(
                        orient='records',
                        indent=4,
                        lines=False,
                        mode='w',
                    ))
                return (raws, filepath)
            except OSError as exc:
                logger.error(f"{exc.__class__.__name__} for {filepath} - {exc}")
                return None

    async def concurrent_save_raws(
        self,
        raws: list[pd.DataFrame],
        directory: str,
        filenames: pd.Series,
        concurrency: int = 8,
    ) -> list[tuple[str, str]]:
        """
        并发保存元数据到指定目录，忽略已存在的文件

        Args:
            raws (list[pd.DataFrame]): 元数据内容，必须与 filenames 保持相同形状且一一对应
            directory (str): 文件存储目录
            filenames (pd.Series): 文件名，必须与 raws 保持相同形状且一一对应
            concurrency (int, optional): 并发保存的数量. Defaults to 8.

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
                logger.info(f"Filtered {filter_size} existing files from {patch_size} raws")
        # 检查 raws 是否为空
        if not raws or filenames.empty:
            return
        # 信号量
        semaphore = asyncio.Semaphore(concurrency)
        # 创建异步任务列表
        tasks = [
            self.save_raws(
                raws=raw,
                filepath=os.path.join(
                    directory,  # 文件夹目录
                    filename,  # 文件名
                ),
                semaphore=semaphore,
            ) for raw, filename in zip(raws, filenames)
        ]
        # 并发执行保存任务
        result: list[tuple[str, str]] = await asyncio.gather(*tasks, return_exceptions=True)
        return result

    async def save_tags(
            self,
            tag: str,
            filepath: str,
            semaphore: asyncio.Semaphore,
            callback: Callable[[str], str] = lambda x: x.replace(' ', ', ').replace('_', ' '),
    ) -> tuple[str, str]:
        """
        保存单个标签到指定路径

        Args:
            tag (str): 标签内容
            filepath (str): 文件存储路径
            callback (Callable[[str], str], optional): 可调用对象，用于后处理标签内容. Defaults to lambda x: x.replace(' ', ', ').replace('_', ' ').
            semaphore (asyncio.Semaphore): 信号量，用于控制并发保存的数量
            
        Returns:
            tuple[str, str]. 若保存成功，则返回对应的 (tags, filepat) 序列；若保存失败，则返回 None
        """
        # 使用信号量控制并发保存
        async with semaphore:
            try:
                # 处理标签内容
                if callback:
                    tag = callback(tag)
                # 保存文件
                async with aiofiles.open(filepath, 'w') as f:
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
            concurrency: int = 8,
            callback: Callable[[str], str] = lambda x: x.replace(' ', ', ').replace('_', ' '),
    ) -> list[tuple[str, str]]:
        """
        并发保存标签到指定目录，忽略已存在的文件  

        Args:
            tags (pd.Series): 标签内容，必须与 filenames 保持相同形状且一一对应
            directory (str): 文件存储目录
            filenames (pd.Series): 文件名，必须与 tags 保持相同形状且一一对应
            callback (Callable[[str], str], optional): 可调用对象，用于后处理标签内容. Defaults to lambda x: x.replace(' ', ', ').replace('_', ' ').
            concurrency (int, optional): 并发保存的数量. Defaults to 8.

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
                logger.info(f"Filtered {filter_size} existing files from {patch_size} tags")
        # 检查 tags 是否为空
        if tags.empty or filenames.empty:
            return
        # 信号量
        semaphore = asyncio.Semaphore(concurrency)
        # 创建异步任务列表
        tasks = [
            self.save_tags(
                tag=tag,
                filepath=os.path.join(
                    directory,  # 文件夹目录
                    filename,  # 文件名
                ),
                callback=callback,
                semaphore=semaphore,
            ) for tag, filename in zip(tags, filenames)
        ]
        # 并发执行保存任务
        result: list[tuple[str, str]] = await asyncio.gather(*tasks, return_exceptions=True)
        return result

    async def fetch_page(
        self,
        api: str,
        headers: dict,
        params: dict,
        semaphore: asyncio.Semaphore,
        callback: Callable[[Any], Any] | None = None,
        **kwargs,
    ) -> list[dict]:
        """
        获取某一页帖子内容
        
        Args:
            api (str): API URL，响应以 json 格式返回
            headers (dict): 请求头
            params (dict): 请求参数
            semaphore (asyncio.Semaphore): 信号量，用于控制并发下载的数量
            callback (Callable[[Any], Any], optional): 回调函数，用于后处理每个页面帖子的 json 响应内容. Defaults to None.
            **kwargs: 传递给 httpx.AsyncClient.request 的其它关键字参数
            
        Returns:
            list[dict]: 帖子内容列表
        """
        # 使用信号量控制并发下载
        async with semaphore:
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
        concurrency: int = 8,
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
        # 信号量
        semaphore = asyncio.Semaphore(concurrency)
        # 结果列表
        result: list[dict] = []
        # 创建异步任务列表
        tasks = []
        # 获取指定页码的帖子列表
        for page in range(start_page, end_page + 1):
            params.update({page_key: page})
            tasks.append(self.fetch_page(
                api,
                headers=headers,
                params=params.copy(),
                semaphore=semaphore,
                callback=callback,
                **kwargs,
            ))
        # 并发执行下载任务
        task_result: list[list[dict]] = await asyncio.gather(*tasks, return_exceptions=True)
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
            若 remove_invalid_characters 为 False，则永远不要使用该方法返回的规范化名称作为存储文件的文件名，因为解码经过 url 编码后的基础名称中，可能包含非法字符（在按照 a-z 排序后的标签中，可能包含 ： < > : " / \ | ? * 等 Windows 系统中的非法字符，从而引发 OSError: [WinError 123] 文件名、目录名或卷标语法不正确）
        """
        # 提取帖子下载链接的文件名
        filename = extract_pattern(url)
        # 解码 url 编码后的文件名
        filename = unquote(filename)
        # 移除文件名中无效的 Windows/MacOS/Linux 路径字符
        if remove_invalid_characters:
            filename = invalid_chars_pattern.sub('', filename)
        return filename


class BooruComponent():
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
