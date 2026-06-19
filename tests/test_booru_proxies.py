import logging
import unittest
from typing import get_args

from waifuboard.booru import Booru, BodyFormValueType, QueryParameterScalarType
from waifuboard.utils import format_bytes


class DummyRequest:
    method = "GET"
    url = "https://example.test/data.json"


class DummyResponse:
    request = DummyRequest()
    reason = "OK"
    content = b"x" * 1536
    history = []

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
    def test_format_bytes_uses_human_readable_units(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(2 * 1024 * 1024), "2.0 MB")

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

    async def test_request_log_keeps_existing_shape_and_appends_metrics(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.INFO,
            trust_env=False,
            max_attempt_number=1,
        )
        client = CapturingClient()
        booru.client = client

        with self.assertLogs("WaifuBoard", level="INFO") as records:
            await booru.get("https://example.test/data.json", proxies=None)

        message = records.output[-1]
        self.assertIn(
            'GET https://example.test/data.json "<[200]> OK" via direct',
            message,
        )
        self.assertIn("(attempt=1/1", message)
        self.assertRegex(message, r"elapsed=\d+\.\d{3}s")
        self.assertIn("bytes=1.5 KB", message)
        self.assertIn("redirects=0", message)

    async def test_params_accept_numeric_values_and_json_dict_values(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=1,
        )
        client = CapturingClient()
        booru.client = client

        await booru.get(
            "https://example.test/data.json?existing=1",
            params={"page": 2, "exact": True, "payload": {"rating": "safe"}},
        )

        self.assertEqual(
            client.request_kwargs["params"],
            {
                "existing": ["1"],
                "page": 2,
                "exact": True,
                "payload": '{"rating":"safe"}',
            },
        )

    def test_public_request_value_types_include_niquests_numeric_scalars(self):
        self.assertGreaterEqual(
            set(get_args(QueryParameterScalarType)),
            {int, float, bool},
        )
        self.assertGreaterEqual(
            set(get_args(BodyFormValueType)),
            {int, float, bool},
        )


if __name__ == "__main__":
    unittest.main()
