import logging
import unittest

from waifuboard.booru import Booru


class DummyRequest:
    method = "GET"
    url = "https://example.test/data.json"


class DummyResponse:
    request = DummyRequest()
    reason = "OK"

    def raise_for_status(self):
        return None

    def __repr__(self):
        return "<Response [200]>"


class CapturingClient:
    base_url = None

    def __init__(self):
        self.request_kwargs = None

    async def request(self, **kwargs):
        self.request_kwargs = kwargs
        return DummyResponse()

    async def gather(self, response):
        return None


class BooruProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_level_none_proxies_disable_proxy_without_empty_urls(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=1,
        )
        client = CapturingClient()
        booru.client = client

        await booru.get("https://example.test/data.json", proxies=None)

        self.assertEqual(client.request_kwargs["proxies"], {"no_proxy": "*"})


if __name__ == "__main__":
    unittest.main()
