"""Proxy normalization, redaction, resolution, and cooldown helpers."""

import time
import typing
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

from niquests.utils import merge_base_url, select_proxy

DIRECT_PROXY_KEY = "direct"


@dataclass(frozen=True)
class ProxyResolution:
    """Resolved proxy identity for both internal cooldown tracking and logs."""

    key: str | None
    log: str | None


# * =================================================


class ProxyCooldownTracker:
    """Track consecutive per-proxy failures and temporary cooldown windows."""

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_seconds: int | float = 600,
        clock: typing.Callable[[], float] = time.monotonic,
    ):
        if threshold is not None and threshold < 1:
            raise ValueError("proxy cooldown threshold must be None or >= 1")
        if cooldown_seconds < 0:
            raise ValueError("proxy cooldown seconds must be >= 0")

        self.threshold = threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._failures: dict[str, deque[bool]] = {}
        self._cooldown_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        """Return whether cooldown tracking is active for this instance."""
        return self.threshold is not None

    def remaining(self, proxy: str) -> float:
        """Return remaining cooldown seconds for a proxy, pruning expired entries."""
        if not self.enabled:
            return 0.0

        remaining = self._cooldown_until.get(proxy, 0.0) - self._clock()
        if remaining <= 0:
            self._cooldown_until.pop(proxy, None)
            return 0.0

        return remaining

    def is_available(self, proxy: str | None) -> bool:
        """Return whether a proxy key can be selected for a new request."""
        return proxy is None or proxy == DIRECT_PROXY_KEY or self.remaining(proxy) <= 0

    def next_available_in(self, proxies: typing.Iterable[str]) -> float:
        """Return the shortest remaining cooldown among the given proxy keys."""
        remaining_values = [self.remaining(proxy) for proxy in proxies]
        remaining_values = [remaining for remaining in remaining_values if remaining > 0]
        return min(remaining_values, default=0.0)

    def record(self, proxy: str | None, *, failed: bool) -> bool:
        """Record a request outcome and return whether it triggered cooldown."""
        if not self.enabled or proxy is None or proxy == DIRECT_PROXY_KEY:
            return False

        if not failed:
            self._failures.pop(proxy, None)
            return False

        assert self.threshold is not None
        failures = self._failures.setdefault(proxy, deque(maxlen=self.threshold))
        failures.append(True)

        if len(failures) < self.threshold:
            return False

        self._failures.pop(proxy, None)
        self._cooldown_until[proxy] = self._clock() + self.cooldown_seconds
        return True


# * =================================================


def normalize_proxy(value: dict[str, str] | str) -> dict[str, str]:
    """Normalize one proxy candidate into the dict shape expected by niquests."""
    if isinstance(value, str):
        return {"http": value, "https": value}
    return value


def redact_proxy_url(proxy: str) -> str:
    """Return a log-safe proxy URL while keeping enough detail to identify it."""
    parsed = urlparse(proxy)

    if parsed.netloc and "@" in parsed.netloc:
        redacted_netloc = f"***:***@{parsed.netloc.rsplit('@', 1)[1]}"
        return parsed._replace(netloc=redacted_netloc).geturl()

    if not parsed.netloc and "@" in proxy:
        return f"***:***@{proxy.rsplit('@', 1)[1]}"

    return proxy


def resolve_proxy(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> ProxyResolution:
    """Resolve one proxy mapping into a raw cooldown key and redacted log value."""
    # {"no_proxy": "*"} 是 WaifuBoard 在 request-level proxies=None 时注入的
    # 显式直连哨兵；niquests.select_proxy() 对它返回 None，因此需要先识别为 direct。
    if proxies.get("no_proxy") == "*" and len(proxies) == 1:
        return ProxyResolution(key=DIRECT_PROXY_KEY, log=DIRECT_PROXY_KEY)

    request_url = merge_base_url(base_url, url) or url
    proxy = select_proxy(request_url, proxies)

    if proxy is None:
        return ProxyResolution(key=None, log=None)

    # 空字符串来自 requests/niquests 风格的 scheme-level 直连配置，
    # 例如 {"https": ""} 命中了当前请求 scheme。
    if proxy == "":
        return ProxyResolution(key=DIRECT_PROXY_KEY, log=DIRECT_PROXY_KEY)

    return ProxyResolution(key=proxy, log=redact_proxy_url(proxy))


def format_proxy_log(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the redacted proxy value displayed in request logs."""
    return resolve_proxy(url, proxies, base_url).log


def format_proxy_key(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the raw proxy value used for internal cooldown tracking."""
    return resolve_proxy(url, proxies, base_url).key
