"""Proxy health tracking and cooldown state."""

import heapq
import time
from collections.abc import Callable, Iterable

from niquests.exceptions import (
    ChunkedEncodingError,
    ConnectionError,
    ContentDecodingError,
    MultiplexingError,
    RequestException,
    RetryError,
    Timeout,
)

DIRECT_PROXY_KEY = "direct"

_PROXY_TRANSPORT_EXCEPTIONS = (
    ChunkedEncodingError,
    ConnectionError,
    ContentDecodingError,
    MultiplexingError,
    RetryError,
    Timeout,
)


def is_proxy_transport_exception(error: BaseException) -> bool:
    """Return whether an exception is evidence of a failed transport path.

    Args:
        error (BaseException): Exception raised while niquests sends or gathers a response.

    Returns:
        bool: True for niquests transport failures that may indicate an unhealthy proxy.
    """
    # niquests 有时直接抛基础 RequestException 表示未细分的连接失败，因此保留精确基类；InvalidURL、HTTPError、JSONDecodeError 等业务/输入子类不能污染代理健康
    return type(error) is RequestException or isinstance(
        error,
        _PROXY_TRANSPORT_EXCEPTIONS,
    )


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
        clock: Callable[[], float] = time.monotonic,
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

    @property
    def has_active_cooldowns(self) -> bool:
        """Return whether at least one proxy still has an active cooldown.

        Returns:
            bool: True when a non-expired cooldown window exists.
        """
        if not self.enabled or not self._cooldown_until:
            # 健康池是绝大多数请求的常态；空字典判断让已启用 threshold 的大型池也能跳过整池 remaining 扫描和时钟读取
            return False

        now = self._clock()
        self._prune_expired(now)
        return bool(self._cooldown_until)

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
        return self._remaining_at(proxy, now)

    def remaining_many(self, proxies: Iterable[str]) -> dict[str, float]:
        """Return cooldown durations for multiple proxies using one clock read.

        Args:
            proxies (Iterable[str]): Raw proxy identities to inspect.

        Returns:
            dict[str, float]: Remaining positive cooldown durations keyed by proxy.
        """
        if not self.enabled:
            return {}

        # 一次代理选择会检查整个候选池；统一读取时钟和清理过期 heap，避免每个候选重复执行相同维护工作
        now = self._clock()
        self._prune_expired(now)
        remaining_by_proxy: dict[str, float] = {}
        for proxy in dict.fromkeys(proxies):
            remaining = self._remaining_at(proxy, now)
            if remaining > 0:
                remaining_by_proxy[proxy] = remaining
        return remaining_by_proxy

    def is_available(self, proxy: str | None) -> bool:
        """Return whether a proxy key can be selected for a new request.

        Args:
            proxy (str | None): Raw proxy identity, direct sentinel, or None for no resolved proxy.

        Returns:
            bool: True when the proxy is direct, unresolved, disabled, or outside cooldown.
        """
        return proxy is None or proxy == DIRECT_PROXY_KEY or self.remaining(proxy) <= 0

    def next_available_in(self, proxies: Iterable[str]) -> float:
        """Return the shortest remaining cooldown among the given proxy keys.

        Args:
            proxies (Iterable[str]): Raw identities for currently unavailable proxies.

        Returns:
            float: Shortest positive cooldown duration, or 0.0 when none remain.
        """
        return min(self.remaining_many(proxies).values(), default=0.0)

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

    def _remaining_at(self, proxy: str, now: float) -> float:
        """Return one proxy's remaining cooldown against an existing timestamp.

        Args:
            proxy (str): Raw proxy identity to inspect.
            now (float): Shared monotonic timestamp for this lookup batch.

        Returns:
            float: Remaining positive cooldown duration, or 0.0 when available.
        """
        remaining = self._cooldown_until.get(proxy, 0.0) - now
        if remaining <= 0:
            self._cooldown_until.pop(proxy, None)
            return 0.0
        return remaining

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
