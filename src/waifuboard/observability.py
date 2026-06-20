"""Logging and request-observability helpers used by WaifuBoard clients."""

import logging
import typing

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


def format_bytes(size: int | None) -> str:
    """Return a compact human-readable byte size for request logs."""
    if size is None:
        return "unknown"

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit = units[0]

    # 按 1024 进位选择最短可读单位，避免大文件日志写出很长的字节数。
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
    """Format compact response-side metrics appended to the request log line."""
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
    """Format one retry warning line with request context and sleep duration."""
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
    formatter: typing.Callable[["RetryCallState"], str] | None = None,
) -> typing.Callable[["RetryCallState"], None]:
    """Return a tenacity before-sleep callback with attempt progress in logs."""

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

        # * === 改动开始 ===
        # 调用方可以保留 tenacity before_sleep 时机与异常处理语义，同时替换为业务化日志格式。
        # Booru.request 用它输出 method/url/proxy/reason，避免另起一个重复的 log_retry 回调。
        if formatter is not None:
            logger.log(log_level, formatter(retry_state), exc_info=local_exc_info)
            return
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
