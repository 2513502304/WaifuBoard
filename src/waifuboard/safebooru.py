"""
Safebooru Image Board API implementation.
"""

import asyncio
import os
import re

import httpx
import pandas as pd
from fake_useragent import UserAgent
from httpx._types import AuthTypes
from lxml import etree

from .booru import Booru, BooruComponent
from .utils import logger

__all__ = [
    # base classes
    'Safebooru',
    'SafebooruComponent',

    # client classes
    'SafebooruClient',

    # component classes
    'SafebooruPosts',
    'SafebooruTags',
    'SafebooruComments',
]


class Safebooru(Booru):
    """
    Safebooru Image Board API
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class SafebooruComponent(BooruComponent):
    """
    Safebooru Image Board Component
    """

    def __init__(self, client: Safebooru):
        super().__init__(client)


class SafebooruClient(Safebooru):
    """
    Safebooru API reference document: https://safebooru.org/index.php?page=help&topic=dapi
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 设置发送相对 URL 请求时使用的基础 URL
        self.base_url = 'https://safebooru.org'

        # 初始化各组件
        self.posts = SafebooruPosts(self)
        # self.tags = SafebooruTags(self)
        # self.artists = YandereArtists(self)
        # self.comments = SafebooruComments(self)
        # self.wiki = YandereWiki(self)
        # self.notes = YandereNotes(self)
        # self.users = YandereUsers(self)
        # self.forum = YandereForum(self)
        # self.pools = YanderePools(self)
        # self.favorites = YandereFavorites(self)

        # 登录后修改（默认无账号）
        #!由于 Safebooru 平台存在限流，最多检索 200000 篇帖子（无论是否登录）
        self.MAX_PID = 200000

    def login(self, auth: AuthTypes):
        # TODO
        raise NotImplementedError("The method is not implemented")


class SafebooruPosts(SafebooruComponent):
    """
    Posts: https://safebooru.org/index.php?page=help&topic=dapi
    """

    def __init__(self, client: SafebooruClient):
        super().__init__(client)

    async def list_pid(
        self,
        tags: str = 'all',
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定标签帖子列表的最大 pid  
        post 页面不受任何参数来控制展示的帖子数量，固定每页 42 篇帖子，第一页的 pid 参数为 0，第 n 页的 pid 为 (n - 1) * 42
        
        Note: 
            由于 safebooru 平台存在限流，最多检索 200000 篇帖子，所以 pid 不能超过 200000
            否则在 https://safebooru.org/index.php?page=post&s=list&tags=all&pid=200001 网页中会弹出 Unable to search this deep in temporarily. 提示
            在 https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1&limit=1&pid=200001 网页中会返回：
            ```
            <?xml version="1.0" encoding="UTF-8"?>
            <response success="false" reason="Search error: API limited due to abuse."/>
            ```
            
        Args:
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。更多信息请参阅 cheatsheet。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换。若设置该参数，则忽略 id 参数，仅获得指定 tags 的帖子. Defaults to 'all'. 表示搜索全站

        Returns:
            int: html 分页器中的最大 pid
        """
        url = 'index.php'  # 与 safebooru 文档中的 api 接口描述不同，这里剔除了 api 的请求参数，并将其放入 params 中（httpx issue #3621, params 中的参数会完全覆盖 url 中的请求参数，而不是合理地拼接到 url 中）
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'page': 'post',  # 固定 api 参数
            's': 'list',  # 固定 api 参数
            'pid': 0,  # 查询帖子开始序号，且与 limit 参数相乘不超过 200000，pid 默认从 0 开始
            'tags': tags,  # 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。更多信息请参阅 cheatsheet。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换。若设置该参数，则忽略 id 参数，仅获得指定 tags 的帖子
        }
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大 pid
            tree = etree.HTML(response.text)
            # 当前页为 b 标签，只有一页时，仅存在 b 标签；超过一页时，余下的页为 a 标签，且最后一页的 a 标签中含有 alt="last page" 属性
            pagination = tree.xpath('//div[@class="pagination"]/a[@alt="last page"]/@href')
            if pagination:  # 存在分页器，说明该页面至少有两页
                last_pid = re.findall(r'pid=(\d+)', pagination[0])[0]
                return int(last_pid)
            else:  # 不存在分页器，说明该页面只有一页
                return 0
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def list(
        self,
        limit: int = 100,
        start_page: int = 0,
        end_page: int = 0,
        all_page: bool = False,
        tags: str = '',
        cid: int | None = None,
        id: int | None = None,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        List
        
        Url for API access: index.php?page=dapi&s=post&q=index
        
        - limit: How many posts you want to retrieve. There is a hard limit of 1000 posts per request.
        - pid: The page number.
        - tags: The tags to search for. Any tag combination that works on the web site will work here. This includes all the meta-tags. See cheatsheet for more information.
        - cid: Change ID of the post. This is in Unix time so there are likely others with the same value if updated at the same time.
        - id: The post id.
        - json: Set to 1 for JSON formatted response.

        Note: 
            由于 safebooru 平台存在限流，最多检索 200000 篇帖子，所以 limit * page 相乘不能超过 200000
            否则在 https://safebooru.org/index.php?page=post&s=list&tags=all&pid=200001 网页中会弹出 Unable to search this deep in temporarily. 提示
            在 https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1&limit=1&pid=200001 网页中会返回：
            ```
            <?xml version="1.0" encoding="UTF-8"?>
            <response success="false" reason="Search error: API limited due to abuse."/>
            ```
            
        Return json format:
        ```
        [
            {
                "preview_url": "https://safebooru.org/thumbnails/535/thumbnail_ac081fb888bf3ce0a0c387ce028ca731c7522e0f.jpg",
                "sample_url": "https://safebooru.org/samples/535/sample_ac081fb888bf3ce0a0c387ce028ca731c7522e0f.jpg",
                "file_url": "https://safebooru.org/images/535/ac081fb888bf3ce0a0c387ce028ca731c7522e0f.jpg",
                "directory": 535,
                "hash": "fd12224ac5f69a151bf4589f80277e5f",
                "width": 2048,
                "height": 1402,
                "id": 5979649,
                "image": "ac081fb888bf3ce0a0c387ce028ca731c7522e0f.jpg",
                "change": 1754388019,
                "owner": "danbooru",
                "parent_id": 0,
                "rating": "general",
                "sample": true,
                "sample_height": 582,
                "sample_width": 850,
                "score": null,
                "tags": "1boy animal_feet animal_hands black_capelet blue_hair blue_scales capelet claws clothing_cutout colored_inner_hair commentary_request creature dark-skinned_male dark_skin dragon dragon_boy dragon_ears dragon_horns dragon_tail dragon_wings full_body hand_on_own_head high_collar highres horns indie_virtual_youtuber knee_up long_hair looking_at_viewer male_focus monster_boy multicolored_hair pelvic_curtain puffy_pants ruteko_(ruko220) shoulder_cutout sitting sleeveless slit_pupils solo tail virtual_youtuber wings yellow_eyes",
                "source": "https://twitter.com/ruko220/status/1694335247796830682",
                "status": "active",
                "has_notes": false,
                "comment_count": 0
            },
            ...
        ]
        ```
        
        获取在起始页码与结束页码范围内，指定标签的帖子列表；若 all_page 为 True，则获取当前查询标签下所有页码的帖子列表
        
        Args:
            limit (int, optional): 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇. Defaults to 100.
            start_page (int, optional): 查询起始页码. Defaults to 0.
            end_page (int, optional): 查询结束页码. Defaults to 0.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。更多信息请参阅 cheatsheet。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换。若设置该参数，则忽略 id 参数，仅获得指定 tags 的帖子. Defaults to ''. 表示搜索全站
            cid (int | None, optional): 帖子的变更 id。这是 Unix 时间，所以如果在同一时间更新，可能会有其他帖子具有相同的值. Defaults to None. 表示不根据 cid 去重
            id (int | None, optional): 帖子 id。若设置该参数，则忽略 limit, pid 参数，仅获得指定 id 的某一个帖子。若超出 safebooru 的最大帖子 id，则自动截断至最大帖子 id。若设置 tags 参数，则忽略该参数. Defaults to None. 表示不查询单独 id
            concurrency (int, optional): 并发下载的数量. Defaults to 8.

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if limit > 1000:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 1000
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 1000.")
        url = 'index.php'  # 与 safebooru 文档中的 api 接口描述不同，这里剔除了 api 的请求参数，并将其放入 params 中（httpx issue #3621, params 中的参数会完全覆盖 url 中的请求参数，而不是合理地拼接到 url 中）
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'page': 'dapi',  # 固定 api 参数
            's': 'post',  # 固定 api 参数
            'q': 'index',  # 固定 api 参数
            'limit': limit,  # 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇
            'pid': 0,  # 查询页码
            'tags': tags,  # 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。更多信息请参阅 cheatsheet。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换。若设置该参数，则忽略 id 参数，仅获得指定 tags 的帖子
            'cid': cid,  # 帖子的变更 id。这是 Unix 时间，所以如果在同一时间更新，可能会有其他帖子具有相同的值
            'id': id,  # 帖子 id。若设置该参数，则忽略 limit, pid 参数，仅获得指定 id 的某一个帖子。若超出 safebooru 的最大帖子 id，则自动截断至最大帖子 id。若设置 tags 参数，则忽略该参数
            'json': 1,  # 设置为 1 以获取 JSON 格式的响应
        }
        # 结果列表
        result: list[dict] = []
        # 若设置 id 参数，则忽略 limit, pid 参数，仅获得指定 id 的某一个帖子
        if id is not None:
            params['tags'] = ''  # 忽略 tags 参数，防止因为 tags 参数存在而导致无法查询指定 id 的帖子
            result = await self.client.concurrent_fetch_page(  # safebooru 在搜索 id 时，返回的结果列表仅包含一个帖子
                url,
                headers=headers,
                params=params,
                start_page=0,
                end_page=0,
                page_key='pid',
                concurrency=1,
            )
            return pd.DataFrame(result)
        #!优先于除 id 以外的所有请求参数
        #!由于 Safebooru 平台存在限流，最多检索 200000 篇帖子（无论是否登录）
        # 获取当前查询标签下所有页码的帖子列表
        if all_page:
            max_pid = await self.list_pid(tags=tags, )  # 获取 html 分页器中的最大 pid
            max_pid += 42  # max_pid 仅代表当前最大页码的第一篇帖子的上一个序号，加上固定的每页 42 篇帖子，得到当前页码的最后一篇帖子序号的边界范围
            max_page = max_pid // limit  # 计算最大页码（保守计算，以免超出限制引发响应异常）
            logger.info(f"Maximum page number is equal to {max_page} for {limit = }, {tags = }")

            # 查看整除计算结果是否忽略了某些帖子，以及能否在不超过最大帖子数量限制的情况下获取它们
            ignored_posts: bool = (max_pid - max_page * limit > 0) and ((max_page + 1) * limit <= self.client.MAX_PID)

            if max_pid > self.client.MAX_PID:
                remain_pid = max_pid - self.client.MAX_PID
                max_pid = self.client.MAX_PID  # 限制最大 pid 不超过 200000
                max_page = max_pid // limit  # 计算最大页码（保守计算，以免超出限制引发响应异常）
                logger.warning(f"Maximum page number is set to {max_page} for {limit = }, {tags = }, because {max_pid + remain_pid} exceeds {self.client.MAX_PID}")

                #!超过 MAX_PID 限制时，不能获取忽略的帖子，因为再次使用 limit 参数请求下一页时会导致 pid 超过 200000
                ignored_posts: bool = False

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=0,
                end_page=max_page - 1,
                page_key='pid',
                concurrency=concurrency,
            )

            # 获取忽略后的帖子
            if ignored_posts:
                logger.info(f"Find ignored page number {max_page + 1} for {limit = }, {tags = }, try to fetch them")
                ignored_result = await self.client.concurrent_fetch_page(
                    url,
                    headers=headers,
                    params=params,
                    start_page=max_page,
                    end_page=max_page,
                    page_key='pid',
                    concurrency=concurrency,
                )
                if ignored_result:
                    # 将忽略的帖子添加到结果列表中
                    result.extend(ignored_result)
        # 获取在起始页码与结束页码范围内，指定标签的帖子列表
        else:
            #!没超过 MAX_PID 限制时，由于 page 的范围是确定的，不需要通过 pid 等参数间接计算（主要是整除计算带来的余数舍去），所以直接获取即可
            #!超过 MAX_PID 限制时，不能获取忽略的帖子，因为再次使用 limit 参数请求下一页时会导致 pid 超过 200000
            if (start_pid := limit * start_page) > self.client.MAX_PID:
                start_page = self.client.MAX_PID // limit  # 计算最大页码（保守计算，以免超出限制引发响应异常）
                logger.warning(f"Start page is set to {start_page} for {limit = }, {tags = }, because {start_pid} exceeds {self.client.MAX_PID}")
            if (end_pid := limit * end_page) > self.client.MAX_PID:
                end_page = self.client.MAX_PID // limit  # 计算最大页码（保守计算，以免超出限制引发响应异常）
                logger.warning(f"End page is set to {end_page} for {limit = }, {tags = }, because {end_pid} exceeds {self.client.MAX_PID}")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=start_page - 1,
                end_page=end_page - 1,
                page_key='pid',
                concurrency=concurrency,
            )
        return pd.DataFrame(result)

    def deleted_image(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    async def download(
        self,
        limit: int = 100,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        tags: str = '',
        cid: int | None = None,
        id: int | None = None,
        save_raws: bool = False,
        save_tags: bool = False,
        concurrency: int = 8,
    ) -> None:
        """
        下载在起始页码与结束页码范围内，指定标签的帖子列表中的帖子；若 all_page 为 True，则下载当前查询标签下所有页码的帖子列表中的帖子

        Args:
            limit (int, optional): 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇. Defaults to 100.
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。更多信息请参阅 cheatsheet。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换。若设置该参数，则忽略 id 参数，仅获得指定 tags 的帖子. Defaults to ''. 表示搜索全站
            cid (int | None, optional): 帖子的变更 id。这是 Unix 时间，所以如果在同一时间更新，可能会有其他帖子具有相同的值. Defaults to None. 表示不根据 cid 去重
            id (int | None, optional): 帖子 id。若设置该参数，则忽略 limit, pid 参数，仅获得指定 id 的某一个帖子。若超出 safebooru 的最大帖子 id，则自动截断至最大帖子 id。若设置 tags 参数，则忽略该参数. Defaults to None. 表示不查询单独 id
            save_raws (bool, optional): 是否保存帖子 api 响应的元数据（json 格式）. Defaults to False.
            save_tags (bool, optional): 是否保存帖子标签. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
        """
        # 获取当前查询标签下所有页码的帖子列表中的帖子
        posts = await self.list(
            limit=limit,
            start_page=start_page,
            end_page=end_page,
            all_page=all_page,
            tags=tags,
            cid=cid,
            id=id,
            concurrency=concurrency,
        )

        if posts.empty:
            logger.info(f"All of the posts are empty.")
            return

        # 下载帖子
        urls = posts['file_url']  # 帖子 URLs
        if id is not None:  # 存储文件目录
            posts_directory = os.path.join(self.directory, f'{id}')  # 帖子文件目录
            images_directory = os.path.join(posts_directory, 'images')  # 图像文件目录
        else:
            posts_directory = os.path.join(self.directory, f'{tags if tags !="" else "all"}')  # 帖子文件目录
            images_directory = os.path.join(posts_directory, 'images')  # 图像文件目录
        result: list[tuple[str, str]] = await self.client.concurrent_download_file(
            urls,
            images_directory,
            concurrency=concurrency,
        )

        if not result:
            logger.info(f"Downloaded 0 successful, 0 failed for posts: {posts['id'].tolist()}")
            return

        # 获取下载成功的帖子 url 以及文件路径
        successful_urls = pd.Series([res[0] for res in result if res is not None])
        successful_filepaths = pd.Series([res[1] for res in result if res is not None])
        logger.info(f"Downloaded {successful_urls.size} successful, {len(result) - successful_urls.size} failed for posts: {posts['id'].tolist()}")

        # 从全部 url 中过滤出下载成功的 url 中的索引，并用于后续的筛选（仅保存下载成功的 url 额外数据）
        successful_url_indices = urls[urls.isin(successful_urls)].index

        # 保存帖子 api 响应的元数据（json 格式）
        if save_raws:
            # 保存元数据
            raws = [posts.loc[[index]] for index in successful_url_indices]  # 筛选后的元数据
            raws_directory = os.path.join(posts_directory, 'raws')  # 元数据文件目录
            raws_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.json')  # 元数据文件名
            await self.client.concurrent_save_raws(
                raws,
                raws_directory,
                filenames=raws_filenames,
                concurrency=concurrency,
            )

        # 保存标签
        if save_tags:
            # 帖子标签
            tags = posts['tags']
            # 保存标签
            tags = tags[successful_url_indices]  # 筛选后的 tags
            tags_directory = os.path.join(posts_directory, 'tags')  # 标签文件目录
            tags_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.txt')  # 标签文件名
            # 保存标签
            await self.client.concurrent_save_tags(
                tags,
                tags_directory,
                filenames=tags_filenames,
                concurrency=concurrency,
            )


class SafebooruTags(SafebooruComponent):
    """
    Tags: https://safebooru.org/index.php?page=help&topic=dapi
    """

    def __init__(self, client: SafebooruClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class SafebooruComments(SafebooruComponent):
    """
    Comments: https://safebooru.org/index.php?page=help&topic=dapi
    """

    def __init__(self, client: SafebooruClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")
