"""
Moebooru Image Board API implementation.
"""

import asyncio
import os

import httpx
import pandas as pd
from fake_useragent import UserAgent
from httpx._types import AuthTypes
from lxml import etree

from .booru import Booru, BooruComponent
from .utils import logger

__all__ = [
    # base classes
    'Moebooru',
    'MoebooruComponent',

    # client classes
    'YandereClient',

    # component classes
    'YanderePosts',
    'YandereTags',
    'YandereArtists',
    'YandereComments',
    'YandereWiki',
    'YandereNotes',
    'YandereUsers',
    'YandereForum',
    'YanderePools',
    'YandereFavorites',
]


class Moebooru(Booru):
    """
    Moebooru Image Board API
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class MoebooruComponent(BooruComponent):
    """
    Moebooru Image Board Component
    """

    def __init__(self, client):
        super().__init__(client)


class YandereClient(Moebooru):
    """
    Yandere API reference document: https://yande.re/help/api
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 设置发送相对 URL 请求时使用的基础 URL
        self.base_url = 'https://yande.re'

        # 初始化各组件
        self.posts = YanderePosts(self)
        # self.tags = YandereTags(self)
        # self.artists = YandereArtists(self)
        # self.comments = YandereComments(self)
        # self.wiki = YandereWiki(self)
        # self.notes = YandereNotes(self)
        # self.users = YandereUsers(self)
        # self.forum = YandereForum(self)
        self.pools = YanderePools(self)
        # self.favorites = YandereFavorites(self)

        # 登录后修改（默认无账号）

    def login(self, auth: AuthTypes):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YanderePosts(MoebooruComponent):
    """
    Posts: https://yande.re/help/api#posts
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    async def list_gt_page(
        self,
        limit: int = 40,
        tags: str = '',
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定标签帖子列表的最大页码
        
        Note: 
            对于 post 页面，返回的 html 分页器中的最大页码由于 yande.re 网站中的某些 Hidden Posts 策略（rating:e, blacklists .etc），实际的最大页码会大于等于该页码  
            yande.re post 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际帖子数量调整 html 分页器中的最大页码

        Args:
            limit (int, optional): 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇. Defaults to 40.
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站

        Returns:
            int: html 分页器中的最大页码，实际的最大页码会大于等于该页码
        """
        url = '/post'
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'limit': limit,  # 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇
            'page': 1,  # 查询页码
            'tags': tags,  # 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换
        }
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 形如 ['2', '3', '4', '5', '1067', '1068', 'Next →'] 的样式。列表中的最后一个永远为 'Next →'；由于请求的 url 中的 page 参数固定为 1，当前页码信息 1 使用 em 标签而非 a 标签，故列表若存在，则永远以 2 开头
            pagination = tree.xpath('//div[@class="pagination"]/a[@aria-label]/text()')
            if pagination:  # 存在分页器，说明该页面至少有两页
                return int(pagination[-2])
            else:  # 不存在分页器，说明该页面只有一页
                return 1
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def list(
        self,
        limit: int = 40,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        tags: str = '',
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        List
        
        The base URL is /post.xml.
        
        - limit: How many posts you want to retrieve. There is a hard limit of 100 posts per request.
        - page: The page number.
        - tags: The tags to search for. Any tag combination that works on the web site will work here. This includes all the meta-tags.

        Note: 
            修订 API 参考文件: https://yande.re/help/api: limit: How many posts you want to retrieve. There is a hard limit of 100 posts per request. 将其更改为：limit: How many posts you want to retrieve. There is a hard limit of 1000 posts per request.
        
        Return json format:
        ```
        [
            {
                "id": 1223391,
                "tags": "akiyama_mio bleed_through chibi christmas dress fixme hirasawa_yui horiguchi_yukiko k-on! kotobuki_tsumugi nakano_azusa pantyhose tainaka_ritsu",
                "created_at": 1742150809,
                "updated_at": 1742156680,
                "creator_id": 537635,
                "approver_id": null,
                "author": "Reverseshin",
                "change": 6324472,
                "source": "Animedia 2010-12",
                "score": 3,
                "md5": "3a453ffae99c4de46e4fb5bf82236842",
                "file_size": 26463488,
                "file_ext": "png",
                "file_url": "https://files.yande.re/image/3a453ffae99c4de46e4fb5bf82236842/yande.re%201223391%20akiyama_mio%20bleed_through%20chibi%20christmas%20dress%20fixme%20hirasawa_yui%20horiguchi_yukiko%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.png",
                "is_shown_in_index": false,
                "preview_url": "https://assets.yande.re/data/preview/3a/45/3a453ffae99c4de46e4fb5bf82236842.jpg",
                "preview_width": 150,
                "preview_height": 93,
                "actual_preview_width": 300,
                "actual_preview_height": 186,
                "sample_url": "https://files.yande.re/sample/3a453ffae99c4de46e4fb5bf82236842/yande.re%201223391%20sample%20akiyama_mio%20bleed_through%20chibi%20christmas%20dress%20fixme%20hirasawa_yui%20horiguchi_yukiko%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.jpg",
                "sample_width": 1500,
                "sample_height": 931,
                "sample_file_size": 481240,
                "jpeg_url": "https://files.yande.re/jpeg/3a453ffae99c4de46e4fb5bf82236842/yande.re%201223391%20akiyama_mio%20bleed_through%20chibi%20christmas%20dress%20fixme%20hirasawa_yui%20horiguchi_yukiko%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.jpg",
                "jpeg_width": 3500,
                "jpeg_height": 2172,
                "jpeg_file_size": 2088523,
                "rating": "s",
                "is_rating_locked": false,
                "has_children": false,
                "parent_id": 162305,
                "status": "active",
                "is_pending": false,
                "width": 5492,
                "height": 3408,
                "is_held": false,
                "frames_pending_string": "",
                "frames_pending": [],
                "frames_string": "",
                "frames": [],
                "is_note_locked": false,
                "last_noted_at": 0,
                "last_commented_at": 0
            },
            ...
        ]
        ```

        获取在起始页码与结束页码范围内，指定标签的帖子列表；若 all_page 为 True，则获取当前查询标签下所有页码的帖子列表
        
        Args:
            limit (int, optional): 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇. Defaults to 40.
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站
            concurrency (int, optional): 并发下载的数量. Defaults to 8.

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if limit > 1000:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 1000
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 1000.")
        url = '/post.json'
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'limit': limit,  # 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇
            'page': 1,  # 查询页码
            'tags': tags,  # 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换
        }
        # 结果列表
        result: list[dict] = []
        # 获取当前查询标签下所有页码的帖子列表
        if all_page:
            gt_page = await self.list_gt_page(  # 获取 html 分页器中的最大页码
                limit=limit,
                tags=tags,
            )
            logger.info(f"Maximum page number is greater than or equal to {gt_page} for {limit = }, {tags = }")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=gt_page,
                page_key='page',
                concurrency=concurrency,
            )

            #!仅适用于 posts 页面
            #!为防止遗漏帖子列表，回退至非并发模式获取 html 分页器中的最大页码之后的帖子列表
            logger.info(f"Fetching posts after {gt_page} page for {limit = }, {tags = }")

            # 信号量
            semaphore = asyncio.Semaphore(concurrency)
            # 当前查询页码
            page = gt_page + 1
            # 直到获取到空数据为止
            while True:
                params.update({'page': page})
                content: list[dict] = await self.client.fetch_page(
                    url,
                    headers=headers,
                    params=params,
                    semaphore=semaphore,
                )
                if content:
                    result.extend(content)
                    page += 1
                else:
                    break
        # 获取在起始页码与结束页码范围内，指定标签的帖子列表
        else:
            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=start_page,
                end_page=end_page,
                page_key='page',
                concurrency=concurrency,
            )
        return pd.DataFrame(result)

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def destroy(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert_tags(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def vote(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    async def download(
        self,
        limit: int = 40,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        tags: str = '',
        save_raws: bool = False,
        save_tags: bool = False,
        concurrency: int = 8,
    ) -> None:
        """
        下载在起始页码与结束页码范围内，指定标签的帖子列表中的帖子；若 all_page 为 True，则下载当前查询标签下所有页码的帖子列表中的帖子

        Args:
            limit (int, optional): 您想检索多少篇帖子。每次请求的帖子数量有一个硬性限制，最多 1000 篇. Defaults to 40.
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 要搜索的标签。任何在网站上有效的标签组合在这里都有效。这包括所有元标签。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站
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
            concurrency=concurrency,
        )

        if posts.empty:
            logger.info(f"All of the posts are empty.")
            return

        # 下载帖子
        urls = posts['file_url']  # 帖子 URLs
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
            tags = tags[successful_url_indices]  # 筛选后的 tags
            tags_directory = os.path.join(posts_directory, 'tags')  # 标签文件目录
            tags_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.txt')  # 标签文件名
            await self.client.concurrent_save_tags(
                tags,
                tags_directory,
                filenames=tags_filenames,
                concurrency=concurrency,
            )


class YandereTags(MoebooruComponent):
    """
    Tags: https://yande.re/help/api#tags
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def related(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereArtists(MoebooruComponent):
    """
    Artists: https://yande.re/help/api#artists
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def destroy(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereComments(MoebooruComponent):
    """
    Comments: https://yande.re/help/api#comments
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def destory(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereWiki(MoebooruComponent):
    """
    Wiki: https://yande.re/help/api#wiki
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def destroy(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def lock(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def unlock(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def history(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereNotes(MoebooruComponent):
    """
    Notes: https://yande.re/help/api#notes
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def search(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def history(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereUsers(MoebooruComponent):
    """
    Users: https://yande.re/help/api#users
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def search(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YandereForum(MoebooruComponent):
    """
    Forum: https://yande.re/help/api#forum
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class YanderePools(MoebooruComponent):
    """
    Pools: https://yande.re/help/api#pools
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    async def list_pools_page(
        self,
        query: str = '',
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定查询标题图集的最大页码
        
        Note: 
            对于 pool 页面，由于不存在 Hidden Posts 策略（rating:e, blacklists .etc），实际的最大页码会等于该页码  
            yande.re/pool 页面受 page 参数影响，但不受 limit 参数影响

        Args:
            query (str, optional): 查询标题. Defaults to ''. 表示搜索全站

        Returns:
            int: html 分页器中的最大页码，实际的最大页码等于该页码
        """
        url = '/pool'
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'query': query,  # 查询标题
            'page': 1,  # 查询页码
        }
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 形如 ['2', '3', '4', '5', '1067', '1068', 'Next →'] 的样式。列表中的最后一个永远为 'Next →'；由于请求的 url 中的 page 参数固定为 1，当前页码信息 1 使用 em 标签而非 a 标签，故列表若存在，则永远以 2 开头
            pagination = tree.xpath('//div[@class="pagination"]/a[@aria-label]/text()')
            if pagination:  # 存在分页器，说明该页面至少有两页
                return int(pagination[-2])
            else:  # 不存在分页器，说明该页面只有一页
                return 1
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def list_pools(
        self,
        query: str = '',
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        List Pools
        
        The base URL is /pool.xml. If you don't specify any parameters you'll get a list of all pools.
        
        - query: The title.
        - page: The page.
        
        Return json format:
        ```
        [
            {
                "id": 1746,
                "name": "K-ON!_-_Colorful_Memories",
                "created_at": "2010-07-18T00:21:15.758Z",
                "updated_at": "2012-03-11T19:10:27.707Z",
                "user_id": 3048,
                "is_public": true,
                "post_count": 44,
                "description": "http://kyotoanimation.shop-pro.jp/?pid=20190414"
            },
            ...
        ]
        ```
    
        获取在起始页码与结束页码范围内，指定标题的图集列表；若 all_page 为 True，则获取当前查询标题下所有页码的图集列表
        
        Note: 
            yande.re/pool 接口受 page 参数影响，但不受 limit 参数影响

        Args:
            query (str, optional): 查询标题. Defaults to ''. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标题下所有页码的图集列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.

        Returns:
            pd.DataFrame: 请求结果列表
        """
        url = '/pool.json'
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'query': query,  # 查询标题
            'page': 1,  # 查询页码
        }
        # 结果列表
        result: list[dict] = []
        # 获取当前查询标题下所有页码的图集列表
        if all_page:
            max_page = await self.list_pools_page(query=query, )  # 获取 html 分页器中的最大页码
            logger.info(f"Maximum page number is equal to {max_page} for {query = }")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=max_page,
                page_key='page',
                concurrency=concurrency,
            )
        # 获取在起始页码与结束页码范围内，指定标题的图集列表
        else:
            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=start_page,
                end_page=end_page,
                page_key='page',
                concurrency=concurrency,
            )
        return pd.DataFrame(result)

    async def list_posts(
        self,
        id: int,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        List Posts
        
        The base URL is /pool/show.xml. If you don't specify any parameters you'll get a list of all pools.
        
        Note:
            - 修订 API 参考文件: https://yande.re/help/api: If you don't specify any parameters you'll get a list of all pools. 将其更改为：If you don't specify any parameters you'll get a list of pool which id is 0.  
            - id 参数是必须的，否则访问 https://yande.re/pool/show 或 https://yande.re/pool/show.json 是会自动跳转回 https://yande.re/pool/ 页面，并弹出 Can't find pool with id 0 提示  
            - page 参数是可选的，pool/show 页面默认不以分页策略展示，所有内容均在一页中展示（忽略 limit 参数），设置为 1 即可。若需要分页策略，需点击页面最下方的 "Index View" 按钮，对于 id 为 1746 的图集，将跳转至 /post?tags=pool%3A1746 访问（即 /post?tags=pool:1746）
            - 图集支持批量下载，需点击页面最下方的 "Download" 按钮，对于 id 为 1746 的图集，将跳转至 /pool/zip/1746 访问，但需要用户登录后才能下载  
            
        - id: The pool id number.
        - page: The page.
        
        Return json format:
        ```
        {
            "id": 1746,
            "name": "K-ON!_-_Colorful_Memories",
            "created_at": "2010-07-18T00:21:15.758Z",
            "updated_at": "2012-03-11T19:10:27.707Z",
            "user_id": 3048,
            "is_public": true,
            "post_count": 44,
            "description": "http://kyotoanimation.shop-pro.jp/?pid=20190414",
            "posts": [
                {
                    "id": 145519,
                    "tags": "akiyama_mio hirasawa_yui jpeg_artifacts k-on! kotobuki_tsumugi nakano_azusa pantyhose tainaka_ritsu",
                    "created_at": "2010-07-18T00:23:44.162Z",
                    "creator_id": 17990,
                    "author": "Share",
                    "change": 650971,
                    "source": "",
                    "score": 30,
                    "md5": "841ac093c4e6de2dd13ce1fb52703da7",
                    "file_size": 1143459,
                    "file_url": "https://files.yande.re/image/841ac093c4e6de2dd13ce1fb52703da7/yande.re%20145519%20akiyama_mio%20hirasawa_yui%20jpeg_artifacts%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.jpg",
                    "is_shown_in_index": true,
                    "preview_url": "https://assets.yande.re/data/preview/84/1a/841ac093c4e6de2dd13ce1fb52703da7.jpg",
                    "preview_width": 106,
                    "preview_height": 150,
                    "actual_preview_width": 213,
                    "actual_preview_height": 300,
                    "sample_url": "https://files.yande.re/sample/841ac093c4e6de2dd13ce1fb52703da7/yande.re%20145519%20sample%20akiyama_mio%20hirasawa_yui%20jpeg_artifacts%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.jpg",
                    "sample_width": 1065,
                    "sample_height": 1500,
                    "sample_file_size": 271128,
                    "jpeg_url": "https://files.yande.re/image/841ac093c4e6de2dd13ce1fb52703da7/yande.re%20145519%20akiyama_mio%20hirasawa_yui%20jpeg_artifacts%20k-on%21%20kotobuki_tsumugi%20nakano_azusa%20pantyhose%20tainaka_ritsu.jpg",
                    "jpeg_width": 3000,
                    "jpeg_height": 4226,
                    "jpeg_file_size": 0,
                    "rating": "s",
                    "has_children": false,
                    "parent_id": null,
                    "status": "active",
                    "width": 3000,
                    "height": 4226,
                    "is_held": false,
                    "frames_pending_string": "",
                    "frames_pending": [],
                    "frames_string": "",
                    "frames": []
                },
                ...
            ]
        }
        ```
        
        获取指定 id 的图集列表

        Args:
            id (int): 图集 id
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
        Returns:
            pd.DataFrame: 请求结果列表
        """
        url = '/pool/show.json'
        headers = {
            'User-Agent': UserAgent().random,
        }
        params = {
            'id': id,  # 图集的 ID 号码
            'page': 1,  # 查询页码
        }
        # 结果列表
        result: list[dict] = await self.client.concurrent_fetch_page(
            url,
            headers=headers,
            params=params,
            start_page=1,
            end_page=1,
            page_key='page',
            concurrency=concurrency,
            callback=lambda x: x.get('posts', []),  # 获取帖子列表
        )
        return pd.DataFrame(result)

    def update_pool(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create_pool(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def destroy_pool(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def add_post(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def remove_post(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    async def download(
        self,
        query: str = '',
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        save_raws: bool = False,
        save_tags: bool = False,
        concurrency: int = 8,
    ) -> None:
        """
        下载在起始页码与结束页码范围内，指定标题的图集列表中的帖子；若 all_page 为 True，则下载当前查询标题下所有页码的图集列表中的帖子

        Args:
            query (str, optional): 查询标题. Defaults to ''. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否下载当前查询标题下所有页码的图集列表中的帖子，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            save_raws (bool, optional): 是否保存帖子 api 响应的元数据（json 格式）. Defaults to False.
            save_tags (bool, optional): 是否保存帖子标签. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
        """
        # 获取当前查询标题下所有页码的图集列表
        pools = await self.list_pools(
            query=query,
            start_page=start_page,
            end_page=end_page,
            all_page=all_page,
            concurrency=concurrency,
        )

        if pools.empty:
            logger.info(f"All of the pools are empty.")
            return

        # 图集 id
        ids = pools['id']
        # 图集名称
        names = pools['name']

        # 遍历图集列表
        for id, name in zip(ids, names):
            # 获取图集 ID 下所有帖子
            posts = await self.list_posts(
                id=id,
                concurrency=concurrency,
            )

            # 下载帖子
            urls = posts['file_url']  # 帖子 URLs
            posts_directory = os.path.join(self.directory, f'{name}')  # 帖子文件目录
            images_directory = os.path.join(posts_directory, 'images')  # 图像文件目录
            result: list[tuple[str, str]] = await self.client.concurrent_download_file(
                urls,
                images_directory,
                concurrency=concurrency,
            )

            if not result:
                logger.info(f"Downloaded 0 successful, 0 failed for pool: {name}")
                continue

            # 获取下载成功的帖子 url 以及文件路径
            successful_urls = pd.Series([res[0] for res in result if res is not None])
            successful_filepaths = pd.Series([res[1] for res in result if res is not None])
            logger.info(f"Downloaded {successful_urls.size} successful, {len(result) - successful_urls.size} failed for pool: {name}")

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
                tags = tags[successful_url_indices]  # 筛选后的 tags
                tags_directory = os.path.join(posts_directory, 'tags')  # 标签文件目录
                tags_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.txt')  # 标签文件名
                await self.client.concurrent_save_tags(
                    tags,
                    tags_directory,
                    filenames=tags_filenames,
                    concurrency=concurrency,
                )


class YandereFavorites(MoebooruComponent):
    """
    Favorites: https://yande.re/help/api#favorites
    """

    def __init__(self, client: YandereClient):
        super().__init__(client)

    def list_users(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")
