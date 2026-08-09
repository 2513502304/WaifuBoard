import logging
import unittest
from unittest.mock import patch
from typing import get_args

from niquests.exceptions import HTTPError
from niquests.exceptions import RequestException

from waifuboard.booru import Booru, BodyFormValueType, QueryParameterScalarType
from waifuboard.observability import format_bytes
from waifuboard.proxy import ProxyCooldownTracker, format_proxy_key, resolve_proxy


class DummyRequest:
    def __init__(self, method="GET", url="https://example.test/data.json"):
        self.method = method
        self.url = url


class DummyResponse:
    content = b"x" * 1536

    def __init__(self, status_code=200, reason="OK"):
        self.request = DummyRequest()
        self.status_code = status_code
        self.reason = reason
        self.history = []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(
                f"{self.status_code} Client Error: {self.reason}",
                response=self,
            )
        return None

    def __repr__(self):
        return f"<Response [{self.status_code}]>"


class CapturingClient:
    base_url = None

    def __init__(self, response=None):
        self.request_kwargs = None
        if isinstance(response, list):
            self.responses = response
        else:
            self.responses = [response or DummyResponse()]
        self.response = self.responses[0]
        self.request_history = []
        self.request_count = 0

    async def request(self, **kwargs):
        self.request_count += 1
        self.request_kwargs = kwargs
        self.request_history.append(kwargs)
        self.response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(self.response, Exception):
            raise self.response
        self.response.request = DummyRequest(kwargs["method"], kwargs["url"])
        return self.response

    async def gather(self, response):
        return None


class BooruProxyTests(unittest.IsolatedAsyncioTestCase):
    def test_format_bytes_uses_human_readable_units(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(2 * 1024 * 1024), "2.0 MB")

    def test_format_proxy_key_keeps_credentials_separate(self):
        self.assertEqual(
            format_proxy_key(
                "https://example.test/data.json",
                {"https": "http://alice:secret@proxy.test:8080"},
            ),
            "http://alice:secret@proxy.test:8080",
        )
        self.assertNotEqual(
            format_proxy_key(
                "https://example.test/data.json",
                {"https": "http://alice:secret@proxy.test:8080"},
            ),
            format_proxy_key(
                "https://example.test/data.json",
                {"https": "http://bob:secret@proxy.test:8080"},
            ),
        )

    def test_resolve_proxy_returns_raw_key_and_redacted_log(self):
        resolution = resolve_proxy(
            "https://example.test/data.json",
            {"https": "http://alice:secret@proxy.test:8080"},
        )

        self.assertEqual(resolution.key, "http://alice:secret@proxy.test:8080")
        self.assertEqual(resolution.log, "http://***:***@proxy.test:8080")

    def test_proxy_cooldown_tracker_success_resets_failure_streak(self):
        tracker = ProxyCooldownTracker(threshold=2, cooldown_seconds=60, clock=lambda: 0.0)

        self.assertFalse(tracker.record("http://proxy.test:8080", failed=True))
        self.assertFalse(tracker.record("http://proxy.test:8080", failed=False))
        self.assertFalse(tracker.record("http://proxy.test:8080", failed=True))
        self.assertTrue(tracker.is_available("http://proxy.test:8080"))

    def test_proxy_cooldown_tracker_releases_proxy_after_threshold_and_expiry(self):
        now = [10.0]
        tracker = ProxyCooldownTracker(
            threshold=2,
            cooldown_seconds=60,
            clock=lambda: now[0],
        )

        self.assertFalse(tracker.record("http://proxy.test:8080", failed=True))
        self.assertTrue(tracker.is_available("http://proxy.test:8080"))
        self.assertTrue(tracker.record("http://proxy.test:8080", failed=True))
        self.assertFalse(tracker.is_available("http://proxy.test:8080"))
        self.assertEqual(tracker.remaining("http://proxy.test:8080"), 60.0)

        now[0] = 70.0
        self.assertTrue(tracker.is_available("http://proxy.test:8080"))
        self.assertEqual(tracker.remaining("http://proxy.test:8080"), 0.0)

    def test_proxy_cooldown_tracker_prunes_expired_one_off_proxies(self):
        now = [0.0]
        tracker = ProxyCooldownTracker(
            threshold=1,
            cooldown_seconds=60,
            clock=lambda: now[0],
        )

        tracker.record("http://proxy-a.test:8080", failed=True)
        now[0] = 61.0
        tracker.record("http://proxy-b.test:8080", failed=True)

        self.assertNotIn("http://proxy-a.test:8080", tracker._cooldown_until)
        self.assertIn("http://proxy-b.test:8080", tracker._cooldown_until)

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

    async def test_disabled_info_logging_skips_response_metric_work(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=1,
        )
        client = CapturingClient()
        booru.client = client

        with patch("waifuboard.booru.get_body_size") as get_body_size:
            await booru.get("https://example.test/data.json", proxies=None)

        get_body_size.assert_not_called()

    async def test_expected_statuses_do_not_trigger_outer_status_retry(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.INFO,
            trust_env=False,
            max_attempt_number=2,
        )
        client = CapturingClient(DummyResponse(404, "Not Found"))
        booru.client = client

        with self.assertLogs("WaifuBoard", level="INFO") as records:
            response = await booru.get(
                "https://example.test/missing.json",
                proxies=None,
                expected_statuses={404},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(client.request_count, 1)
        self.assertIn(
            'GET https://example.test/missing.json "<[404]> Not Found" via direct',
            records.output[-1],
        )
        self.assertIn("expected=404", records.output[-1])

    async def test_http_verb_helpers_forward_expected_statuses(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.INFO,
            trust_env=False,
            max_attempt_number=2,
        )
        client = CapturingClient(DummyResponse(404, "Not Found"))
        booru.client = client

        verbs = ("post", "put", "delete", "head", "patch", "options")
        with self.assertLogs("WaifuBoard", level="INFO") as records:
            for verb in verbs:
                with self.subTest(verb=verb):
                    response = await getattr(booru, verb)(
                        "https://example.test/missing.json",
                        expected_statuses={404},
                    )
                    self.assertEqual(response.status_code, 404)

        self.assertEqual(client.request_count, len(verbs))
        self.assertEqual(
            sum("expected=404" in record for record in records.output),
            len(verbs),
        )

    async def test_unexpected_status_retries_outer_and_rotates_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=2,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
        )
        client = CapturingClient(
            [
                DummyResponse(503, "Service Unavailable"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch(
            "waifuboard.proxy.random.choice", side_effect=lambda choices: choices[0]
        ):
            with patch("waifuboard.booru.asyncio.sleep"):
                with self.assertLogs("WaifuBoard", level="WARNING") as records:
                    response = await booru.get(
                        "https://example.test/unavailable.json"
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.request_count, 2)
        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-b.test:8080", "https": "http://proxy-b.test:8080"},
        )
        retry_log = records.output[-1]
        self.assertIn("GET https://example.test/unavailable.json retry in", retry_log)
        self.assertIn("via http://proxy-a.test:8080", retry_log)
        self.assertIn("attempt=2/2", retry_log)
        self.assertIn("reason=HTTPError:", retry_log)

    async def test_non_cooldown_status_does_not_poison_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=1,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        client = CapturingClient(
            [
                DummyResponse(404, "Not Found"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch("waifuboard.proxy.random.choice", side_effect=lambda choices: choices[0]):
            with self.assertLogs("WaifuBoard", level="INFO"):
                await booru.get("https://example.test/missing.json")
                await booru.get("https://example.test/next.json")

        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )

    async def test_expected_cooldown_status_does_not_poison_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=1,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        client = CapturingClient(
            [
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch("waifuboard.proxy.random.choice", side_effect=lambda choices: choices[0]):
            with self.assertLogs("WaifuBoard", level="INFO"):
                await booru.get(
                    "https://example.test/rate-limited.json",
                    expected_statuses={429},
                )
                await booru.get("https://example.test/next.json")

        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )

    async def test_disabled_cooldown_statuses_do_not_poison_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=1,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
            proxy_cooldown_statuses=None,
        )
        client = CapturingClient(
            [
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch("waifuboard.proxy.random.choice", side_effect=lambda choices: choices[0]):
            with self.assertLogs("WaifuBoard", level="INFO"):
                await booru.get("https://example.test/rate-limited.json")
                await booru.get("https://example.test/next.json")

        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )

    async def test_transport_exception_reselects_proxy_on_outer_retry(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=2,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
        )
        client = CapturingClient(
            [
                RequestException("connection failed"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch(
            "waifuboard.proxy.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            with patch("waifuboard.booru.asyncio.sleep"):
                with self.assertLogs("WaifuBoard", level="WARNING"):
                    response = await booru.get("https://example.test/retry.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.request_count, 2)
        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-b.test:8080", "https": "http://proxy-b.test:8080"},
        )

    async def test_proxy_cooldown_skips_failed_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=1,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        client = CapturingClient(
            [
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch("waifuboard.proxy.random.choice", side_effect=lambda choices: choices[0]):
            with self.assertLogs("WaifuBoard", level="DEBUG") as records:
                await booru.get("https://example.test/rate-limited.json")
                await booru.get("https://example.test/recovered.json")

        self.assertTrue(any("proxy.cooldown" in record for record in records.output))
        self.assertEqual(
            client.request_history[0]["proxies"],
            {"http": "http://proxy-a.test:8080", "https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy-b.test:8080", "https": "http://proxy-b.test:8080"},
        )

    async def test_proxy_cooldown_waits_when_all_proxies_are_unavailable(self):
        now = [0.0]
        booru = Booru(
            default_headers=False,
            logger_level=logging.DEBUG,
            trust_env=False,
            max_attempt_number=1,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        booru._proxy_cooldown = ProxyCooldownTracker(
            threshold=1,
            cooldown_seconds=60,
            clock=lambda: now[0],
        )
        client = CapturingClient(
            [
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        async def advance_clock(seconds):
            now[0] += seconds

        with patch(
            "waifuboard.proxy.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            with patch(
                "waifuboard.proxy.asyncio.sleep",
                side_effect=advance_clock,
            ) as sleep:
                with self.assertLogs("WaifuBoard", level="DEBUG"):
                    await booru.get("https://example.test/a.json")
                    await booru.get("https://example.test/b.json")
                with self.assertLogs("WaifuBoard", level="WARNING") as records:
                    await booru.get("https://example.test/c.json")

        sleep.assert_awaited_once_with(60.0)
        self.assertTrue(
            any("All proxies are cooling down" in record for record in records.output)
        )
        self.assertEqual(client.request_count, 3)

    async def test_single_proxy_waits_for_cooldown_before_reuse(self):
        now = [0.0]
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=1,
            proxies="http://proxy.test:8080",
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        booru._proxy_cooldown = ProxyCooldownTracker(
            threshold=1,
            cooldown_seconds=60,
            clock=lambda: now[0],
        )
        client = CapturingClient(
            [
                DummyResponse(429, "Too Many Requests"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        await booru.get("https://example.test/rate-limited.json")

        async def advance_clock(seconds):
            now[0] += seconds

        with patch("waifuboard.proxy.asyncio.sleep", side_effect=advance_clock) as sleep:
            with self.assertLogs("WaifuBoard", level="WARNING") as records:
                response = await booru.get("https://example.test/recovered.json")

        self.assertEqual(response.status_code, 200)
        sleep.assert_awaited_once_with(60.0)
        self.assertTrue(any("is cooling down" in record for record in records.output))
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"http": "http://proxy.test:8080", "https": "http://proxy.test:8080"},
        )

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
