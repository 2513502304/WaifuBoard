"""
Danbooru Image Board API implementation.
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
    'Danbooru',
    'DanbooruComponent',

    # client classes
    'DanbooruClient',

    # component classes

    # Versioned Types
    'DanbooruArtists',
    'DanbooruNotes',
    'DanbooruPools',
    'DanbooruPosts',
    'DanbooruWikiPages',

    # Type Versions
    'DanbooruPostVersions',
    'DanbooruPoolVersions'

    # Non-versioned Types
    'DanbooruComments',
    'DanbooruForumPosts',
    'DanbooruTags',
    'DanbooruUsers',
]


class Danbooru(Booru):
    """
    Danbooru Image Board API
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DanbooruComponent(BooruComponent):
    """
    Danbooru Image Board Component
    """

    def __init__(self, client: Danbooru):
        super().__init__(client)


class DanbooruClient(Danbooru):
    """
    Danbooru API reference document: https://danbooru.donmai.us/wiki_pages/help%3Aapi  
      
        - Danbooru Common Search Parameters used for search endpoint: 
            - page - Returns the given page. Subject to maximum page limits (see help:users)
            - limit - The number of results to return per page. The maximum limit is 200 for /posts.json and 1000 for everything else.
            - search[id]
            - search[created_at]
            - search[updated_at]
            - search[order]=custom Returns results in the same order as given by search[id]=3,2,1.
            
    Danbooru Common URL Parameters used for navigation around the site: https://danbooru.donmai.us/wiki_pages/help%3Acommon_url_parameters  
    
    Note: 
        Danbooru 性能和安全由 Cloudflare 提供，访问该网站时，若使用常见的 User-Agent 或将 User-Agent 设置为 ''，会被拦截跳转到验证是否真人的页面，但随便给一个 User-Agent 即可避免上述情况  
        wft? 你把 python-requests/2.32.4 和 python-httpx/0.28.0 这种 UA 都通过了不让我正常的 UA 通过？还有，为什么不禁全呢，换成 python-httpx/0.28.1 版本就又不通过了？
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 设置发送相对 URL 请求时使用的基础 URL
        self.base_url = 'https://danbooru.donmai.us/'

        # 初始化各组件
        self.posts = DanbooruPosts(self)
        # self.tags = DanbooruTags(self)
        # self.artists = DanbooruArtists(self)
        # self.comments = DanbooruComments(self)
        # self.wiki_pages = DanbooruWikiPages(self)
        # self.notes = DanbooruNotes(self)
        # self.users = DanbooruUsers(self)
        # self.forum_posts = DanbooruForumPosts(self)
        self.pools = DanbooruPools(self)

        # self.post_versions = DanbooruPostVersions(self)
        # self.pool_versions = DanbooruPoolVersions(self)

        # 登录后修改（默认无账号）
        self.MAX_PAGE = 1000

    def login(self, auth: AuthTypes):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruPosts(DanbooruComponent):
    """
    Posts: https://danbooru.donmai.us/wiki_pages/api%3Aposts
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    async def index_page(
        self,
        limit: int = 20,
        tags: str = '',
        random: bool = False,
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定标签帖子列表的最大页码
        
        Note: 
            danbooru post 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际帖子数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/posts?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/posts.json?limit=1000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/logical/post_query_builder.rb:171:in 'PostQueryBuilder#paginated_posts'",
                    "app/logical/post_query.rb:86:in 'PostQuery#paginated_posts'",
                    "app/logical/post_sets/post.rb:109:in 'PostSets::Post#posts'",
                    "app/controllers/posts_controller.rb:21:in 'PostsController#index'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```
            
        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            tags (str, optional): 使用标签和元标签进行搜索的帖子查询 (Help:Cheatsheet)，post[tag] 也可以使用。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站
            random (bool, optional): 在帖子查询下选择随机抽样。若设置为 True，则仅返回 1 页内容. Defaults to False.

        Returns:
            int: html 分页器中的最大页码，实际的最大页码会大于等于该页码
        """
        # 随机抽样，仅返回 1 页内容
        if random:
            return 1
        url = '/posts'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
            'tags': tags,  # 使用标签和元标签进行搜索的帖子查询 (Help:Cheatsheet)，post[tag] 也可以使用。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换
            'random': random,  # 在帖子查询下选择随机抽样
        }
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 当前页为 span 标签，只有一页时，仅存在 span 标签；超过一页时，余下的页为 a 标签，最后一个 a 标签为下一页，倒数第二个 a 标签为最后一页（且为 hidden 属性）
            pagination = tree.xpath('//div[contains(@class, "paginator")]/a')
            if pagination:  # 存在分页器，说明该页面至少有两页
                return int(pagination[-2].xpath('./text()')[0])
            else:  # 不存在分页器，说明该页面只有一页
                return 1
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def index(
        self,
        limit: int = 20,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        tags: str = '',
        random: bool = False,
        md5: str | None = None,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        Index
        
        HTTP Method	    GET
        Base URL	    /posts.json
        Type	        read request
        Description	    The default order is ID descending
        
        Index parameters
        
        - tags - The post query to search for using tags and metatags (Help:Cheatsheet).
            - post[tags] can also be used.
        - random - Selects a random sampling under the post query.
        - format - Chooses which format to return. Can be: html, json, xml, atom.
        - md5 - Search for an MD5 match. Takes priority over all other parameters.
        
        Return json format:
        ```
        [
            {
                "id": 9755122,
                "created_at": "2025-08-07T03:06:03.772-04:00",
                "uploader_id": 434318,
                "score": 1,
                "source": "https://twitter.com/jasper_xandros/status/1946618564607115753",
                "md5": "b3c448861c60fbe54e2aa13d9d56f7bd",
                "last_comment_bumped_at": null,
                "rating": "s",
                "image_width": 1536,
                "image_height": 2048,
                "tag_string": "2girls barefoot blush breasts cloak collarbone couple english_text facial_mark halo height_difference highres hololive hololive_english jasper_xandros long_hair mori_calliope multiple_girls open_mouth takanashi_kiara virtual_youtuber",
                "fav_count": 0,
                "file_ext": "jpg",
                "last_noted_at": null,
                "parent_id": null,
                "has_children": false,
                "approver_id": null,
                "tag_count_general": 15,
                "tag_count_artist": 1,
                "tag_count_character": 2,
                "tag_count_copyright": 2,
                "file_size": 260316,
                "up_score": 1,
                "down_score": 0,
                "is_pending": false,
                "is_flagged": false,
                "is_deleted": false,
                "tag_count": 21,
                "updated_at": "2025-08-07T03:48:52.934-04:00",
                "is_banned": false,
                "pixiv_id": null,
                "last_commented_at": null,
                "has_active_children": false,
                "bit_flags": 0,
                "tag_count_meta": 1,
                "has_large": true,
                "has_visible_children": false,
                "media_asset": {
                    "id": 30919583,
                    "created_at": "2025-08-07T03:02:37.285-04:00",
                    "updated_at": "2025-08-07T03:02:38.864-04:00",
                    "md5": "b3c448861c60fbe54e2aa13d9d56f7bd",
                    "file_ext": "jpg",
                    "file_size": 260316,
                    "image_width": 1536,
                    "image_height": 2048,
                    "duration": null,
                    "status": "active",
                    "file_key": "xbUZ6jEOQ",
                    "is_public": true,
                    "pixel_hash": "05d3e53f510fed97e4c4f3854a013ffc",
                    "variants": [
                        {
                            "type": "180x180",
                            "url": "https://cdn.donmai.us/180x180/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                            "width": 135,
                            "height": 180,
                            "file_ext": "jpg"
                        },
                        {
                            "type": "360x360",
                            "url": "https://cdn.donmai.us/360x360/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                            "width": 270,
                            "height": 360,
                            "file_ext": "jpg"
                        },
                        {
                            "type": "720x720",
                            "url": "https://cdn.donmai.us/720x720/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.webp",
                            "width": 540,
                            "height": 720,
                            "file_ext": "webp"
                        },
                        {
                            "type": "sample",
                            "url": "https://cdn.donmai.us/sample/b3/c4/sample-b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                            "width": 850,
                            "height": 1133,
                            "file_ext": "jpg"
                        },
                        {
                            "type": "original",
                            "url": "https://cdn.donmai.us/original/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                            "width": 1536,
                            "height": 2048,
                            "file_ext": "jpg"
                        }
                    ]
                },
                "tag_string_general": "2girls barefoot blush breasts cloak collarbone couple english_text facial_mark halo height_difference long_hair multiple_girls open_mouth virtual_youtuber",
                "tag_string_character": "mori_calliope takanashi_kiara",
                "tag_string_copyright": "hololive hololive_english",
                "tag_string_artist": "jasper_xandros",
                "tag_string_meta": "highres",
                "file_url": "https://cdn.donmai.us/original/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                "large_file_url": "https://cdn.donmai.us/sample/b3/c4/sample-b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                "preview_file_url": "https://cdn.donmai.us/180x180/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg"
            },
            ...
        ]
        ```

        获取在起始页码与结束页码范围内，指定标签的帖子列表；若 all_page 为 True，则获取当前查询标签下所有页码的帖子列表
        
        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 使用标签和元标签进行搜索的帖子查询 (Help:Cheatsheet)，post[tag] 也可以使用。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站
            random (bool, optional): 在帖子查询下选择随机抽样。若设置为 True，则仅返回 1 页内容. Defaults to False.
            md5 (str, optional): 搜索 MD5 匹配项。优先于所有其他参数. Defaults to ''.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
        Note: 
            danbooru post 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际帖子数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/posts?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/posts.json?limit=1000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/logical/post_query_builder.rb:171:in 'PostQueryBuilder#paginated_posts'",
                    "app/logical/post_query.rb:86:in 'PostQuery#paginated_posts'",
                    "app/logical/post_sets/post.rb:109:in 'PostSets::Post#posts'",
                    "app/controllers/posts_controller.rb:21:in 'PostsController#index'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if limit > 200:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 200
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 200.")
        url = '/posts.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
            'tags': tags,  # 使用标签和元标签进行搜索的帖子查询 (Help:Cheatsheet)，post[tag] 也可以使用。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换
            'random': random,  # 在帖子查询下选择随机抽样
            'md5': md5,  # 搜索 MD5 匹配项。优先于所有其他参数
        }
        # 结果列表
        result: list[dict] = []
        if md5 is not None:
            result = await self.client.concurrent_fetch_page(  # danbooru 在搜索 md5 时，返回的结果列表仅包含一个帖子
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=1,
                page_key='page',
                concurrency=1,
            )
            return pd.DataFrame(result)
        # TODO: 添加检验用户账号等级逻辑，以便调整每次搜索的最大页数
        self.client.MAX_PAGE
        # 获取当前查询标签下所有页码的帖子列表
        if all_page:
            max_page = await self.index_page(  # 获取 html 分页器中的最大页码
                limit=limit,
                tags=tags,
                random=random,
            )
            logger.info(f"Maximum page number is equal to {max_page} for {limit = }, {tags = }, {random = }")

            if max_page > self.client.MAX_PAGE:
                remain_page = max_page - self.client.MAX_PAGE
                max_page = self.client.MAX_PAGE  # 限制最大页码不超过每次搜索的最大页数
                logger.warning(
                    f"Maximum page number is set to {max_page} for {limit = }, {tags = }, {random = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=max_page,
                page_key='page',
                concurrency=concurrency,
            )
        # 获取在起始页码与结束页码范围内，指定标签的帖子列表
        else:
            if start_page > self.client.MAX_PAGE:
                remain_page = start_page - self.client.MAX_PAGE
                start_page = self.client.MAX_PAGE
                logger.warning(
                    f"Start page is set to {start_page} for {limit = }, {tags = }, {random = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")
            if end_page > self.client.MAX_PAGE:
                remain_page = end_page - self.client.MAX_PAGE
                end_page = self.client.MAX_PAGE
                logger.warning(f"End page is set to {end_page} for {limit = }, {tags = }, {random = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

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

    async def show(
        self,
        id: int,
        semaphore: asyncio.Semaphore,
    ) -> pd.DataFrame:
        """
        Show
        
        HTTP Method	    GET
        Base URL	    /posts/$id.json
        Type	        read request
        Description	    $id is the post ID
        
        Return json format:
        ```
        {
            "id": 9755122,
            "created_at": "2025-08-07T03:06:03.772-04:00",
            "uploader_id": 434318,
            "score": 3,
            "source": "https://twitter.com/jasper_xandros/status/1946618564607115753",
            "md5": "b3c448861c60fbe54e2aa13d9d56f7bd",
            "last_comment_bumped_at": null,
            "rating": "s",
            "image_width": 1536,
            "image_height": 2048,
            "tag_string": "2girls barefoot blush breasts cloak collarbone couple english_text facial_mark halo height_difference highres hololive hololive_english jasper_xandros long_hair mori_calliope multiple_girls open_mouth takanashi_kiara virtual_youtuber",
            "fav_count": 2,
            "file_ext": "jpg",
            "last_noted_at": null,
            "parent_id": null,
            "has_children": false,
            "approver_id": null,
            "tag_count_general": 15,
            "tag_count_artist": 1,
            "tag_count_character": 2,
            "tag_count_copyright": 2,
            "file_size": 260316,
            "up_score": 3,
            "down_score": 0,
            "is_pending": false,
            "is_flagged": false,
            "is_deleted": false,
            "tag_count": 21,
            "updated_at": "2025-08-07T03:48:52.934-04:00",
            "is_banned": false,
            "pixiv_id": null,
            "last_commented_at": null,
            "has_active_children": false,
            "bit_flags": 0,
            "tag_count_meta": 1,
            "has_large": true,
            "has_visible_children": false,
            "media_asset": {
                "id": 30919583,
                "created_at": "2025-08-07T03:02:37.285-04:00",
                "updated_at": "2025-08-07T03:02:38.864-04:00",
                "md5": "b3c448861c60fbe54e2aa13d9d56f7bd",
                "file_ext": "jpg",
                "file_size": 260316,
                "image_width": 1536,
                "image_height": 2048,
                "duration": null,
                "status": "active",
                "file_key": "xbUZ6jEOQ",
                "is_public": true,
                "pixel_hash": "05d3e53f510fed97e4c4f3854a013ffc",
                "variants": [
                    {
                        "type": "180x180",
                        "url": "https://cdn.donmai.us/180x180/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                        "width": 135,
                        "height": 180,
                        "file_ext": "jpg"
                    },
                    {
                        "type": "360x360",
                        "url": "https://cdn.donmai.us/360x360/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                        "width": 270,
                        "height": 360,
                        "file_ext": "jpg"
                    },
                    {
                        "type": "720x720",
                        "url": "https://cdn.donmai.us/720x720/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.webp",
                        "width": 540,
                        "height": 720,
                        "file_ext": "webp"
                    },
                    {
                        "type": "sample",
                        "url": "https://cdn.donmai.us/sample/b3/c4/sample-b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                        "width": 850,
                        "height": 1133,
                        "file_ext": "jpg"
                    },
                    {
                        "type": "original",
                        "url": "https://cdn.donmai.us/original/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
                        "width": 1536,
                        "height": 2048,
                        "file_ext": "jpg"
                    }
                ]
            },
            "tag_string_general": "2girls barefoot blush breasts cloak collarbone couple english_text facial_mark halo height_difference long_hair multiple_girls open_mouth virtual_youtuber",
            "tag_string_character": "mori_calliope takanashi_kiara",
            "tag_string_copyright": "hololive hololive_english",
            "tag_string_artist": "jasper_xandros",
            "tag_string_meta": "highres",
            "file_url": "https://cdn.donmai.us/original/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
            "large_file_url": "https://cdn.donmai.us/sample/b3/c4/sample-b3c448861c60fbe54e2aa13d9d56f7bd.jpg",
            "preview_file_url": "https://cdn.donmai.us/180x180/b3/c4/b3c448861c60fbe54e2aa13d9d56f7bd.jpg"
        }
        ```
        
        获取指定 id 的帖子列表
        
        Args:
            id (int): 帖子 id
            semaphore (asyncio.Semaphore): 信号量，用于控制并发下载的数量
            
        Note:
            该方法与指定 md5 参数的 index 方法类似，**但是**返回的结果列表仅包含一个帖子
            
        Returns:
            pd.DataFrame: 请求结果列表
        """
        url = f'/posts/{id}.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {}
        # 结果列表
        result = await self.client.fetch_page(  # danbooru 在搜索 id 时，返回的结果列表仅包含一个帖子
            url,
            headers=headers,
            params=params,
            semaphore=semaphore,
        )
        return pd.DataFrame(result)

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    async def download(
        self,
        limit: int = 20,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        tags: str = '',
        random: bool = False,
        md5: str | None = None,
        save_raws: bool = False,
        save_tags: bool = False,
        concurrency: int = 8,
    ) -> None:
        """
        下载在起始页码与结束页码范围内，指定标签的帖子列表中的帖子；若 all_page 为 True，则下载当前查询标签下所有页码的帖子列表中的帖子

        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前查询标签下所有页码的帖子列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            tags (str, optional): 使用标签和元标签进行搜索的帖子查询 (Help:Cheatsheet)，post[tag] 也可以使用。要组合的不同标签使用空格连接，同一标签中的空格使用 _ 替换. Defaults to ''. 表示搜索全站
            random (bool, optional): 在帖子查询下选择随机抽样。若设置为 True，则仅返回 1 页内容. Defaults to False.
            md5 (str, optional): 搜索 MD5 匹配项。优先于所有其他参数. Defaults to ''.
            save_raws (bool, optional): 是否保存帖子 api 响应的元数据（json 格式）. Defaults to False.
            save_tags (bool, optional): 是否保存帖子标签. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
        """
        # 获取当前查询标签下所有页码的帖子列表中的帖子
        posts = await self.index(
            limit=limit,
            start_page=start_page,
            end_page=end_page,
            all_page=all_page,
            tags=tags,
            random=random,
            md5=md5,
            concurrency=concurrency,
        )

        if posts.empty:
            logger.info(f"All of the posts are empty.")
            return

        # 下载帖子
        urls = posts['file_url']  # 帖子 URLs
        if md5 is not None:  # 存储文件目录
            posts_directory = os.path.join(self.directory, f'{md5}')  # 帖子文件目录
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
            tags = posts['tag_string']
            # 保存标签
            tags = tags[successful_url_indices]  # 筛选后的 tags
            tags_directory = os.path.join(posts_directory, 'tags')  # 标签文件目录
            tags_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.txt')  # 标签文件名
            await self.client.concurrent_save_tags(
                tags,
                tags_directory,
                filenames=tags_filenames,
                concurrency=concurrency,
            )


class DanbooruTags(DanbooruComponent):
    """
    Tags: https://danbooru.donmai.us/wiki_pages/api%3Atags
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def related(self, ):
        # see Other Functions section: https://danbooru.donmai.us/wiki_pages/help%3Aapi
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruArtists(DanbooruComponent):
    """
    Artists: https://danbooru.donmai.us/wiki_pages/api%3Aartists
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def banned(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def ban(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def unban(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruComments(DanbooruComponent):
    """
    Comments: https://danbooru.donmai.us/wiki_pages/api%3Acomments
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def undelete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruWikiPages(DanbooruComponent):
    """
    Wiki: https://danbooru.donmai.us/wiki_pages/api%3Awiki_pages
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
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

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruNotes(DanbooruComponent):
    """
    Notes: https://danbooru.donmai.us/wiki_pages/api%3Anotes
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruUsers(DanbooruComponent):
    """
    Users: https://danbooru.donmai.us/wiki_pages/api%3Ausers
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruForumPosts(DanbooruComments):
    """
    Forum Posts: https://danbooru.donmai.us/wiki_pages/api%3Aforum_posts
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    def index(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def show(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def undelete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")


class DanbooruPools(DanbooruComponent):
    """
    Pools: https://danbooru.donmai.us/wiki_pages/api%3Apools
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    async def index_page(
        self,
        limit: int = 20,
        query: dict | None = None,
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定搜索参数图集的最大页码

        Note: 
            danbooru pool 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际图集数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/pools?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/pools.json?limit=1000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/pools_controller.rb:20:in 'PoolsController#index'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:pools](https://danbooru.donmai.us/wiki_pages/api%3Apools). Defaults to None. 表示搜索全站

        Returns:
            int: html 分页器中的最大页码，实际的最大页码等于该页码
        """
        if query is None:
            query = {}
        url = '/pools'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 当前页为 span 标签，只有一页时，仅存在 span 标签；超过一页时，余下的页为 a 标签，最后一个 a 标签为下一页，倒数第二个 a 标签为最后一页（且为 hidden 属性）
            #!若访问 pools/gallery 页面，则无法获取最后一页页码（原网页中唯独缺少了该页码的 a 标签）
            #!但由于 pools/gallery 与 pools 内容一致，因此可以间接从 pools 页面中获取最后一页页码
            pagination = tree.xpath('//div[contains(@class, "paginator")]/a')
            if pagination:  # 存在分页器，说明该页面至少有两页
                return int(pagination[-2].xpath('./text()')[0])
            else:  # 不存在分页器，说明该页面只有一页
                return 1
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def index(
        self,
        limit: int = 20,
        query: dict | None = None,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        Index
        
        HTTP Method	    GET
        Base URL	    /pools.json
        Type	        read request
        Description	    The default order is updated at descending.
        
        Search attributes
        
        All of the following are standard attributes with all of their available formats and qualifiers.

        - Number syntax
            - id
            - created_at
            - updated_at
            
        - Text syntax
            - name
            - description
            
        - Array syntax
            - post_ids
        
        - Boolean syntax
            - is_deleted
            
        Special search parameters
        
        - name_matches - Normalized case-insensitive wildcard searching on the name text field.
        - description_matches - Case-insensitive wildcard searching on the description text field.
        - post_tags_match - The pools's post's tags match the given terms. Meta-tags not supported.
        - name_contains - same as name_matches. (A)
        
        - category
            - series - Only series-type pools.
            - collection - Only collection-type pools.
        - order - Sets the order of the results.
            - updated_at - Orders by last updated time. (default order) (M)
            - name - Alphabetic order by name.
            - created_at - Orders by creation time.
            - post_count - Orders by post count.

        Return json format:
        ```
        [
            {
                "id": 25767,
                "name": "Girls_Band_Cry_and_Various_-_100_Days_Challenge_(yun_cao_bing)",
                "created_at": "2025-05-20T01:23:26.659-04:00",
                "updated_at": "2025-08-07T11:45:41.889-04:00",
                "description": "Artist: [[yun_cao_bing]]\r\nOrdered by the datetime of posting (on bilibili). Earliest post first.",
                "is_active": true,
                "is_deleted": false,
                "post_ids": [9010243, 9010244, 9014311, 9019533, 9020707, 9035262, 9035264, 9035294, 9035296, 9040170, 9042951, 9048727, 9049726, 9052845, 9057388, 9058039, 9062619, 9063534, 9067162, 9069531, 9072575, 9074083, 9078824, 9082553, 9082831, 9084833, 9087985, 9093112, 9096829, 9099633, 9104261, 9105053, 9114809, 9114797, 9115504, 9119794, 9124730, 9129614, 9134214, 9139246, 9145623, 9146696, 9150634, 9158009, 9160080, 9160892, 9170450, 9170455, 9177628, 9176669, 9186695, 9190286, 9191692, 9192507, 9197299, 9197872, 9203217, 9207738, 9207746, 9213064, 9213191, 9220683, 9218351, 9217986, 9222382, 9230860, 9231573, 9231583, 9236637, 9241311, 9241294, 9246839, 9254557, 9254558, 9257069, 9262469, 9335067, 9276436, 9278466, 9283178, 9335084, 9295031, 9303663, 9335100, 9305054, 9311246, 9314899, 9315618, 9320750, 9329180, 9340814, 9341231, 9341451, 9351622, 9351626, 9356375, 9357906, 9361750, 9361755, 9366438, 9367126, 9376322, 9376326, 9380890, 9382704, 9386440, 9387665, 9390664, 9391322, 9391384, 9403709, 9403713, 9404616, 9408321, 9414354, 9414697, 9419024, 9429320, 9430577, 9430591, 9436293, 9442566, 9447301, 9452519, 9457358, 9466457, 9468799, 9474177, 9474970, 9479506, 9485999, 9492681, 9493639, 9498176, 9503207, 9503208, 9508829, 9508835, 9514749, 9514753, 9520176, 9520180, 9539389, 9539394, 9542784, 9542816, 9551233, 9562986, 9584331, 9589851, 9590826, 9607135, 9678528, 9678538, 9694827, 9701048, 9709089, 9720352, 9729953, 9741358, 9746778, 9756829],
                "category": "series",
                "post_count": 162
            },
            ...
        ]
        ```

        获取在起始页码与结束页码范围内，指定搜索参数的图集列表；若 all_page 为 True，则获取当前搜索参数下所有页码的图集列表

        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:pools](https://danbooru.donmai.us/wiki_pages/api%3Apools). Defaults to None. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前搜索参数下所有页码的图集列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
        Note: 
            danbooru pool 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际图集数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/pools?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/pools.json?limit=1000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/pools_controller.rb:20:in 'PoolsController#index'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if query is None:
            query = {}
        if limit > 1000:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 1000
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 1000.")
        url = '/pools.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        # 结果列表
        result: list[dict] = []
        # TODO: 添加检验用户账号等级逻辑，以便调整每次搜索的最大页数
        self.client.MAX_PAGE
        # 获取当前搜索参数下所有页码的图集列表
        if all_page:
            max_page = await self.index_page(  # 获取 html 分页器中的最大页码
                limit=limit,
                query=query,
            )
            logger.info(f"Maximum page number is equal to {max_page} for {limit = }, {query = }")

            if max_page > self.client.MAX_PAGE:
                remain_page = max_page - self.client.MAX_PAGE
                max_page = self.client.MAX_PAGE  # 限制最大页码不超过每次搜索的最大页数
                logger.warning(f"Maximum page number is set to {max_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

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
            if start_page > self.client.MAX_PAGE:
                remain_page = start_page - self.client.MAX_PAGE
                start_page = self.client.MAX_PAGE
                logger.warning(f"Start page is set to {start_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")
            if end_page > self.client.MAX_PAGE:
                remain_page = end_page - self.client.MAX_PAGE
                end_page = self.client.MAX_PAGE
                logger.warning(f"End page is set to {end_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

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

    async def show(
        self,
        id: int,
        semaphore: asyncio.Semaphore,
    ) -> pd.DataFrame:
        """
        Show
        
        HTTP Method	    GET
        Base URL	    /pools/$id.json
        Type	        read request
        Description	    $id is the pool ID
        
        Return json format:
        ```
        {
            "id": 25767,
            "name": "Girls_Band_Cry_and_Various_-_100_Days_Challenge_(yun_cao_bing)",
            "created_at": "2025-05-20T01:23:26.659-04:00",
            "updated_at": "2025-08-07T11:45:41.889-04:00",
            "description": "Artist: [[yun_cao_bing]]\r\nOrdered by the datetime of posting (on bilibili). Earliest post first.",
            "is_active": true,
            "is_deleted": false,
            "post_ids": [9010243, 9010244, 9014311, 9019533, 9020707, 9035262, 9035264, 9035294, 9035296, 9040170, 9042951, 9048727, 9049726, 9052845, 9057388, 9058039, 9062619, 9063534, 9067162, 9069531, 9072575, 9074083, 9078824, 9082553, 9082831, 9084833, 9087985, 9093112, 9096829, 9099633, 9104261, 9105053, 9114809, 9114797, 9115504, 9119794, 9124730, 9129614, 9134214, 9139246, 9145623, 9146696, 9150634, 9158009, 9160080, 9160892, 9170450, 9170455, 9177628, 9176669, 9186695, 9190286, 9191692, 9192507, 9197299, 9197872, 9203217, 9207738, 9207746, 9213064, 9213191, 9220683, 9218351, 9217986, 9222382, 9230860, 9231573, 9231583, 9236637, 9241311, 9241294, 9246839, 9254557, 9254558, 9257069, 9262469, 9335067, 9276436, 9278466, 9283178, 9335084, 9295031, 9303663, 9335100, 9305054, 9311246, 9314899, 9315618, 9320750, 9329180, 9340814, 9341231, 9341451, 9351622, 9351626, 9356375, 9357906, 9361750, 9361755, 9366438, 9367126, 9376322, 9376326, 9380890, 9382704, 9386440, 9387665, 9390664, 9391322, 9391384, 9403709, 9403713, 9404616, 9408321, 9414354, 9414697, 9419024, 9429320, 9430577, 9430591, 9436293, 9442566, 9447301, 9452519, 9457358, 9466457, 9468799, 9474177, 9474970, 9479506, 9485999, 9492681, 9493639, 9498176, 9503207, 9503208, 9508829, 9508835, 9514749, 9514753, 9520176, 9520180, 9539389, 9539394, 9542784, 9542816, 9551233, 9562986, 9584331, 9589851, 9590826, 9607135, 9678528, 9678538, 9694827, 9701048, 9709089, 9720352, 9729953, 9741358, 9746778, 9756829],
            "category": "series",
            "post_count": 162
        }
        ```
        
        获取指定 id 的图集列表
        
        Args:
            id (int): 图集 id
            semaphore (asyncio.Semaphore): 信号量，用于控制并发下载的数量
            
        Note:
            该方法与指定 search[id]/search[name] 参数的 index 方法类似，**但是**返回的结果列表仅包含一个图集

        Returns:
            pd.DataFrame: 请求结果列表
        """
        url = f'/pools/{id}.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {}
        # 结果列表
        result: list[dict] = await self.client.fetch_page(  # danbooru 在搜索 id 时，返回的结果列表仅包含一个图集
            url,
            headers=headers,
            params=params,
            semaphore=semaphore,
        )
        return pd.DataFrame(result)

    def create(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def update(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def delete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def undelete(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    def revert(self, ):
        # TODO
        raise NotImplementedError("The method is not implemented")

    async def download(
        self,
        limit: int = 20,
        query: dict | None = None,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        save_raws: bool = False,
        save_tags: bool = False,
        concurrency: int = 8,
    ) -> None:
        """
        下载在起始页码与结束页码范围内，指定搜索参数的图集列表中的帖子；若 all_page 为 True，则下载当前搜索参数下所有页码的图集列表中的帖子

        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:pools](https://danbooru.donmai.us/wiki_pages/api%3Apools). Defaults to None. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否下载当前搜索参数下所有页码的图集列表中的帖子，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            save_raws (bool, optional): 是否保存帖子 api 响应的元数据（json 格式）. Defaults to False.
            save_tags (bool, optional): 是否保存帖子标签. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
        """
        if query is None:
            query = {}
        # 获取当前搜索参数下所有页码的图集列表
        pools = await self.index(
            limit=limit,
            query=query,
            start_page=start_page,
            end_page=end_page,
            all_page=all_page,
            concurrency=concurrency,
        )

        # 过滤空图集
        empty_mask = pools['post_count'] == 0  # danbooru 中可能存在空图集
        if not empty_mask.empty:
            empty_pools = pools[empty_mask]
            logger.info(f"Found {len(empty_pools)} empty pools, which will be ignored. Empty pools: {empty_pools['name'].to_list()}")
            pools = pools[~empty_mask]

        if pools.empty:
            logger.info(f"All of the pools are empty.")
            return

        # 图集中的帖子 id 列表
        post_ids = pools['post_ids']
        # 图集名称
        names = pools['name']

        # 遍历图集列表
        for ids, name in zip(post_ids, names):
            # 信号量
            semaphore = asyncio.Semaphore(concurrency)
            # 异步任务列表
            tasks = [self.client.posts.show(id=id, semaphore=semaphore) for id in ids]  # 委托给 DanbooruPosts 类的 show 方法以获得单个 id 下的帖子
            # 并发获取图集 ID 下所有帖子
            task_result: list[pd.DataFrame] = await asyncio.gather(*tasks, return_exceptions=True)
            # 合并所有帖子
            posts = pd.concat(task_result, axis=0, join='outer', ignore_index=True)

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
                tags = posts['tag_string']
                # 保存标签
                tags = tags[successful_url_indices]  # 筛选后的 tags
                tags_directory = os.path.join(posts_directory, 'tags')  # 标签文件目录
                tags_filenames = successful_filepaths.apply(lambda x: os.path.splitext(os.path.basename(x))[0] + '.txt')  # 标签文件名
                await self.client.concurrent_save_tags(
                    tags,
                    tags_directory,
                    filenames=tags_filenames,
                    concurrency=concurrency,
                )


class DanbooruPostVersions(DanbooruComponent):
    """
    Post Version: https://danbooru.donmai.us/wiki_pages/api%3Apost_versions
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    async def index_page(
        self,
        limit: int = 20,
        query: dict | None = None,
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定搜索参数帖子版本的最大页码

        Note: 
            danbooru post history 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际帖子数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/post_versions?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/post_versions.json?limit=10000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/post_versions_controller.rb:11:in 'PostVersionsController#index'",
                    "app/controllers/post_versions_controller.rb:37:in 'PostVersionsController#set_timeout'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```
            
        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:pools](https://danbooru.donmai.us/wiki_pages/api%3Apools). Defaults to None. 表示搜索全站

        Returns:
            int: html 分页器中的最大页码，实际的最大页码等于该页码
        """
        if query is None:
            query = {}
        url = '/post_versions'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        #!当前 post_versions 页码，无法获取最后一页页码（原网页中唯独缺少了该页码的 a 标签）
        try:
            return self.client.MAX_PAGE  # 暂未实现，返回最大页数（超出最大页数的请求返回为空，不会影响最后数据获取的总量，但会延长程序运行的时间）

            #!very slowly way, start with page 1
            current_page = 1
            #!first request, check pagination is exist or not
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 当前页为 span 标签，只有一页时，仅存在 span 标签；超过一页时，余下的页为 a 标签，最后一个 a 标签为下一页，倒数第二个 a 标签为当前页向后 +4 页或最后一页
            pagination = tree.xpath('//div[contains(@class, "paginator")]/a')
            if pagination:  # 存在分页器，说明该页面至少有两页
                current_page = int(pagination[-2].xpath('./text()')[0])
            else:  # 不存在分页器，说明该页面只有一页
                return 1

            # TODO 改变 xpath 表达式（并非从第一页开始）
            #!gap some page
            params['page'] = gap_page = current_page
            while gap_page < 1000:
                current_page = gap_page
                response = await self.client.get(url, headers=headers, params=params)
                response.raise_for_status()
                # 解析 html 分页器中的最大页码
                tree = etree.HTML(response.text)
                # TODO
                pagination = tree.xpath('???')
                if pagination:  # 存在分页器，说明该页面至少有两页
                    gap_page  # TODO update gap_page
                    params['page'] = gap_page
                else:
                    pass

            # TODO 改变 xpath 表达式（并非从第一页开始）
            #!slow down, check any remain page is exist or not
            for i in range(current_page, 1000 + 1):  # (1 + 4 * 249) = 997, range in [997, 1000]
                params['page'] = i
                response = await self.client.get(url, headers=headers, params=params)
                response.raise_for_status()
                # 解析 html 分页器中的最大页码
                tree = etree.HTML(response.text)
                # TODO
                pagination = tree.xpath('???')
                if pagination:  # 存在分页器，说明该页面至少有两页
                    pass
                else:  # 不存在分页器，说明该页面只有一页
                    pass
            return current_page
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def index(
        self,
        limit: int = 20,
        query: dict | None = None,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        Index
        
        HTTP Method	    GET
        Base URL	    /post_versions.json
        Type	        read request
        Description	    The default order is ID descending.
        
        Search attributes
        
        All of the following are standard attributes with all of their available formats and qualifiers.

        - Number syntax
            - id
            - post_id
            - parent_id
            - updater_id
            - version
            - created_at
            - updated_at
        
        - Text syntax
            - tags
            - rating
            - source
        
        - Boolean syntax
            - rating_changed
            - parent_changed
            - source_changed
        
        - Array syntax
            - added_tags
            - removed_tags
        
        Special search parameters
        
        - changed_tags - Search where all tags must be either an added tag or removed tag
            - The list of tags is space-delineated
        - all_changed_tags - The same as changed_tags
        - changed_tags - Search where at least one tag must be either an added tag or removed tag
            - The list of tags is space-delineated
        - tag_matches - Case-insensitive search of the tag string with first tag from the input
            - If asterisks ( * ) are missing from the input, it adds an asterisk to either side of the tag
        - updater_name - Searches by updater name instead of updater ID
        - is_new - Boolean syntax
            - Shorthand search for version=1 or version=>1

        Return json format:
        ```
        [
            {
                "id": 9755122,
                "post_id": 1382152,
                "tags": "/\\/\\/\\ 1boy 1girl ? breasts chinese cleavage comic forehead_jewel forehead_protector highres kumiko_(aleron) large_breasts league_of_legends monochrome navel popped_collar staff sweatdrop swimsuit talon_(league_of_legends) thighhighs translation_request trembling",
                "added_tags": [],
                "removed_tags": [
                    "sling_bikini"
                ],
                "updater_id": 23449,
                "updated_at": "2013-03-24T20:11:07.925-04:00",
                "rating": "q",
                "rating_changed": false,
                "parent_id": null,
                "parent_changed": false,
                "source": "http://i1.pixiv.net/img79/img/allan9706/34464563_big_p29.jpg",
                "source_changed": false,
                "version": 5,
                "obsolete_added_tags": "",
                "obsolete_removed_tags": "",
                "unchanged_tags": "/\\/\\/\\ 1boy 1girl ? breasts chinese cleavage comic forehead_jewel forehead_protector highres kumiko_(aleron) large_breasts league_of_legends monochrome navel popped_collar staff sweatdrop swimsuit talon_(league_of_legends) thighhighs translation_request trembling"
            }
        ]
        ```

        获取在起始页码与结束页码范围内，指定搜索参数的帖子版本列表；若 all_page 为 True，则获取当前搜索参数下所有页码的帖子版本列表

        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:post versions](https://danbooru.donmai.us/wiki_pages/api%3Apost_versions). Defaults to None. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前搜索参数下所有页码的帖子版本列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
        Note: 
            danbooru post history 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际帖子版本数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/post_versions?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/post_versions.json?limit=10000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/post_versions_controller.rb:11:in 'PostVersionsController#index'",
                    "app/controllers/post_versions_controller.rb:37:in 'PostVersionsController#set_timeout'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if query is None:
            query = {}
        if limit > 1000:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 1000
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 1000.")
        url = '/post_versions.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        # 结果列表
        result: list[dict] = []
        # TODO: 添加检验用户账号等级逻辑，以便调整每次搜索的最大页数
        self.client.MAX_PAGE
        # 获取当前搜索参数下所有页码的帖子版本列表
        if all_page:
            max_page = await self.index_page(  # 获取 html 分页器中的最大页码
                limit=limit,
                query=query,
            )
            logger.info(f"Maximum page number is equal to {max_page} for {limit = }, {query = }")

            if max_page > self.client.MAX_PAGE:
                remain_page = max_page - self.client.MAX_PAGE
                max_page = self.client.MAX_PAGE  # 限制最大页码不超过每次搜索的最大页数
                logger.warning(f"Maximum page number is set to {max_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=max_page,
                page_key='page',
                concurrency=concurrency,
            )
        # 获取在起始页码与结束页码范围内，指定标题的帖子版本列表
        else:
            if start_page > self.client.MAX_PAGE:
                remain_page = start_page - self.client.MAX_PAGE
                start_page = self.client.MAX_PAGE
                logger.warning(f"Start page is set to {start_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")
            if end_page > self.client.MAX_PAGE:
                remain_page = end_page - self.client.MAX_PAGE
                end_page = self.client.MAX_PAGE
                logger.warning(f"End page is set to {end_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

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


class DanbooruPoolVersions(DanbooruComponent):
    """
    Pools: https://danbooru.donmai.us/wiki_pages/api%3Apool_versions
    """

    def __init__(self, client: DanbooruClient):
        super().__init__(client)

    async def index_page(
        self,
        limit: int = 20,
        query: dict | None = None,
    ) -> int:
        """
        使用定位 html 分页器的方式，获取指定搜索参数图集版本的最大页码

        Note: 
            danbooru pool history 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际图集版本数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/pool_versions?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/pool_versions.json?limit=10000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/pool_versions_controller.rb:10:in 'PoolVersionsController#index'",
                    "app/controllers/pool_versions_controller.rb:35:in 'PoolVersionsController#set_timeout'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```
            
        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:pools](https://danbooru.donmai.us/wiki_pages/api%3Apools). Defaults to None. 表示搜索全站

        Returns:
            int: html 分页器中的最大页码，实际的最大页码等于该页码
        """
        if query is None:
            query = {}
        url = '/pool_versions'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        #!当前 pool_versions 页码，无法获取最后一页页码（原网页中唯独缺少了该页码的 a 标签）
        try:
            return self.client.MAX_PAGE  # 暂未实现，返回最大页数（超出最大页数的请求返回为空，不会影响最后数据获取的总量，但会延长程序运行的时间）

            #!very slowly way, start with page 1
            current_page = 1
            #!first request, check pagination is exist or not
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            # 解析 html 分页器中的最大页码
            tree = etree.HTML(response.text)
            # 当前页为 span 标签，只有一页时，仅存在 span 标签；超过一页时，余下的页为 a 标签，最后一个 a 标签为下一页，倒数第二个 a 标签为当前页向后 +4 页或最后一页
            pagination = tree.xpath('//div[contains(@class, "paginator")]/a')
            if pagination:  # 存在分页器，说明该页面至少有两页
                current_page = int(pagination[-2].xpath('./text()')[0])
            else:  # 不存在分页器，说明该页面只有一页
                return 1

            # TODO 改变 xpath 表达式（并非从第一页开始）
            #!gap some page
            params['page'] = gap_page = current_page
            while gap_page < 1000:
                current_page = gap_page
                response = await self.client.get(url, headers=headers, params=params)
                response.raise_for_status()
                # 解析 html 分页器中的最大页码
                tree = etree.HTML(response.text)
                # TODO
                pagination = tree.xpath('???')
                if pagination:  # 存在分页器，说明该页面至少有两页
                    gap_page  # TODO update gap_page
                    params['page'] = gap_page
                else:
                    pass

            # TODO 改变 xpath 表达式（并非从第一页开始）
            #!slow down, check any remain page is exist or not
            for i in range(current_page, 1000 + 1):  # (1 + 4 * 249) = 997, range in [997, 1000]
                params['page'] = i
                response = await self.client.get(url, headers=headers, params=params)
                response.raise_for_status()
                # 解析 html 分页器中的最大页码
                tree = etree.HTML(response.text)
                # TODO
                pagination = tree.xpath('???')
                if pagination:  # 存在分页器，说明该页面至少有两页
                    pass
                else:  # 不存在分页器，说明该页面只有一页
                    pass
            return current_page
        except httpx.HTTPError as exc:
            logger.error(f"{exc.__class__.__name__} for {exc.request.url} - {exc}")

    async def index_version(
        self,
        limit: int = 20,
        query: dict | None = None,
        start_page: int = 1,
        end_page: int = 1,
        all_page: bool = False,
        concurrency: int = 8,
    ) -> pd.DataFrame:
        """
        Index
        
        HTTP Method	    GET
        Base URL	    /pool_versions.json
        Type	        read request
        Description	    The default order is ID descending.
        
        Search attributes
        
        All of the following are standard attributes with all of their available formats and qualifiers.
        
        - Number syntax
            - id
            - pool_id
            - updater_id
            - version
            - created_at
            - updated_at
        
        - Text syntax
            - name
            - description
            - category
        
        - Boolean syntax
            - name_changed
            - description_changed
            - is_active
            - is_deleted
        
        - Array syntax
            - post_ids
            - added_post_ids
            - removed_post_ids
        
        Special search parameters
        
        - name_matches - Case-insensitive normalized wildcard search on the name field. current version can't use. (M)
        - updater_name - Case-insensitive updater name to updater ID search.
        - post_id - Searches for a single post being added or removed from a pool.
        - is_new - Boolean syntax
        - Shorthand search for version=1 or version=>1
        - name_contains - same as name_matches. (A)

        Return json format:
        ```
        [
            {
                "id": 747009,
                "pool_id": 25767,
                "post_ids": [9010243, 9010244, 9014311, 9019533, 9020707, 9035262, 9035264, 9035294, 9035296, 9040170, 9042951, 9048727, 9049726, 9052845, 9057388, 9058039, 9062619, 9063534, 9067162, 9069531, 9072575, 9074083, 9078824, 9082553, 9082831, 9084833, 9087985, 9093112, 9096829, 9099633, 9104261, 9105053, 9114809, 9114797, 9115504, 9119794, 9124730, 9129614, 9134214, 9139246, 9145623, 9146696, 9150634, 9158009, 9160080, 9160892, 9170450, 9170455, 9177628, 9176669, 9186695, 9190286, 9191692, 9192507, 9197299, 9197872, 9203217, 9207738, 9207746, 9213064, 9213191, 9220683, 9218351, 9217986, 9222382, 9230860, 9231573, 9231583, 9236637, 9241311, 9241294, 9246839, 9254557, 9254558, 9257069, 9262469, 9335067, 9276436, 9278466, 9283178, 9335084, 9295031, 9303663, 9335100, 9305054, 9311246, 9314899, 9315618, 9320750, 9329180, 9340814, 9341231, 9341451, 9351622, 9351626, 9356375, 9357906, 9361750, 9361755, 9366438, 9367126, 9376322, 9376326, 9380890, 9382704, 9386440, 9387665, 9390664, 9391322, 9391384, 9403709, 9403713, 9404616, 9408321, 9414354, 9414697, 9419024, 9429320, 9430577, 9430591, 9436293, 9442566, 9447301, 9452519, 9457358, 9466457, 9468799, 9474177, 9474970, 9479506, 9485999, 9492681, 9493639, 9498176, 9503207, 9503208, 9508829, 9508835, 9514749, 9514753, 9520176, 9520180, 9539389, 9539394, 9542784, 9542816, 9551233, 9562986, 9584331, 9589851, 9590826, 9607135, 9678528, 9678538, 9694827, 9701048, 9709089, 9720352, 9729953, 9741358, 9746778, 9756829, 9766771],
                "added_post_ids": [9766771],
                "removed_post_ids": [],
                "updater_id": 480070,
                "description": "Artist: [[yun_cao_bing]]\r\nOrdered by the datetime of posting (on bilibili). Earliest post first.",
                "description_changed": false,
                "name": "Girls_Band_Cry_and_Various_-_100_Days_Challenge_(yun_cao_bing)",
                "name_changed": false,
                "created_at": "2025-05-20T01:23:26.000-04:00",
                "updated_at": "2025-08-09T07:47:27.000-04:00",
                "version": 61,
                "is_active": true,
                "boolean": false,
                "is_deleted": false,
                "category": "series"
            },
            ...
        ]
        ```

        获取在起始页码与结束页码范围内，指定搜索参数的图集版本列表；若 all_page 为 True，则获取当前搜索参数下所有页码的图集版本列表

        Args:
            limit (int, optional): 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000. Defaults to 20.
            query (dict | None, optional): 搜索参数，必须遵循 danbooru 中定义的语法，参见 [help:common url parameters](https://danbooru.donmai.us/wiki_pages/help:common_url_parameters) 以及 [api:post versions](https://danbooru.donmai.us/wiki_pages/api%3Apost_versions). Defaults to None. 表示搜索全站
            start_page (int, optional): 查询起始页码. Defaults to 1.
            end_page (int, optional): 查询结束页码. Defaults to 1.
            all_page (bool, optional): 是否获取当前搜索参数下所有页码的图集版本列表，若为 True，则忽略 start_page 与 end_page 参数. Defaults to False.
            concurrency (int, optional): 并发下载的数量. Defaults to 8.
            
        Note: 
            danbooru pool history 页面展示策略受 limit 参数影响，会自动根据 limit 参数与实际图集版本数量调整 html 分页器中的最大页码  
            page 受账号等级影响 (see help:users)，对普通无账户或会员用户，每次搜索的最大页数为 1000  
            否则在 https://danbooru.donmai.us/pool_versions?limit=1000&page=1001 网页中会弹出 Search Error. You cannot go beyond page 1000. Try narrowing your search terms, or upgrade your account to go beyond page 1000. 提示  
            在 https://danbooru.donmai.us/pool_versions.json?limit=10000&page=1001 网页中会返回：  
            ```
            {
                "success": false,
                "error": "PaginationExtension::PaginationError",
                "message": "You cannot go beyond page 1000.",
                "backtrace": [
                    "app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'",
                    "app/models/application_record.rb:18:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginate'",
                    "app/models/application_record.rb:37:in 'ApplicationRecord::PaginationMethods::ClassMethods#paginated_search'",
                    "app/controllers/pool_versions_controller.rb:10:in 'PoolVersionsController#index'",
                    "app/controllers/pool_versions_controller.rb:35:in 'PoolVersionsController#set_timeout'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```
            
            search[name_matches] 搜索参数版本当前无法使用，返回结果为：
            ```
            {
                "success": false,
                "error": "ActiveRecord::StatementInvalid",
                "message": "",
                "backtrace": [
                    "app/logical/pagination_extension.rb:141:in 'PaginationExtension#records'",
                    "app/logical/application_responder.rb:29:in 'ApplicationResponder#to_format'",
                    "app/controllers/application_controller.rb:82:in 'ApplicationController#respond_with'",
                    "app/controllers/pool_versions_controller.rb:13:in 'PoolVersionsController#index'",
                    "app/controllers/pool_versions_controller.rb:35:in 'PoolVersionsController#set_timeout'",
                    "app/logical/rack_server_timing.rb:19:in 'RackServerTiming#call'"
                ]
            }
            ```

        Returns:
            pd.DataFrame: 请求结果列表
        """
        if query is None:
            query = {}
        if limit > 1000:  # 事实上，超过该值时，返回的结果会被截断到该值
            limit = 1000
            logger.warning(f"Limit is set to {limit}, Because it exceeds the maximum allowed value of 1000.")
        url = '/pool_versions.json'
        headers = {
            'User-Agent': 'python',  # wtf? why you let this UA pass and block my normal UA?
        }
        params = {
            'limit': limit,  # 每页返回的结果数量。对于 /posts.json 最大限制为 200，其他情况为 1000
            'page': 1,  # 查询页码
        }
        # 更新搜索参数
        params.update(query)
        # 结果列表
        result: list[dict] = []
        # TODO: 添加检验用户账号等级逻辑，以便调整每次搜索的最大页数
        self.client.MAX_PAGE
        # 获取当前搜索参数下所有页码的图集版本列表
        if all_page:
            max_page = await self.index_page(  # 获取 html 分页器中的最大页码
                limit=limit,
                query=query,
            )
            logger.info(f"Maximum page number is equal to {max_page} for {limit = }, {query = }")

            if max_page > self.client.MAX_PAGE:
                remain_page = max_page - self.client.MAX_PAGE
                max_page = self.client.MAX_PAGE  # 限制最大页码不超过每次搜索的最大页数
                logger.warning(f"Maximum page number is set to {max_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

            result = await self.client.concurrent_fetch_page(
                url,
                headers=headers,
                params=params,
                start_page=1,
                end_page=max_page,
                page_key='page',
                concurrency=concurrency,
            )
        # 获取在起始页码与结束页码范围内，指定标题的图集版本列表
        else:
            if start_page > self.client.MAX_PAGE:
                remain_page = start_page - self.client.MAX_PAGE
                start_page = self.client.MAX_PAGE
                logger.warning(f"Start page is set to {start_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")
            if end_page > self.client.MAX_PAGE:
                remain_page = end_page - self.client.MAX_PAGE
                end_page = self.client.MAX_PAGE
                logger.warning(f"End page is set to {end_page} for {limit = }, {query = }, because {self.client.MAX_PAGE + remain_page} exceeds {self.client.MAX_PAGE}")

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
