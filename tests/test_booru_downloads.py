import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from waifuboard.booru import Booru
from waifuboard.danbooru import DanbooruPools, DanbooruPosts
from waifuboard.moebooru import YanderePools, YanderePosts


class RecordingDownloadBooru(Booru):
    def __init__(self):
        super().__init__(
            default_headers=False,
            logger_level="WARNING",
            trust_env=False,
            max_attempt_number=1,
        )
        self.download_calls = []

    async def download_file(self, url, filepath, headers=None, referer=None):
        self.download_calls.append(
            {
                "url": url,
                "filepath": filepath,
                "headers": headers,
                "referer": referer,
            }
        )
        return (url, filepath)


class FakeDownloadClient:
    def __init__(self, directory: str, base_url: str):
        self.directory = directory
        self.base_url = base_url
        self.download_calls = []

    async def concurrent_download_file(
        self,
        urls,
        directory,
        extract_pattern=None,
        headers=None,
        referers=None,
    ):
        self.download_calls.append(
            {
                "urls": urls,
                "directory": directory,
                "headers": headers,
                "referers": referers,
            }
        )
        if False:
            yield None


class BooruDownloadTests(unittest.IsolatedAsyncioTestCase):
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
            return SimpleNamespace(content=b"image-bytes")

        booru.get = fake_get

        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "image.jpg"
            result = await booru.download_file(
                "https://cdn.example.test/image.jpg",
                str(filepath),
                referer="https://example.test/posts/1",
            )

            self.assertEqual(result, ("https://cdn.example.test/image.jpg", str(filepath)))
            self.assertEqual(filepath.read_bytes(), b"image-bytes")

        self.assertEqual(calls[0]["referer"], "https://example.test/posts/1")

    async def test_concurrent_download_file_keeps_referers_aligned_after_filter(self):
        booru = RecordingDownloadBooru()
        urls = pd.Series(
            {
                10: "https://cdn.example.test/10.jpg",
                20: "https://cdn.example.test/20.jpg",
            }
        )
        referers = pd.Series(
            {
                10: "https://example.test/posts/10",
                20: "https://example.test/posts/20",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "10.jpg").write_bytes(b"exists")
            results = [
                result
                async for result in booru.concurrent_download_file(
                    urls,
                    directory,
                    referers=referers,
                )
            ]

        self.assertEqual(len(results), 1)
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

    async def test_concurrent_download_file_keeps_duplicate_index_referers_positional(self):
        booru = RecordingDownloadBooru()
        urls = pd.Series(
            [
                "https://cdn.example.test/a.jpg",
                "https://cdn.example.test/b.jpg",
            ],
            index=[1, 1],
        )
        referers = pd.Series(
            [
                "https://example.test/posts/a",
                "https://example.test/posts/b",
            ],
            index=[1, 1],
        )

        with tempfile.TemporaryDirectory() as directory:
            results = [
                result
                async for result in booru.concurrent_download_file(
                    urls,
                    directory,
                    referers=referers,
                )
            ]

        self.assertEqual(len(results), 2)
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

        referers = client.download_calls[0]["referers"]
        self.assertEqual(referers.tolist(), ["https://danbooru.donmai.us/posts/123"])

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

        referers = client.download_calls[0]["referers"]
        self.assertEqual(referers.tolist(), ["https://danbooru.donmai.us/posts/123"])

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

        referers = client.download_calls[0]["referers"]
        self.assertEqual(referers.tolist(), ["https://yande.re/post/show/456"])

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

        referers = client.download_calls[0]["referers"]
        self.assertEqual(referers.tolist(), ["https://yande.re/post/show/456"])


if __name__ == "__main__":
    unittest.main()
