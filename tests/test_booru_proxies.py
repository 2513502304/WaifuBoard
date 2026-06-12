import logging
import unittest
from typing import get_args

from waifuboard.booru import Booru, BodyFormValueType, QueryParameterScalarType


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
