"""Proxy normalization, redaction, resolution, and cooldown helpers."""

import asyncio
import heapq
import logging
import random
import time
import typing
from dataclasses import dataclass
from urllib.parse import urlparse

from niquests.utils import merge_base_url, select_proxy

from .observability import format_elapsed, logger

DIRECT_PROXY_KEY = "direct"


@dataclass(frozen=True)
class ProxyResolution:
    """Resolved proxy identity for both internal cooldown tracking and logs.

    Attributes:
        key (str | None): Raw proxy identity used only for internal health tracking.
        log (str | None): Redacted proxy identity safe to include in logs.
    """

    key: str | None
    log: str | None


@dataclass(frozen=True)
class ProxySelection:
    """One normalized proxy selection and its internal and log-safe identities.

    Attributes:
        proxies (dict[str, str]): Normalized mapping passed to niquests.
        key (str | None): Raw proxy identity used only for internal health tracking.
        log (str | None): Redacted proxy identity safe to include in logs.
    """

    proxies: dict[str, str]
    key: str | None
    log: str | None


@dataclass(frozen=True)
class _ProxyCandidate:
    """One pre-resolved pool candidate with a stable per-request identity.

    Attributes:
        index (int): Stable candidate index within the request-level proxy pool.
        selection (ProxySelection): Reusable normalized and resolved proxy metadata.
    """

    index: int
    selection: ProxySelection


# * =================================================


class ProxyCooldownTracker:
    """Track consecutive per-proxy failures and temporary cooldown windows.

    Failure streaks are reset by a successful request. Reaching ``threshold``
    moves the proxy into a monotonic-clock cooldown window. Expired windows are
    removed lazily during later tracker activity.
    """

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_seconds: int | float = 600,
        clock: typing.Callable[[], float] = time.monotonic,
    ):
        """Initialize per-proxy failure and cooldown tracking.

        Args:
            threshold (int | None): Consecutive failures required to start cooldown, or None to disable tracking.
            cooldown_seconds (int | float): Duration of each cooldown window in seconds.
            clock (Callable[[], float]): Monotonic clock provider used for deadlines and deterministic tests.
        """
        if threshold is not None and threshold < 1:
            raise ValueError("proxy cooldown threshold must be None or >= 1")
        if cooldown_seconds < 0:
            raise ValueError("proxy cooldown seconds must be >= 0")

        self.threshold = threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        # 每个代理只保存当前连续失败窗口；成功时直接删除，比维护累计成功率更符合 threshold 的“连续”语义
        self._failures: dict[str, int] = {}
        # 使用 monotonic deadline 避免系统时间回拨或校时导致 cooldown 提前结束或被异常延长
        self._cooldown_until: dict[str, float] = {}
        # 最小堆让动态的一次性代理也能在后续 tracker 操作中摊销清理，避免每次 record 都全表扫描
        self._cooldown_deadlines: list[tuple[float, str]] = []

    @property
    def enabled(self) -> bool:
        """Return whether cooldown tracking is active for this instance.

        Returns:
            bool: True when a failure threshold is configured, otherwise False.
        """
        return self.threshold is not None

    def remaining(self, proxy: str) -> float:
        """Return remaining cooldown seconds for a proxy, pruning expired entries.

        Args:
            proxy (str): Raw internal proxy identity to inspect.

        Returns:
            float: Remaining cooldown duration in seconds, or 0.0 when available.
        """
        if not self.enabled:
            return 0.0

        now = self._clock()
        self._prune_expired(now)
        remaining = self._cooldown_until.get(proxy, 0.0) - now
        if remaining <= 0:
            self._cooldown_until.pop(proxy, None)
            return 0.0

        return remaining

    def is_available(self, proxy: str | None) -> bool:
        """Return whether a proxy key can be selected for a new request.

        Args:
            proxy (str | None): Raw proxy identity, direct sentinel, or None for no resolved proxy.

        Returns:
            bool: True when the proxy is direct, unresolved, disabled, or outside cooldown.
        """
        return proxy is None or proxy == DIRECT_PROXY_KEY or self.remaining(proxy) <= 0

    def next_available_in(self, proxies: typing.Iterable[str]) -> float:
        """Return the shortest remaining cooldown among the given proxy keys.

        Args:
            proxies (Iterable[str]): Raw identities for currently unavailable proxies.

        Returns:
            float: Shortest positive cooldown duration, or 0.0 when none remain.
        """
        remaining_values = [self.remaining(proxy) for proxy in proxies]
        remaining_values = [remaining for remaining in remaining_values if remaining > 0]
        return min(remaining_values, default=0.0)

    def record(self, proxy: str | None, *, failed: bool) -> bool:
        """Record a request outcome and return whether it triggered cooldown.

        Args:
            proxy (str | None): Raw internal proxy identity associated with the request.
            failed (bool): Whether the outcome counts as a proxy-health failure.

        Returns:
            bool: True only when this outcome starts a new cooldown window.
        """
        if not self.enabled or proxy is None or proxy == DIRECT_PROXY_KEY:
            return False

        now = self._clock()
        self._prune_expired(now)

        if not failed:
            # 任意一次成功都会打断“连续失败”，但不会提前解除另一个并发请求已经触发的 cooldown
            self._failures.pop(proxy, None)
            return False

        assert self.threshold is not None
        failure_count = self._failures.get(proxy, 0) + 1
        if failure_count < self.threshold:
            self._failures[proxy] = failure_count
            return False

        self._failures.pop(proxy, None)
        # 触发 cooldown 后清空 streak，恢复后必须重新累计完整 threshold 才会再次禁用
        deadline = now + self.cooldown_seconds
        self._cooldown_until[proxy] = deadline
        heapq.heappush(self._cooldown_deadlines, (deadline, proxy))
        return True

    def _prune_expired(self, now: float) -> None:
        """Remove expired cooldowns without scanning every tracked proxy.

        Args:
            now (float): Current monotonic timestamp used as the expiry boundary.

        Returns:
            None: Expired entries are removed from the tracker in place.
        """
        while self._cooldown_deadlines and self._cooldown_deadlines[0][0] <= now:
            deadline, proxy = heapq.heappop(self._cooldown_deadlines)
            # 同一个 proxy 可能在旧 heap entry 到期前进入了新的 cooldown；只有 deadline 仍匹配当前窗口时才能删除
            if self._cooldown_until.get(proxy) == deadline:
                self._cooldown_until.pop(proxy, None)


# * =================================================


def normalize_proxy(value: dict[str, str] | str) -> dict[str, str]:
    """Normalize one proxy candidate into the dict shape expected by niquests.

    Args:
        value (dict[str, str] | str): Proxy mapping or one URL used for both HTTP schemes.

    Returns:
        dict[str, str]: Proxy mapping accepted by niquests request methods.
    """
    if isinstance(value, str):
        return {"http": value, "https": value}
    return value


def redact_proxy_url(proxy: str) -> str:
    """Return a log-safe proxy URL while keeping enough detail to identify it.

    Args:
        proxy (str): Raw proxy URL that may contain credentials.

    Returns:
        str: Proxy URL with username and password replaced by redaction markers.
    """
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
    """Resolve one proxy mapping into a raw cooldown key and redacted log value.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        ProxyResolution: Internal proxy key and corresponding redacted log value.
    """
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


# * =================================================


class ProxySelector:
    """Select normalized proxies while honoring cooldown and per-request rotation.

    Pool candidates are normalized and resolved once when the selector is
    created. Repeated outer attempts then select available candidates without
    replacement until every available candidate has been tried once.
    """

    def __init__(
        self,
        *,
        url: str,
        proxies: (
            dict[str, str]
            | str
            | tuple[dict[str, str], ...]
            | tuple[str, ...]
        ),
        tracker: ProxyCooldownTracker,
        base_url: str | None = None,
    ):
        """Prepare reusable proxy candidates for one Booru request.

        Args:
            url (str): Absolute or relative request URL used for proxy resolution.
            proxies (dict[str, str] | str | tuple[dict[str, str], ...] | tuple[str, ...]): Single proxy configuration or request-level proxy pool.
            tracker (ProxyCooldownTracker): Shared health tracker used to skip cooling proxies.
            base_url (str | None): Session base URL used to resolve relative request URLs.
        """
        self._tracker = tracker
        self._used_candidate_indexes: set[int] = set()

        if isinstance(proxies, tuple):
            self._single: ProxySelection | None = None
            # URL、base_url 与候选池在单次 Booru.request 生命周期内不变，因此 normalize/select_proxy/脱敏只需执行一次
            self._candidates = tuple(
                _ProxyCandidate(
                    index=index,
                    selection=self._resolve_selection(url, candidate, base_url),
                )
                for index, candidate in enumerate(proxies)
            )
        else:
            self._candidates = ()
            self._single = self._resolve_selection(url, proxies, base_url)

    @staticmethod
    def _resolve_selection(
        url: str,
        proxy: dict[str, str] | str,
        base_url: str | None,
    ) -> ProxySelection:
        """Normalize and resolve one candidate into reusable request metadata.

        Args:
            url (str): Absolute or relative request URL used for proxy resolution.
            proxy (dict[str, str] | str): Proxy mapping or URL to normalize.
            base_url (str | None): Session base URL used to resolve relative request URLs.

        Returns:
            ProxySelection: Normalized request mapping plus internal and log-safe identities.
        """
        normalized = normalize_proxy(proxy)
        resolution = resolve_proxy(url, normalized, base_url)
        return ProxySelection(
            proxies=normalized,
            key=resolution.key,
            log=resolution.log,
        )

    async def select(self) -> ProxySelection:
        """Return the next available proxy, waiting when every choice is cooling.

        Returns:
            ProxySelection: Available proxy selected for the next outer request attempt.
        """
        if self._single is not None:
            return await self._select_single()

        if not self._candidates:
            # 空 tuple 没有可轮换候选，沿用当前分支的直连回退行为而不是让 random.choice 抛 IndexError
            return ProxySelection(proxies={}, key=None, log=None)

        while True:
            available_candidates: list[_ProxyCandidate] = []
            cooling_down_keys: list[str] = []

            for candidate in self._candidates:
                selection = candidate.selection
                # remaining 同时完成过期项惰性清理；单次读取即可判断 availability，避免先 is_available 再重复查 deadline
                remaining = (
                    self._tracker.remaining(selection.key)
                    if selection.key not in (None, DIRECT_PROXY_KEY)
                    else 0.0
                )
                if remaining <= 0:
                    available_candidates.append(candidate)
                    continue

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"proxy.skip proxy={selection.log} reason=cooldown "
                        f"remaining={format_elapsed(remaining)}"
                    )
                if selection.key is not None:
                    cooling_down_keys.append(selection.key)

            if available_candidates:
                # 外层 retry 优先使用本次 request 尚未尝试过的代理，避免 random.choice 再次抽中同一个死代理；全部尝试过后才开启下一轮
                unused_candidates = [
                    candidate
                    for candidate in available_candidates
                    if candidate.index not in self._used_candidate_indexes
                ]
                if not unused_candidates:
                    self._used_candidate_indexes.clear()
                    unused_candidates = available_candidates

                selected = random.choice(unused_candidates)
                self._used_candidate_indexes.add(selected.index)
                return selected.selection

            wait_seconds = self._tracker.next_available_in(cooling_down_keys)
            logger.warning(
                "All proxies are cooling down; waiting "
                f"{format_elapsed(wait_seconds)} before retrying proxy selection."
            )
            # 用户要求代理池不可用时等待最早恢复项；该等待属于代理调度，不消耗 tenacity attempt 或 HTTP timeout
            await asyncio.sleep(wait_seconds)

    async def _select_single(self) -> ProxySelection:
        """Wait for and return the only configured proxy selection.

        Returns:
            ProxySelection: Single proxy after any active cooldown has expired.
        """
        assert self._single is not None

        while True:
            remaining = (
                self._tracker.remaining(self._single.key)
                if self._single.key not in (None, DIRECT_PROXY_KEY)
                else 0.0
            )
            if remaining <= 0:
                break
            logger.warning(
                f"Proxy {self._single.log} is cooling down; waiting "
                f"{format_elapsed(remaining)} before retrying proxy selection."
            )
            # 单代理没有替代候选，只能等待 cooldown 到期；直接失败会破坏“无可用代理时 await”的既定行为
            await asyncio.sleep(remaining)

        return self._single


def format_proxy_log(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the redacted proxy value displayed in request logs.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        str | None: Redacted selected proxy, ``direct``, or None when unresolved.
    """
    return resolve_proxy(url, proxies, base_url).log


def format_proxy_key(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the raw proxy value used for internal cooldown tracking.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        str | None: Raw selected proxy, ``direct``, or None when unresolved.
    """
    return resolve_proxy(url, proxies, base_url).key
