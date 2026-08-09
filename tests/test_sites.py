import tempfile
import unittest
from collections.abc import Iterable
from types import SimpleNamespace

import pandas as pd
from niquests.exceptions import RequestException

from waifuboard.sites._pagination import (
    max_numeric_link_text,
    max_query_parameter,
)
from waifuboard.sites.danbooru import (
    DanbooruArtists,
    DanbooruPools,
    DanbooruPosts,
    DanbooruTags,
    DanbooruWikiPages,
)
from waifuboard.sites.moebooru import YanderePools, YanderePosts
from waifuboard.sites.safebooru import SafebooruPosts
from waifuboard.typing import DownloadItem, DownloadResult


def unique_download_items(items: Iterable[DownloadItem]) -> list[DownloadItem]:
    """Mirror Booru's first-item-wins destination deduplication in site mocks.

    Args:
        items: Download items submitted by a site component.

    Returns:
        Download items deduplicated by destination path in input order.
    """
    items_by_filepath: dict[str, DownloadItem] = {}
    for item in items:
        items_by_filepath.setdefault(item.filepath, item)
    return list(items_by_filepath.values())


class PaginationParserTests(unittest.TestCase):
    def test_numeric_link_parser_ignores_navigation_labels(self):
        html = """
        <div class="pagination">
          <a aria-label="page">2</a>
          <a aria-label="page">1068</a>
          <a aria-label="next">Next &rarr;</a>
        </div>
        """

        self.assertEqual(
            max_numeric_link_text(
                html,
                '//div[@class="pagination"]/a[@aria-label]',
            ),
            1068,
        )

    def test_numeric_link_parser_keeps_one_based_default(self):
        self.assertEqual(max_numeric_link_text("<html></html>", "//a"), 1)
        self.assertEqual(max_numeric_link_text(None, "//a"), 1)

    def test_query_parser_keeps_zero_based_default_and_reads_last_pid(self):
        html = """
        <div class="pagination">
          <a alt="last page" href="index.php?page=post&amp;pid=1995">Last</a>
        </div>
        """

        self.assertEqual(
            max_query_parameter(
                html,
                '//div[@class="pagination"]/a[@alt="last page"]/@href',
                parameter="pid",
                default=0,
            ),
            1995,
        )
        self.assertEqual(
            max_query_parameter("<html></html>", "//a/@href", parameter="pid", default=0),
            0,
        )
        self.assertEqual(
            max_query_parameter(None, "//a/@href", parameter="pid", default=0),
            0,
        )


class SiteReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_download_sidecars_follow_result_urls(self, component):
        """Assert unique downloads and sidecars follow returned URLs."""
        await component.download(save_raws=True, save_tags=True)

        self.assertEqual(
            component.client.download_urls,
            [
                "https://example.test/a.jpg",
                "https://example.test/skipped.jpg",
                "https://example.test/b.jpg",
            ],
        )
        self.assertEqual(component.client.saved_raw_ids, [3, 1])
        self.assertEqual(
            component.client.saved_tags,
            ["tag-b", "tag-a-first"],
        )

    async def test_pagination_probes_preserve_site_specific_failure_semantics(self):
        error = RequestException(
            "unavailable",
            request=SimpleNamespace(url="https://example.test/page"),
        )

        async def failing_get(*args, **kwargs):
            raise error

        client = SimpleNamespace(
            directory=".",
            base_url="https://example.test",
            get=failing_get,
        )

        for component_type in (
            DanbooruPosts,
            DanbooruTags,
            DanbooruArtists,
            DanbooruWikiPages,
            DanbooruPools,
        ):
            with self.subTest(component_type=component_type):
                self.assertEqual(await component_type(client).index_page(), 1)

        # HTML 分页探测失败时，Yandere 与 Safebooru 无法区分“只有首屏”和“最大页未知”；抛出请求错误可避免 all_page 静默截断数据
        with self.assertRaises(RequestException):
            await YanderePosts(client).list_gt_page()
        with self.assertRaises(RequestException):
            await YanderePools(client).list_pools_page()
        with self.assertRaises(RequestException):
            await SafebooruPosts(client).list_pid()

    async def test_yandere_tail_pagination_stops_on_repeated_non_empty_page(self):
        class PaginationClient:
            directory = "."
            base_url = "https://yande.re"

            async def concurrent_fetch_page(self, *args, **kwargs):
                if False:
                    yield None

            async def fetch_page(self, *args, **kwargs):
                return [{"id": 10}, {"id": 9}]

        posts = YanderePosts(PaginationClient())

        async def fixed_gt_page(**kwargs):
            return 1

        posts.list_gt_page = fixed_gt_page
        pages = [page async for page in posts.list(all_page=True)]

        self.assertEqual(pages, [[{"id": 10}, {"id": 9}]])

    async def test_yandere_pool_download_skips_empty_post_list(self):
        class DownloadClient:
            base_url = "https://yande.re"

            def __init__(self, directory):
                self.directory = directory
                self.download_calls = []

            async def concurrent_download_file(self, *args, **kwargs):
                self.download_calls.append((args, kwargs))
                if False:
                    yield None

        with tempfile.TemporaryDirectory() as directory:
            client = DownloadClient(directory)
            pools = YanderePools(client)

            async def fake_list_pools(**kwargs):
                yield [{"id": 1, "name": "empty-pool"}]

            async def fake_list_posts(**kwargs):
                return None

            pools.list_pools = fake_list_pools
            pools.list_posts = fake_list_posts

            await pools.download()

        self.assertEqual(client.download_calls, [])

    async def test_yandere_post_download_matches_sidecars_by_returned_url(self):
        class DownloadClient:
            base_url = "https://yande.re"

            def __init__(self, directory):
                self.directory = directory
                self.download_urls = []
                self.saved_raw_ids = []
                self.saved_tags = []

            async def concurrent_download_file(self, items, *args, **kwargs):
                items = unique_download_items(items)
                self.download_urls = [item.url for item in items]
                items_by_url = {item.url: item for item in items}
                yield DownloadResult(
                    item=items_by_url["https://example.test/b.jpg"],
                    filepath=f"{self.directory}/b.jpg",
                )
                yield None
                yield DownloadResult(
                    item=items_by_url["https://example.test/a.jpg"],
                    filepath=f"{self.directory}/a-1.jpg",
                )

            async def save_raws(self, raws, **kwargs):
                self.saved_raw_ids.append(raws.iloc[0]["id"])

            async def save_tags(self, tags, **kwargs):
                self.saved_tags.append(tags)

        with tempfile.TemporaryDirectory() as directory:
            posts = YanderePosts(DownloadClient(directory))

            async def fake_list(**kwargs):
                yield [
                    {"id": 1, "file_url": "https://example.test/a.jpg", "tags": "tag-a-first"},
                    {"id": 2, "file_url": "https://example.test/skipped.jpg", "tags": "tag-skipped"},
                    {"id": 3, "file_url": "https://example.test/b.jpg", "tags": "tag-b"},
                    {"id": 4, "file_url": "https://example.test/a.jpg", "tags": "tag-a-second"},
                    {"id": 5, "file_url": None, "tags": "tag-null"},
                ]

            posts.list = fake_list
            await self._assert_download_sidecars_follow_result_urls(posts)

    async def test_yandere_pool_download_matches_sidecars_by_returned_url(self):
        class DownloadClient:
            base_url = "https://yande.re"

            def __init__(self, directory):
                self.directory = directory
                self.download_urls = []
                self.saved_raw_ids = []
                self.saved_tags = []

            async def concurrent_download_file(self, items, *args, **kwargs):
                items = unique_download_items(items)
                self.download_urls = [item.url for item in items]
                items_by_url = {item.url: item for item in items}
                yield DownloadResult(
                    item=items_by_url["https://example.test/b.jpg"],
                    filepath=f"{self.directory}/b.jpg",
                )
                yield None
                yield DownloadResult(
                    item=items_by_url["https://example.test/a.jpg"],
                    filepath=f"{self.directory}/a-1.jpg",
                )

            async def save_raws(self, raws, **kwargs):
                self.saved_raw_ids.append(raws.iloc[0]["id"])

            async def save_tags(self, tags, **kwargs):
                self.saved_tags.append(tags)

        with tempfile.TemporaryDirectory() as directory:
            pools = YanderePools(DownloadClient(directory))

            async def fake_list_pools(**kwargs):
                yield [{"id": 10, "name": "sample-pool"}]

            async def fake_list_posts(**kwargs):
                return [
                    {"id": 1, "file_url": "https://example.test/a.jpg", "tags": "tag-a-first"},
                    {"id": 2, "file_url": "https://example.test/skipped.jpg", "tags": "tag-skipped"},
                    {"id": 3, "file_url": "https://example.test/b.jpg", "tags": "tag-b"},
                    {"id": 4, "file_url": "https://example.test/a.jpg", "tags": "tag-a-second"},
                    {"id": 5, "file_url": None, "tags": "tag-null"},
                ]

            pools.list_pools = fake_list_pools
            pools.list_posts = fake_list_posts
            await self._assert_download_sidecars_follow_result_urls(pools)

    async def test_safebooru_post_download_matches_sidecars_by_returned_url(self):
        class DownloadClient:
            base_url = "https://safebooru.org"

            def __init__(self, directory):
                self.directory = directory
                self.download_urls = []
                self.saved_raw_ids = []
                self.saved_tags = []

            async def concurrent_download_file(self, items, *args, **kwargs):
                items = unique_download_items(items)
                self.download_urls = [item.url for item in items]
                items_by_url = {item.url: item for item in items}
                yield DownloadResult(
                    item=items_by_url["https://example.test/b.jpg"],
                    filepath=f"{self.directory}/b.jpg",
                )
                yield None
                yield DownloadResult(
                    item=items_by_url["https://example.test/a.jpg"],
                    filepath=f"{self.directory}/a-1.jpg",
                )

            async def save_raws(self, raws, **kwargs):
                self.saved_raw_ids.append(raws.iloc[0]["id"])

            async def save_tags(self, tags, **kwargs):
                self.saved_tags.append(tags)

        with tempfile.TemporaryDirectory() as directory:
            posts = SafebooruPosts(DownloadClient(directory))

            async def fake_list(**kwargs):
                yield [
                    {"id": 1, "file_url": "https://example.test/a.jpg", "tags": "tag-a-first"},
                    {"id": 2, "file_url": "https://example.test/skipped.jpg", "tags": "tag-skipped"},
                    {"id": 3, "file_url": "https://example.test/b.jpg", "tags": "tag-b"},
                    {"id": 4, "file_url": "https://example.test/a.jpg", "tags": "tag-a-second"},
                    {"id": 5, "file_url": None, "tags": "tag-null"},
                ]

            posts.list = fake_list
            await self._assert_download_sidecars_follow_result_urls(posts)

    async def test_safebooru_list_uses_native_zero_based_pid(self):
        class PaginationClient:
            directory = "."
            base_url = "https://safebooru.org"
            MAX_PID = 200000

            def __init__(self):
                self.calls = []

            async def concurrent_fetch_page(self, *args, **kwargs):
                self.calls.append(kwargs)
                if False:
                    yield None

        client = PaginationClient()
        posts = SafebooruPosts(client)

        _ = [page async for page in posts.list()]

        self.assertEqual(client.calls[0]["start_page"], 0)
        self.assertEqual(client.calls[0]["end_page"], 0)

    async def test_safebooru_download_translates_one_based_user_page(self):
        client = SimpleNamespace(directory=".", base_url="https://safebooru.org")
        posts = SafebooruPosts(client)
        received_pages = []

        async def fake_list(**kwargs):
            received_pages.append((kwargs["start_page"], kwargs["end_page"]))
            yield []

        posts.list = fake_list

        await posts.download()

        self.assertEqual(received_pages, [(0, 0)])


if __name__ == "__main__":
    unittest.main()
