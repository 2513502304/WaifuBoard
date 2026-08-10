import logging
import unittest
from unittest.mock import Mock, patch
from typing import Any, cast, get_args

from niquests.exceptions import HTTPError
from niquests.exceptions import RequestException

from waifuboard.booru import Booru, BodyFormValueType, QueryParameterScalarType
from waifuboard.observability import format_bytes, format_request_error
from waifuboard.proxy import (
    PREPARED_PROXY_CACHE_SIZE,
    ProxyCooldownTracker,
    PreparedProxyPool,
    format_proxy_key,
    normalize_proxy,
    prepare_proxy_pool,
    resolve_proxy,
)
from waifuboard.proxy.pool import _prepare_proxy_pool_cached


class DummyRequest:
    def __init__(self, method="GET", url="https://example.test/data.json"):
        self.method = method
        self.url = url


class DummyResponse:
    content = b"x" * 1536

    def __init__(self, status_code=200, reason="OK", request_url=None):
        self.request_url = request_url
        self.request = DummyRequest(url=request_url or "https://example.test/data.json")
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
    trust_env = False

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
        self.response.request = DummyRequest(
            kwargs["method"],
            self.response.request_url or kwargs["url"],
        )
        return self.response

    async def gather(self, response):
        return None


class BooruProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Start each test with empty shared proxy preparation caches."""
        _prepare_proxy_pool_cached.cache_clear()
        PreparedProxyPool._resolve_route.cache_clear()

    def tearDown(self):
        """Prevent one test's prepared pools and routes from leaking to another."""
        PreparedProxyPool._resolve_route.cache_clear()
        _prepare_proxy_pool_cached.cache_clear()

    async def test_instance_proxy_pool_is_normalized_only_during_initialization(self):
        proxies = tuple(
            f"http://proxy-{index}.test:8080" for index in range(10)
        )

        with patch(
            "waifuboard.proxy.pool.normalize_proxy",
            wraps=normalize_proxy,
        ) as normalize:
            booru = Booru(
                default_headers=False,
                logger_level=logging.CRITICAL,
                trust_env=False,
                max_attempt_number=1,
                proxies=proxies,
            )
            client = CapturingClient()
            booru.client = client

            self.assertEqual(normalize.call_count, len(proxies))
            await booru.get("https://example.test/first.json")
            await booru.get("https://example.test/second.json")

        self.assertEqual(normalize.call_count, len(proxies))
        self.assertEqual(client.request_count, 2)

    def test_equivalent_request_proxy_overrides_share_prepared_pool(self):
        first = prepare_proxy_pool(
            (
                {"https": "http://proxy-a.test:8080"},
                "http://proxy-b.test:8080",
            )
        )
        second = prepare_proxy_pool(
            (
                {"https": "http://proxy-a.test:8080"},
                "http://proxy-b.test:8080",
            )
        )

        self.assertIs(first, second)

    def test_prepared_proxy_cache_uses_bounded_configuration_capacity(self):
        self.assertEqual(
            _prepare_proxy_pool_cached.cache_info().maxsize,
            PREPARED_PROXY_CACHE_SIZE,
        )

        for index in range(PREPARED_PROXY_CACHE_SIZE + 1):
            prepare_proxy_pool(f"http://proxy-{index}.test:8080")

        cache_info = _prepare_proxy_pool_cached.cache_info()
        self.assertEqual(cache_info.currsize, PREPARED_PROXY_CACHE_SIZE)
        self.assertEqual(cache_info.misses, PREPARED_PROXY_CACHE_SIZE + 1)

    async def test_mutated_request_proxy_mapping_does_not_reuse_stale_cache(self):
        proxies = {"https": "http://proxy-a.test:8080"}
        first = prepare_proxy_pool(proxies)
        proxies["https"] = "http://proxy-b.test:8080"
        second = prepare_proxy_pool(proxies)
        tracker = ProxyCooldownTracker()

        first_selection = await first.selector(
            url="https://example.test/data.json",
            tracker=tracker,
        ).select()
        second_selection = await second.selector(
            url="https://example.test/data.json",
            tracker=tracker,
        ).select()

        self.assertIsNot(first, second)
        self.assertEqual(first_selection.key, "http://proxy-a.test:8080")
        self.assertEqual(second_selection.key, "http://proxy-b.test:8080")

    async def test_repeated_immutable_request_override_reuses_identity_cache(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.CRITICAL,
            trust_env=False,
            max_attempt_number=1,
        )
        booru.client = cast(
            Any,
            CapturingClient([DummyResponse(), DummyResponse()]),
        )
        proxies = tuple(
            f"http://proxy-{index}.test:8080" for index in range(100)
        )

        with patch(
            "waifuboard.booru.prepare_proxy_pool",
            wraps=prepare_proxy_pool,
        ) as prepare:
            await booru.get("https://example.test/first.json", proxies=proxies)
            await booru.get("https://example.test/second.json", proxies=proxies)

        prepare.assert_called_once_with(proxies)

    async def test_returned_selection_cannot_mutate_cached_proxy_metadata(self):
        pool = prepare_proxy_pool({"https": "http://proxy-a.test:8080"})
        tracker = ProxyCooldownTracker()

        first_selection = await pool.selector(
            url="https://example.test/first.json",
            tracker=tracker,
        ).select()
        first_selection.proxies["https"] = "http://proxy-b.test:8080"
        second_selection = await pool.selector(
            url="https://example.test/second.json",
            tracker=tracker,
        ).select()

        self.assertEqual(first_selection.proxies["https"], "http://proxy-b.test:8080")
        self.assertEqual(second_selection.proxies["https"], "http://proxy-a.test:8080")
        self.assertEqual(second_selection.key, "http://proxy-a.test:8080")

    async def test_prepared_pool_tracks_effective_environment_proxy(self):
        pool = prepare_proxy_pool({"https": "http://explicit.test:8080"})

        with patch(
            "waifuboard.proxy.pool.getproxies",
            return_value={
                "http": "http://environment.test:8080",
                "http://restricted.test": "http://restricted-env.test:8080",
            },
        ):
            selection = await pool.selector(
                url="http://restricted.test/data.json",
                tracker=ProxyCooldownTracker(),
                trust_env=True,
            ).select()

        self.assertEqual(selection.key, "http://restricted-env.test:8080")
        self.assertEqual(
            selection.proxies["http://restricted.test"],
            "http://restricted-env.test:8080",
        )
        self.assertEqual(selection.proxies["https"], "http://explicit.test:8080")

    async def test_environment_proxy_change_invalidates_route_cache(self):
        pool = prepare_proxy_pool({"https": "http://explicit.test:8080"})

        with patch(
            "waifuboard.proxy.pool.getproxies",
            side_effect=[
                {"http": "http://environment-a.test:8080"},
                {"http": "http://environment-b.test:8080"},
            ],
        ):
            first = await pool.selector(
                url="http://example.test/first.json",
                tracker=ProxyCooldownTracker(),
                trust_env=True,
            ).select()
            second = await pool.selector(
                url="http://example.test/second.json",
                tracker=ProxyCooldownTracker(),
                trust_env=True,
            ).select()

        self.assertEqual(first.key, "http://environment-a.test:8080")
        self.assertEqual(second.key, "http://environment-b.test:8080")

    async def test_environment_snapshot_is_read_once_for_distinct_bypass_rules(self):
        pool = prepare_proxy_pool(
            (
                {
                    "https": "http://proxy-a.test:8080",
                    "no_proxy": "internal-a.test",
                },
                {
                    "https": "http://proxy-b.test:8080",
                    "no_proxy": "internal-b.test",
                },
            )
        )

        with patch(
            "waifuboard.proxy.pool.getproxies",
            return_value={"http": "http://environment.test:8080"},
        ) as getproxies:
            await pool.selector(
                url="http://example.test/data.json",
                tracker=ProxyCooldownTracker(),
                trust_env=True,
            ).select()

        getproxies.assert_called_once_with()

    async def test_empty_pool_tracks_environment_fallback_when_trusted(self):
        with patch(
            "waifuboard.proxy.pool.getproxies",
            return_value={"https": "http://environment.test:8080"},
        ):
            selection = await prepare_proxy_pool(()).selector(
                url="https://example.test/data.json",
                tracker=ProxyCooldownTracker(),
                trust_env=True,
            ).select()

        self.assertEqual(selection.key, "http://environment.test:8080")
        self.assertEqual(
            selection.proxies,
            {"https": "http://environment.test:8080"},
        )

    async def test_prepared_proxy_pool_keeps_host_specific_resolution(self):
        pool = prepare_proxy_pool(
            {
                "https": "http://global.test:8080",
                "https://restricted.test": "http://japan.test:8080",
            }
        )
        tracker = ProxyCooldownTracker()

        restricted = await pool.selector(
            url="https://restricted.test/image/1.jpg",
            tracker=tracker,
        ).select()
        unrestricted = await pool.selector(
            url="https://public.test/api/posts",
            tracker=tracker,
        ).select()

        self.assertEqual(restricted.key, "http://japan.test:8080")
        self.assertEqual(unrestricted.key, "http://global.test:8080")

    async def test_route_cache_reuses_origin_and_separates_scheme_or_host(self):
        pool = prepare_proxy_pool({"all": "http://global.test:8080"})
        tracker = ProxyCooldownTracker()

        await pool.selector(
            url="https://api.example.test/first?page=1",
            tracker=tracker,
        ).select()
        await pool.selector(
            url="https://api.example.test/second?page=2",
            tracker=tracker,
        ).select()
        await pool.selector(
            url="http://api.example.test/second?page=2",
            tracker=tracker,
        ).select()
        await pool.selector(
            url="https://cdn.example.test/image.jpg",
            tracker=tracker,
        ).select()

        cache_info = PreparedProxyPool._resolve_route.cache_info()
        self.assertEqual(cache_info.hits, 1)
        self.assertEqual(cache_info.misses, 3)
        self.assertEqual(cache_info.currsize, 3)

    async def test_prepared_proxy_pool_resolves_relative_url_against_current_base_url(self):
        pool = prepare_proxy_pool(
            {
                "https": "http://global.test:8080",
                "https://restricted.test": "http://japan.test:8080",
            }
        )
        tracker = ProxyCooldownTracker()

        restricted = await pool.selector(
            url="/images/1.jpg",
            base_url="https://restricted.test",
            tracker=tracker,
        ).select()
        unrestricted = await pool.selector(
            url="/posts.json",
            base_url="https://public.test",
            tracker=tracker,
        ).select()

        self.assertEqual(restricted.key, "http://japan.test:8080")
        self.assertEqual(unrestricted.key, "http://global.test:8080")

    async def test_mutated_instance_proxy_mapping_refreshes_prepared_pool(self):
        proxies = {"https": "http://proxy-a.test:8080"}
        booru = Booru(
            default_headers=False,
            logger_level=logging.CRITICAL,
            trust_env=False,
            max_attempt_number=1,
            proxies=proxies,
        )
        client = CapturingClient([DummyResponse(), DummyResponse()])
        booru.client = client

        await booru.get("https://example.test/first.json")
        proxies["https"] = "http://proxy-b.test:8080"
        await booru.get("https://example.test/second.json")

        self.assertEqual(
            client.request_history[0]["proxies"],
            {"https": "http://proxy-a.test:8080"},
        )
        self.assertEqual(
            client.request_history[1]["proxies"],
            {"https": "http://proxy-b.test:8080"},
        )

    async def test_prepared_pool_selectors_keep_retry_state_independent(self):
        pool = prepare_proxy_pool(
            ("http://proxy-a.test:8080", "http://proxy-b.test:8080")
        )
        tracker = ProxyCooldownTracker()
        first_request = pool.selector(
            url="https://example.test/first.json",
            tracker=tracker,
        )
        second_request = pool.selector(
            url="https://example.test/second.json",
            tracker=tracker,
        )

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            first_attempt = await first_request.select()
            concurrent_first_attempt = await second_request.select()
            first_retry = await first_request.select()
            concurrent_first_retry = await second_request.select()

        self.assertEqual(first_attempt.key, "http://proxy-a.test:8080")
        self.assertEqual(concurrent_first_attempt.key, "http://proxy-a.test:8080")
        self.assertEqual(first_retry.key, "http://proxy-b.test:8080")
        self.assertEqual(concurrent_first_retry.key, "http://proxy-b.test:8080")

    async def test_empty_prepared_proxy_pool_selects_direct_connection(self):
        selection = await prepare_proxy_pool(()).selector(
            url="https://example.test/data.json",
            tracker=ProxyCooldownTracker(),
        ).select()

        self.assertEqual(selection.proxies, {})
        self.assertEqual(selection.key, "direct")
        self.assertEqual(selection.log, "direct")

    async def test_disabled_cooldown_skips_pool_health_scan(self):
        tracker = ProxyCooldownTracker()
        selector = prepare_proxy_pool(
            tuple(f"http://proxy-{index}.test:8080" for index in range(100))
        ).selector(
            url="https://example.test/data.json",
            tracker=tracker,
        )

        with patch.object(
            tracker,
            "remaining_many",
            wraps=tracker.remaining_many,
        ) as remaining_many:
            await selector.select()

        remaining_many.assert_not_called()

    def test_cooldown_pool_scan_reads_clock_once(self):
        clock = Mock(return_value=0.0)
        tracker = ProxyCooldownTracker(
            threshold=1,
            cooldown_seconds=60,
            clock=clock,
        )
        tracker.record("http://proxy-a.test:8080", failed=True)
        tracker.record("http://proxy-b.test:8080", failed=True)
        clock.reset_mock()

        remaining = tracker.remaining_many(
            [
                "http://proxy-a.test:8080",
                "http://proxy-b.test:8080",
                "http://proxy-a.test:8080",
            ]
        )

        self.assertEqual(
            remaining,
            {
                "http://proxy-a.test:8080": 60.0,
                "http://proxy-b.test:8080": 60.0,
            },
        )
        clock.assert_called_once_with()

    def test_proxy_availability_counts_current_candidate_slots(self):
        proxies = tuple(
            f"http://proxy-{index}.test:8080" for index in range(1000)
        )
        tracker = ProxyCooldownTracker(
            threshold=1,
            cooldown_seconds=60,
        )
        for proxy in proxies[:3]:
            tracker.record(proxy, failed=True)

        selector = prepare_proxy_pool(proxies).selector(
            url="https://example.test/data.json",
            tracker=tracker,
        )

        self.assertEqual(selector.availability(), (997, 1000))
        self.assertEqual(selector.availability_log(), "available=997/1000")

    def test_format_bytes_uses_human_readable_units(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(2 * 1024 * 1024), "2.0 MB")

    def test_request_error_log_handles_missing_prepared_request(self):
        self.assertEqual(
            format_request_error(RequestException("dns failed")),
            "RequestException for <unknown> - dns failed",
        )

    def test_request_error_log_escapes_line_breaks(self):
        error = RequestException(
            "first line\nforged line",
            request=DummyRequest(url="https://example.test/a\r\nforged"),
        )

        self.assertEqual(
            format_request_error(error),
            "RequestException for https://example.test/a\\r\\nforged - first line\\nforged line",
        )

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

    async def test_direct_retry_log_keeps_proxy_context_aligned_with_info_log(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.INFO,
            trust_env=False,
            max_attempt_number=2,
        )
        client = CapturingClient(
            [
                DummyResponse(503, "Service Unavailable"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch("waifuboard.booru.asyncio.sleep"):
            with self.assertLogs("WaifuBoard", level="INFO") as records:
                response = await booru.get("https://example.test/direct.json")

        self.assertEqual(response.status_code, 200)
        retry_log = next(
            record for record in records.output if " retry in " in record
        )
        response_log = next(
            record for record in records.output if " elapsed=" in record
        )
        self.assertIn(
            "GET https://example.test/direct.json retry in",
            retry_log,
        )
        self.assertIn("via direct", retry_log)
        self.assertIn("attempt=2/2", retry_log)
        self.assertIn("via direct", response_log)
        self.assertIn("attempt=2/2", response_log)

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

    async def test_unexpected_404_retries_outer_and_rotates_proxy(self):
        booru = Booru(
            default_headers=False,
            logger_level=logging.WARNING,
            trust_env=False,
            max_attempt_number=2,
            proxies=("http://proxy-a.test:8080", "http://proxy-b.test:8080"),
        )
        client = CapturingClient(
            [
                DummyResponse(404, "Not Found"),
                DummyResponse(200, "OK"),
            ]
        )
        booru.client = client

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            with patch("waifuboard.booru.asyncio.sleep"):
                with self.assertLogs("WaifuBoard", level="WARNING"):
                    response = await booru.get(
                        "https://example.test/rate-limited-as-missing.json"
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
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
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

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
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

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
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

    async def test_redirect_failure_is_recorded_against_final_route_proxy(self):
        initial_proxy = "http://initial.test:8080"
        redirected_proxy = "http://redirected.test:8080"
        booru = Booru(
            default_headers=False,
            logger_level=logging.CRITICAL,
            trust_env=False,
            max_attempt_number=1,
            proxies={
                "https": initial_proxy,
                "https://cdn.example.test": redirected_proxy,
            },
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        booru.client = CapturingClient(
            DummyResponse(
                429,
                "Too Many Requests",
                request_url="https://cdn.example.test/final.jpg",
            )
        )

        await booru.get("https://example.test/image/1.jpg")

        self.assertTrue(booru._proxy_cooldown.is_available(initial_proxy))
        self.assertFalse(booru._proxy_cooldown.is_available(redirected_proxy))

    async def test_redirect_transport_error_is_recorded_against_final_route_proxy(self):
        initial_proxy = "http://initial.test:8080"
        redirected_proxy = "http://redirected.test:8080"
        booru = Booru(
            default_headers=False,
            logger_level=logging.CRITICAL,
            trust_env=False,
            max_attempt_number=1,
            proxies={
                "https": initial_proxy,
                "https://cdn.example.test": redirected_proxy,
            },
            proxy_cooldown_threshold=1,
            proxy_cooldown_seconds=60,
        )
        exc = RequestException(
            "redirect transport failed",
            request=DummyRequest(url="https://cdn.example.test/final.jpg"),
        )
        booru.client = CapturingClient(exc)

        with self.assertRaises(RequestException):
            await booru.get("https://example.test/image/1.jpg")

        self.assertTrue(booru._proxy_cooldown.is_available(initial_proxy))
        self.assertFalse(booru._proxy_cooldown.is_available(redirected_proxy))

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

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
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
            "waifuboard.proxy.pool.random.choice",
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

        with patch(
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            with self.assertLogs("WaifuBoard", level="DEBUG") as records:
                await booru.get("https://example.test/rate-limited.json")
                await booru.get("https://example.test/recovered.json")

        cooldown_log = next(
            record for record in records.output if "proxy.cooldown" in record
        )
        self.assertIn("proxy=http://proxy-a.test:8080", cooldown_log)
        self.assertIn("available=1/2", cooldown_log)
        skip_log = next(record for record in records.output if "proxy.skip" in record)
        self.assertIn("proxy=http://proxy-a.test:8080", skip_log)
        self.assertIn("available=1/2", skip_log)
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
            "waifuboard.proxy.pool.random.choice",
            side_effect=lambda choices: choices[0],
        ):
            with patch(
                "waifuboard.proxy.pool.asyncio.sleep",
                side_effect=advance_clock,
            ) as sleep:
                with self.assertLogs("WaifuBoard", level="DEBUG"):
                    await booru.get("https://example.test/a.json")
                    await booru.get("https://example.test/b.json")
                with self.assertLogs("WaifuBoard", level="WARNING") as records:
                    await booru.get("https://example.test/c.json")

        sleep.assert_awaited_once_with(60.0)
        wait_log = next(
            record
            for record in records.output
            if "All proxies are cooling down" in record
        )
        self.assertIn("available=0/2", wait_log)
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

        with self.assertLogs("WaifuBoard", level="WARNING") as cooldown_records:
            await booru.get("https://example.test/rate-limited.json")

        cooldown_log = next(
            record
            for record in cooldown_records.output
            if "proxy.cooldown" in record
        )
        self.assertIn("proxy=http://proxy.test:8080", cooldown_log)
        self.assertIn("available=0/1", cooldown_log)

        async def advance_clock(seconds):
            now[0] += seconds

        with patch(
            "waifuboard.proxy.pool.asyncio.sleep",
            side_effect=advance_clock,
        ) as sleep:
            with self.assertLogs("WaifuBoard", level="WARNING") as records:
                response = await booru.get("https://example.test/recovered.json")

        self.assertEqual(response.status_code, 200)
        sleep.assert_awaited_once_with(60.0)
        wait_log = next(
            record for record in records.output if "is cooling down" in record
        )
        self.assertIn("Proxy http://proxy.test:8080", wait_log)
        self.assertIn("available=0/1", wait_log)
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
