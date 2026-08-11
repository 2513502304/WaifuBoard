import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
from niquests.exceptions import HTTPError, JSONDecodeError, RequestException

from waifuboard.booru import Booru
from waifuboard.sites.danbooru import DanbooruPools, DanbooruPosts
from waifuboard.sites.moebooru import YanderePools, YanderePosts
from waifuboard.sites.safebooru import SafebooruPosts
from waifuboard.typing import DownloadItem, DownloadResult, PageResult


class RecordingDownloadBooru(Booru):
    def __init__(self):
        super().__init__(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        self.download_calls = []

    async def download_file(self, item):
        self.download_calls.append(
            {
                "url": item.url,
                "filepath": item.filepath,
                "headers": item.headers,
                "referer": item.referer,
            }
        )
        return DownloadResult(item=item, filepath=item.filepath)


class FakeDownloadClient:
    def __init__(self, directory: str, base_url: str):
        self.directory = directory
        self.base_url = base_url
        self.download_calls = []
        self.saved_raws = []
        self.saved_tags = []

    async def concurrent_download_file(
        self,
        items,
    ):
        items = list(items)
        self.download_calls.append(
            {
                "items": items,
            }
        )
        for item in items:
            yield DownloadResult(item=item, filepath=item.filepath)

    async def save_raws(self, raws, directory, filename, overwrite=False):
        self.saved_raws.append(
            {
                "raws": raws,
                "directory": directory,
                "filename": filename,
                "overwrite": overwrite,
            }
        )
        return (raws, directory, filename)

    async def save_tags(self, tag, directory, filename, overwrite=False):
        self.saved_tags.append(
            {
                "tag": tag,
                "directory": directory,
                "filename": filename,
                "overwrite": overwrite,
            }
        )
        return (tag, directory, filename)


class ReversingDownloadClient(FakeDownloadClient):
    async def concurrent_download_file(
        self,
        items,
    ):
        items = list(items)
        self.download_calls.append({"items": items})
        for item in reversed(items):
            yield DownloadResult(item=item, filepath=item.filepath)


class BooruDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_preserves_final_http_error_response_for_callers(self):
        request = SimpleNamespace(
            method="GET",
            url="https://example.test/missing",
        )

        class ErrorResponse:
            reason = "Not Found"

            def __init__(self):
                self.request = request

            def raise_for_status(self):
                raise HTTPError("404 Client Error", request=request, response=self)

            def __repr__(self):
                return "<Response [404]>"

        class ErrorClient:
            base_url = None

            async def request(self, **kwargs):
                return ErrorResponse()

            async def gather(self, response):
                return None

        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        booru.client = ErrorClient()

        response = await booru.get(request.url)

        self.assertEqual(response.reason, "Not Found")

    async def test_download_file_rejects_final_http_error_response(self):
        request = SimpleNamespace(
            method="GET",
            url="https://cdn.example.test/image.jpg",
        )

        class ErrorResponse:
            reason = "Service Unavailable"
            content = b"maintenance"

            def __init__(self):
                self.request = request

            def raise_for_status(self):
                raise HTTPError("503 Server Error", request=request, response=self)

            def __repr__(self):
                return "<Response [503]>"

        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )

        async def fake_get(*args, **kwargs):
            return ErrorResponse()

        booru.get = fake_get

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "image.jpg"
            result = await booru.download_file(
                DownloadItem(url=request.url, filepath=str(filepath))
            )

            self.assertIsNone(result)
            self.assertFalse(filepath.exists())

    async def test_download_file_forwards_referer(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        calls = []

        async def fake_get(url, **kwargs):
            calls.append({"url": url, **kwargs})
            return SimpleNamespace(
                content=b"image-bytes",
                raise_for_status=lambda: None,
            )

        booru.get = fake_get

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "image.jpg"
            item = DownloadItem(
                url="https://cdn.example.test/image.jpg",
                filepath=str(filepath),
                referer="https://example.test/posts/1",
            )
            result = await booru.download_file(item)

            self.assertEqual(result, DownloadResult(item=item, filepath=str(filepath)))
            self.assertEqual(filepath.read_bytes(), b"image-bytes")

        self.assertEqual(calls[0]["referer"], "https://example.test/posts/1")

    async def test_download_file_supports_tuple_list_headers_with_referer(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        calls = []

        async def fake_get(url, **kwargs):
            calls.append({"url": url, **kwargs})
            return SimpleNamespace(
                content=b"image-bytes",
                raise_for_status=lambda: None,
            )

        booru.get = fake_get
        headers = [("X-Download", "one")]

        with tempfile.TemporaryDirectory() as directory:
            item = DownloadItem(
                url="https://cdn.example.test/image.jpg",
                filepath=str(Path(directory) / "image.jpg"),
                headers=headers,
                referer="https://example.test/posts/1",
            )
            result = await booru.download_file(item)

        self.assertIsNotNone(result)
        self.assertEqual(calls[0]["headers"], [("X-Download", "one")])
        self.assertEqual(calls[0]["referer"], "https://example.test/posts/1")
        self.assertEqual(headers, [("X-Download", "one")])

    async def test_download_file_rejects_empty_response_without_creating_file(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )

        async def fake_get(*args, **kwargs):
            return SimpleNamespace(content=b"", raise_for_status=lambda: None)

        booru.get = fake_get

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "empty.jpg"
            item = DownloadItem(
                url="https://cdn.example.test/empty.jpg",
                filepath=str(filepath),
            )

            result = await booru.download_file(item)

            self.assertIsNone(result)
            self.assertFalse(filepath.exists())

    async def test_download_file_removes_temporary_file_after_replace_failure(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )

        async def fake_get(*args, **kwargs):
            return SimpleNamespace(
                content=b"image-bytes",
                raise_for_status=lambda: None,
            )

        booru.get = fake_get

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "image.jpg"
            item = DownloadItem(
                url="https://cdn.example.test/image.jpg",
                filepath=str(filepath),
            )

            with patch(
                "waifuboard.booru.aioos.replace",
                new=AsyncMock(side_effect=OSError("replace failed")),
            ):
                result = await booru.download_file(item)

            self.assertIsNone(result)
            self.assertFalse(filepath.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    async def test_concurrent_download_file_uses_download_items_after_filter(self):
        booru = RecordingDownloadBooru()

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "10.jpg").write_bytes(b"exists")
            items = [
                DownloadItem(
                    url="https://cdn.example.test/10.jpg",
                    filepath=str(Path(directory) / "10.jpg"),
                    referer="https://example.test/posts/10",
                ),
                DownloadItem(
                    url="https://cdn.example.test/20.jpg",
                    filepath=str(Path(directory) / "20.jpg"),
                    referer="https://example.test/posts/20",
                ),
            ]
            results = [
                result
                async for result in booru.concurrent_download_file(items)
            ]

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], DownloadResult)
        self.assertEqual(
            {
                (call["url"], call["referer"])
                for call in booru.download_calls
            },
            {
                (
                    "https://cdn.example.test/20.jpg",
                    "https://example.test/posts/20",
                ),
            },
        )

    async def test_concurrent_download_file_redownloads_zero_byte_files(self):
        booru = RecordingDownloadBooru()

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "empty.jpg"
            filepath.touch()
            item = DownloadItem(
                url="https://cdn.example.test/empty.jpg",
                filepath=str(filepath),
            )

            results = [
                result
                async for result in booru.concurrent_download_file([item])
            ]

        self.assertEqual(len(results), 1)
        self.assertEqual([call["url"] for call in booru.download_calls], [item.url])

    async def test_concurrent_download_file_deduplicates_destination_paths(self):
        booru = RecordingDownloadBooru()

        with tempfile.TemporaryDirectory() as directory:
            filepath = str(Path(directory) / "same.jpg")
            items = [
                DownloadItem(
                    url="https://cdn.example.test/first.jpg",
                    filepath=filepath,
                ),
                DownloadItem(
                    url="https://cdn.example.test/second.jpg",
                    filepath=filepath,
                ),
            ]

            results = [
                result
                async for result in booru.concurrent_download_file(items)
            ]

        self.assertEqual(len(results), 1)
        self.assertEqual(
            [call["url"] for call in booru.download_calls],
            ["https://cdn.example.test/first.jpg"],
        )

    async def test_concurrent_download_file_preserves_item_identity(self):
        booru = RecordingDownloadBooru()

        with tempfile.TemporaryDirectory() as directory:
            items = [
                DownloadItem(
                    url="https://cdn.example.test/a.jpg",
                    filepath=str(Path(directory) / "a.jpg"),
                    referer="https://example.test/posts/a",
                    raw=pd.DataFrame([{"id": "a"}]),
                    tags="tag_a",
                ),
                DownloadItem(
                    url="https://cdn.example.test/b.jpg",
                    filepath=str(Path(directory) / "b.jpg"),
                    referer="https://example.test/posts/b",
                    raw=pd.DataFrame([{"id": "b"}]),
                    tags="tag_b",
                ),
            ]
            results = [
                result
                async for result in booru.concurrent_download_file(items)
            ]

        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result.item.tags for result in results if result is not None},
            {"tag_a", "tag_b"},
        )
        self.assertEqual(
            {
                (call["url"], call["referer"])
                for call in booru.download_calls
            },
            {
                (
                    "https://cdn.example.test/a.jpg",
                    "https://example.test/posts/a",
                ),
                (
                    "https://cdn.example.test/b.jpg",
                    "https://example.test/posts/b",
                ),
            },
        )

    async def test_concurrent_fetch_page_returns_page_result_without_mutating_params(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        params = {"tags": "cat"}

        async def fake_fetch_page(api, *, params=None, **kwargs):
            return [{"page": params["page"], "tags": params["tags"]}]

        booru.fetch_page = fake_fetch_page

        results = [
            result
            async for result in booru.concurrent_fetch_page(
                "https://example.test/posts.json",
                params=params,
                start_page=1,
                end_page=2,
                page_key="page",
            )
        ]

        self.assertEqual(params, {"tags": "cat"})
        self.assertEqual(
            {result.page for result in results if result is not None},
            {1, 2},
        )
        self.assertTrue(
            all(isinstance(result, PageResult) for result in results if result is not None)
        )

    async def test_fetch_page_distinguishes_request_failure_from_empty_page(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        request = SimpleNamespace(url="https://example.test/posts.json")

        async def failing_get(*args, **kwargs):
            raise RequestException("unavailable", request=request)

        booru.get = failing_get
        failed = await booru.fetch_page(request.url)

        async def empty_get(*args, **kwargs):
            return SimpleNamespace(
                json=lambda: [],
                raise_for_status=lambda: None,
            )

        booru.get = empty_get
        empty = await booru.fetch_page(request.url)

        self.assertIsNone(failed)
        self.assertEqual(empty, [])

    async def test_fetch_page_retries_malformed_success_response(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=2,
        )
        calls = 0

        class JsonResponse:
            def raise_for_status(self):
                return None

            def json(self):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise JSONDecodeError("empty response", "", 0)
                return [{"id": 1}]

        async def fake_get(*args, **kwargs):
            return JsonResponse()

        booru.get = fake_get

        with patch("waifuboard.booru.asyncio.sleep", new=AsyncMock()):
            content = await booru.fetch_page("https://example.test/posts.json")

        self.assertEqual(content, [{"id": 1}])
        self.assertEqual(calls, 2)

    async def test_concurrent_fetch_page_preserves_failed_page_number(self):
        booru = Booru(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )

        async def fake_fetch_page(api, *, params=None, **kwargs):
            return None if params["page"] == 1 else []

        booru.fetch_page = fake_fetch_page
        results = [
            result
            async for result in booru.concurrent_fetch_page(
                "https://example.test/posts.json",
                start_page=1,
                end_page=2,
                page_key="page",
            )
        ]
        results_by_page = {result.page: result.content for result in results}

        self.assertEqual(results_by_page, {1: None, 2: []})

    def test_build_download_items_keeps_duplicate_index_raws_positional(self):
        booru = RecordingDownloadBooru()
        component = DanbooruPosts(booru)
        posts = pd.DataFrame(
            [
                {
                    "id": 1,
                    "file_url": "https://cdn.example.test/1.jpg",
                    "tag_string": "first_tag",
                },
                {
                    "id": 2,
                    "file_url": "https://cdn.example.test/2.jpg",
                    "tag_string": "second_tag",
                },
            ],
            index=[7, 7],
        )

        with tempfile.TemporaryDirectory() as directory:
            items = component.build_download_items(
                posts,
                directory,
                tag_column="tag_string",
            )

        self.assertEqual([item.raw.iloc[0]["id"] for item in items], [1, 2])
        self.assertEqual([len(item.raw) for item in items], [1, 1])

    def test_build_download_items_skips_unused_sidecar_materialization(self):
        booru = RecordingDownloadBooru()
        component = DanbooruPosts(booru)
        posts = pd.DataFrame(
            [
                {
                    "id": 1,
                    "file_url": "https://cdn.example.test/1.jpg",
                    "tag_string": "tag",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            items = component.build_download_items(
                posts,
                directory,
                tag_column="tag_string",
                include_raw=False,
                include_tags=False,
            )

        self.assertIsNone(items[0].raw)
        self.assertIsNone(items[0].tags)


class SiteDownloadRefererTests(unittest.IsolatedAsyncioTestCase):
    async def test_danbooru_posts_download_uses_post_page_referers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://danbooru.donmai.us/")
            posts = DanbooruPosts(client)

            async def fake_index(**kwargs):
                yield [
                    {
                        "id": 123,
                        "file_url": "https://cdn.donmai.us/original/123.jpg",
                        "tag_string": "tag",
                    }
                ]

            posts.index = fake_index

            await posts.download()

        items = client.download_calls[0]["items"]
        self.assertEqual([item.referer for item in items], ["https://danbooru.donmai.us/posts/123"])
        self.assertIsNone(items[0].raw)
        self.assertIsNone(items[0].tags)

    async def test_danbooru_pools_download_uses_post_page_referers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://danbooru.donmai.us/")
            pools = DanbooruPools(client)

            async def fake_index(**kwargs):
                yield [{"id": 1, "name": "pool", "post_count": 1, "post_ids": [123]}]

            async def fake_show(**kwargs):
                return [
                    {
                        "id": 123,
                        "file_url": "https://cdn.donmai.us/original/123.jpg",
                        "tag_string": "tag",
                    }
                ]

            async def fake_batch_process_tasks(tasks):
                return [await task for task in tasks]

            pools.index = fake_index
            client.posts = SimpleNamespace(show=fake_show)
            client.batch_process_tasks = fake_batch_process_tasks

            await pools.download()

        items = client.download_calls[0]["items"]
        self.assertEqual([item.referer for item in items], ["https://danbooru.donmai.us/posts/123"])

    async def test_yandere_posts_download_uses_post_page_referers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://yande.re")
            posts = YanderePosts(client)

            async def fake_list(**kwargs):
                yield [
                    {
                        "id": 456,
                        "file_url": "https://files.yande.re/image/456.jpg",
                        "tags": "tag",
                    }
                ]

            posts.list = fake_list

            await posts.download()

        items = client.download_calls[0]["items"]
        self.assertEqual([item.referer for item in items], ["https://yande.re/post/show/456"])

    async def test_yandere_pools_download_uses_post_page_referers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://yande.re")
            pools = YanderePools(client)

            async def fake_list_pools(**kwargs):
                yield [{"id": 1, "name": "pool"}]

            async def fake_list_posts(**kwargs):
                return [
                    {
                        "id": 456,
                        "file_url": "https://files.yande.re/image/456.jpg",
                        "tags": "tag",
                    }
                ]

            pools.list_pools = fake_list_pools
            pools.list_posts = fake_list_posts

            await pools.download()

        items = client.download_calls[0]["items"]
        self.assertEqual([item.referer for item in items], ["https://yande.re/post/show/456"])

    async def test_danbooru_posts_download_saves_sidecars_from_download_item(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ReversingDownloadClient(directory, "https://danbooru.donmai.us/")
            posts = DanbooruPosts(client)

            async def fake_index(**kwargs):
                yield [
                    {
                        "id": 1,
                        "file_url": "https://cdn.donmai.us/original/1.jpg",
                        "tag_string": "first_tag",
                    },
                    {
                        "id": 2,
                        "file_url": "https://cdn.donmai.us/original/2.jpg",
                        "tag_string": "second_tag",
                    },
                ]

            posts.index = fake_index

            await posts.download(save_raws=True, save_tags=True)

        raw_by_filename = {
            record["filename"]: record["raws"].iloc[0]["id"]
            for record in client.saved_raws
        }
        tag_by_filename = {
            record["filename"]: record["tag"]
            for record in client.saved_tags
        }
        self.assertEqual(raw_by_filename, {"1.json": 1, "2.json": 2})
        self.assertEqual(
            tag_by_filename,
            {"1.txt": "first_tag", "2.txt": "second_tag"},
        )


class SiteReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_yandere_pools_download_skips_empty_post_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://yande.re")
            pools = YanderePools(client)

            async def fake_list_pools(**kwargs):
                yield [{"id": 1, "name": "empty-pool"}]

            async def fake_list_posts(**kwargs):
                return None

            pools.list_pools = fake_list_pools
            pools.list_posts = fake_list_posts

            await pools.download()

        self.assertEqual(client.download_calls, [])

    async def test_pagination_probes_propagate_request_errors(self):
        client = SimpleNamespace(directory=".", base_url="https://example.test")
        error = RequestException(
            "unavailable",
            request=SimpleNamespace(url="https://example.test/page"),
        )

        async def failing_get(*args, **kwargs):
            raise error

        client.get = failing_get

        with self.assertRaises(RequestException):
            await YanderePosts(client).list_gt_page()
        with self.assertRaises(RequestException):
            await YanderePools(client).list_pools_page()
        with self.assertRaises(RequestException):
            await SafebooruPosts(client).list_pid()

    async def test_yandere_tail_pagination_stops_when_server_repeats_a_page(self):
        class PaginationClient:
            directory = "."
            base_url = "https://yande.re"

            async def concurrent_fetch_page(self, *args, **kwargs):
                yield PageResult(page=1, content=[{"id": 10}, {"id": 9}])

            async def fetch_page(self, *args, **kwargs):
                return [{"id": 10}, {"id": 9}]

        posts = YanderePosts(PaginationClient())

        async def fixed_gt_page(**kwargs):
            return 1

        posts.list_gt_page = fixed_gt_page
        pages = [page async for page in posts.list(all_page=True)]

        self.assertEqual(pages, [[{"id": 10}, {"id": 9}]])

    async def test_yandere_tail_pagination_exposes_request_failure(self):
        class PaginationClient:
            directory = "."
            base_url = "https://yande.re"

            async def concurrent_fetch_page(self, *args, **kwargs):
                yield PageResult(page=1, content=[{"id": 10}])

            async def fetch_page(self, *args, **kwargs):
                return None

        posts = YanderePosts(PaginationClient())

        async def fixed_gt_page(**kwargs):
            return 1

        posts.list_gt_page = fixed_gt_page
        pages = [page async for page in posts.list(all_page=True)]

        self.assertEqual(pages, [[{"id": 10}], None])

    async def test_safebooru_default_page_maps_to_zero_based_pid(self):
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

    async def test_safebooru_download_converts_user_pages_to_zero_based_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeDownloadClient(directory, "https://safebooru.org")
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
