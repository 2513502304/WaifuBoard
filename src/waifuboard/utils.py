import time
from collections import deque
import logging
import re
import typing
from urllib.parse import urlparse

from niquests.utils import merge_base_url, select_proxy
from rich.console import Console
from rich.logging import RichHandler
from tenacity import _utils

if typing.TYPE_CHECKING:
    from tenacity import RetryCallState

# * =================================================

console = Console(stderr=True)

# 日志记录
logging.basicConfig(
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.WARNING,
    handlers=[
        RichHandler(
            level=logging.NOTSET,
            console=console,
            rich_tracebacks=True,
            tracebacks_width=None,
            tracebacks_code_width=None,
            tracebacks_show_locals=True,
            tracebacks_suppress=[],
            tracebacks_max_frames=100,
            locals_max_length=None,
            locals_max_string=None,
        )
    ],
    force=False,
)
logger = logging.getLogger("WaifuBoard")

# * =================================================

# 匹配文件名中的无效 Windows/MacOS/Linux 路径字符
INVALID_PATH_REGEX: re.Pattern[str] = re.compile(r'[\\/:*?"<>|]')
# 匹配文件名中的通配符 *, ?, [, ], {, }
INVALID_GLOB_REGEX: re.Pattern[str] = re.compile(r"[][*?{}]")


def normalize_filepath(
    filepath: str,
    regexes: tuple[re.Pattern[str], ...] = (
        INVALID_PATH_REGEX,
        INVALID_GLOB_REGEX,
    ),
) -> str:
    """
    将路径名中的无效字符替换为空字符

    Args:
        filepath (str): 文件路径
        regexes (list[re.Pattern[str]]): 用以匹配无效字符的正则表达式列表

    Returns:
        str: 替换后的路径
    """
    for regex in regexes:
        filepath = regex.sub("", filepath)
    return filepath


# * =================================================


class ProxyCooldownTracker:
    """Track per-proxy consecutive failures and temporary cooldown state."""

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown: int | float = 600,
        clock: typing.Callable[[], float] = time.monotonic,
    ):
        if threshold is not None and threshold < 1:
            raise ValueError("proxy cooldown threshold must be None or >= 1")
        if cooldown < 0:
            raise ValueError("proxy cooldown must be >= 0")

        self.threshold = threshold
        self.cooldown = float(cooldown)
        self._clock = clock
        self._failures: dict[str, deque[bool]] = {}
        self._cooldown_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self.threshold is not None

    def remaining(self, proxy: str) -> float:
        if not self.enabled:
            return 0.0

        remaining = self._cooldown_until.get(proxy, 0.0) - self._clock()
        if remaining <= 0:
            self._cooldown_until.pop(proxy, None)
            return 0.0

        return remaining

    def is_available(self, proxy: str | None) -> bool:
        return proxy is None or proxy == "direct" or self.remaining(proxy) <= 0

    def next_available_in(self, proxies: typing.Iterable[str]) -> float:
        remaining_values = [self.remaining(proxy) for proxy in proxies]
        remaining_values = [remaining for remaining in remaining_values if remaining > 0]
        return min(remaining_values, default=0.0)

    def record(self, proxy: str | None, *, failed: bool) -> bool:
        if not self.enabled or proxy is None or proxy == "direct":
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
        self._cooldown_until[proxy] = self._clock() + self.cooldown
        return True


# * =================================================


def redact_proxy_url(proxy: str) -> str:
    """Return a log-safe proxy URL while keeping enough detail to identify it."""
    parsed = urlparse(proxy)

    if parsed.netloc and "@" in parsed.netloc:
        redacted_netloc = f"***:***@{parsed.netloc.rsplit('@', 1)[1]}"
        return parsed._replace(netloc=redacted_netloc).geturl()

    if not parsed.netloc and "@" in proxy:
        return f"***:***@{proxy.rsplit('@', 1)[1]}"

    return proxy


def format_proxy_log(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    if proxies.get("no_proxy") == "*" and len(proxies) == 1:
        return "direct"

    request_url = merge_base_url(base_url, url) or url
    proxy = select_proxy(request_url, proxies)

    if proxy is None:
        return None

    if proxy == "":
        return "direct"

    return redact_proxy_url(proxy)


def format_proxy_key(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the internal cooldown key without redacting proxy credentials."""
    if proxies.get("no_proxy") == "*" and len(proxies) == 1:
        return "direct"

    request_url = merge_base_url(base_url, url) or url
    proxy = select_proxy(request_url, proxies)

    if proxy is None:
        return None

    if proxy == "":
        return "direct"

    return proxy


# * =================================================


def format_bytes(size: int | None) -> str:
    """Return a compact human-readable byte size for request logs."""
    if size is None:
        return "unknown"

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit = units[0]

    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024

    if unit == "B":
        return f"{int(value)} {unit}"

    return f"{value:.1f} {unit}"


def format_elapsed(seconds: float) -> str:
    """Return elapsed seconds using a stable short precision for logs."""
    return f"{seconds:.3f}s"


def format_response_metrics(
    *,
    attempt_number: int,
    max_attempt_number: int,
    elapsed: float,
    body_size: int | None,
    redirects: int,
    expected_statuses: set[int] | None = None,
) -> str:
    metrics = [
        f"attempt={attempt_number}/{max_attempt_number}",
        f"elapsed={format_elapsed(elapsed)}",
        f"bytes={format_bytes(body_size)}",
        f"redirects={redirects}",
    ]

    if expected_statuses:
        statuses = ",".join(str(status) for status in sorted(expected_statuses))
        metrics.append(f"expected={statuses}")

    return f"({' '.join(metrics)})"


def get_body_size(content: object) -> int | None:
    """Best-effort response body size without forcing another decode pass."""
    if isinstance(content, bytes | bytearray | memoryview):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return None


def format_retry_log(
    *,
    method: str,
    url: str,
    proxy_log: str | None,
    next_attempt: int,
    max_attempt_number: int,
    sleep_seconds: float,
    reason: str,
) -> str:
    proxy_part = f" via {proxy_log}" if proxy_log else ""
    return (
        f"{method} {url} retry in {format_elapsed(sleep_seconds)}{proxy_part} "
        f"(attempt={next_attempt}/{max_attempt_number} reason={reason})"
    )


# * =================================================

# 自 tenacity.before_sleep.before_sleep_log 修改而来
# 改动：在原日志格式中追加 (attempt N/M) 进度信息
# N 取 retry_state.attempt_number + 1，即即将进行的下一次尝试编号（before_sleep 在「刚失败 → 准备下一次」之间触发）
# M 取 retry_state.retry_object.stop.max_attempt_number
# 当 stop 策略不是 stop_after_attempt 或 max_attempt_number 不可达时退化为只显示 N
# 其余逻辑与上游保持一致
# 上游来源：tenacity/before_sleep.py


def before_sleep_log(
    logger: "_utils.LoggerProtocol",
    log_level: int,
    exc_info: bool = False,
    sec_format: str = "%.3g",
) -> typing.Callable[["RetryCallState"], None]:
    """Before sleep strategy that logs to some logger the attempt, with attempt-counter progress."""

    def log_it(retry_state: "RetryCallState") -> None:
        local_exc_info: BaseException | bool | None

        if retry_state.outcome is None:
            raise RuntimeError("log_it() called before outcome was set")

        if retry_state.next_action is None:
            raise RuntimeError("log_it() called before next_action was set")

        if retry_state.outcome.failed:
            ex = retry_state.outcome.exception()
            verb, value = "raised", f"{ex.__class__.__name__}: {ex}"

            if exc_info:
                local_exc_info = retry_state.outcome.exception()
            else:
                local_exc_info = False
        else:
            verb, value = "returned", retry_state.outcome.result()
            local_exc_info = False  # exc_info does not apply when no exception

        if retry_state.fn is None:
            # NOTE(sileht): can't really happen, but we must please mypy
            fn_name = "<unknown>"
        else:
            fn_name = _utils.get_callback_name(retry_state.fn)

        # * === 改动开始 ===
        # 进度信息：N 为即将进行的下一次尝试编号，M 为最大尝试次数（取不到则只显示 N）
        next_attempt = retry_state.attempt_number + 1
        max_attempt = getattr(
            getattr(retry_state.retry_object, "stop", None),
            "max_attempt_number",
            None,
        )
        progress = (
            f"{next_attempt}/{max_attempt}"
            if max_attempt is not None
            else f"{next_attempt}"
        )
        # * === 改动结束 ===

        logger.log(
            log_level,
            f"Retrying {fn_name} "
            # * === 改动开始 ===
            f"(attempt {progress}) "
            # * === 改动结束 ===
            f"in {sec_format % retry_state.next_action.sleep} seconds as it {verb} {value}.",
            exc_info=local_exc_info,
        )

    return log_it
