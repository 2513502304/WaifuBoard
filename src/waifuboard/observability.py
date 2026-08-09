"""Logging and request-observability helpers used by WaifuBoard clients."""

import logging
import re
import typing
from urllib.parse import urlsplit, urlunsplit

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

_HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = frozenset(".,;:!?")
_TRAILING_URL_BRACKETS = {")": "(", "]": "[", "}": "{"}

# * =================================================


def format_bytes(size: int | None) -> str:
    """Return a compact human-readable byte size for request logs.

    Args:
        size (int | None): Body size in bytes, or None when the size is unknown.

    Returns:
        str: Human-readable size using the shortest suitable binary unit.
    """
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
    """Return elapsed seconds using a stable short precision for logs.

    Args:
        seconds (float): Elapsed or waiting duration in seconds.

    Returns:
        str: Duration formatted with three decimal places and an ``s`` suffix.
    """
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
    """Format compact response-side metrics appended to the request log line.

    Args:
        attempt_number (int): Current outer request attempt number.
        max_attempt_number (int): Maximum number of outer request attempts.
        elapsed (float): End-to-end duration of the completed attempt in seconds.
        body_size (int | None): Response body size in bytes, or None when unknown.
        redirects (int): Number of redirects followed by the request.
        expected_statuses (set[int] | None): Expected status codes matched by the response.

    Returns:
        str: Parenthesized metrics suitable for appending to one request log line.
    """
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
    """Best-effort response body size without forcing another decode pass.

    Args:
        content (object): Cached response content in bytes-like or text form.

    Returns:
        int | None: Encoded body size in bytes, or None for unsupported content types.
    """
    if isinstance(content, bytes | bytearray | memoryview):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return None


def _split_trailing_url_punctuation(url: str) -> tuple[str, str]:
    """Separate prose punctuation accidentally captured after a URL.

    Args:
        url (str): HTTP(S) URL match that may include adjacent punctuation.

    Returns:
        tuple[str, str]: URL content and the trailing punctuation to restore.
    """
    trailing = ""

    # 普通句末标点一定不属于日志中的 URL；右括号仅在 URL 内没有对应左括号时剥离，避免破坏路径中合法的成对括号
    while url:
        last_character = url[-1]
        if last_character in _TRAILING_URL_PUNCTUATION:
            url = url[:-1]
            trailing = last_character + trailing
            continue

        opening_bracket = _TRAILING_URL_BRACKETS.get(last_character)
        if opening_bracket is not None and url.count(last_character) > url.count(
            opening_bracket
        ):
            url = url[:-1]
            trailing = last_character + trailing
            continue

        break

    return url, trailing


def _sanitize_http_url(match: re.Match[str]) -> str:
    """Remove credentials, query data, and fragments from one HTTP(S) URL match.

    Args:
        match (re.Match[str]): Regular-expression match containing an HTTP(S) URL.

    Returns:
        str: Sanitized URL followed by any adjacent prose punctuation.
    """
    url, trailing = _split_trailing_url_punctuation(match.group(0))

    try:
        parsed = urlsplit(url)
    except ValueError:
        # 无法可靠解析时不能回退到原文，因为原文可能正好包含无法识别的凭据或 token
        return f"<redacted-url>{trailing}"

    # userinfo 位于最后一个 @ 之前；仅保留实际 authority，并统一删除所有 query 与 fragment 数据
    authority = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    if not authority:
        return f"<redacted-url>{trailing}"

    sanitized = urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    return f"{sanitized}{trailing}"


def _sanitize_log_text(value: object) -> str:
    """Sanitize HTTP(S) URLs and escape line breaks in log-bound text.

    Args:
        value (object): Value whose string representation will be written to a log.

    Returns:
        str: Single-line text with URL credentials and request secrets removed.
    """
    sanitized = _HTTP_URL_PATTERN.sub(_sanitize_http_url, str(value))
    return sanitized.replace("\r", "\\r").replace("\n", "\\n")


def format_request_error(error: BaseException) -> str:
    """Format a request error even when no prepared request is attached.

    Args:
        error (BaseException): Request or transport exception to describe.

    Returns:
        str: Error class, best-effort request URL, and exception message.
    """
    # DNS、adapter 初始化或测试 double 可能在 PreparedRequest 建立前失败，此时 exception.request 合法地为 None，日志不能再触发二次 AttributeError
    request_url = getattr(getattr(error, "request", None), "url", "<unknown>")
    # 显式请求 URL 与异常正文可能重复携带 userinfo、query token 或 fragment，因此两处必须经过同一脱敏路径；随后转义换行以阻止异常伪造额外日志行
    safe_request_url = _sanitize_log_text(request_url)
    safe_error = _sanitize_log_text(error)
    return f"{error.__class__.__name__} for {safe_request_url} - {safe_error}"


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
    """Format one retry warning line with request context and sleep duration.

    Args:
        method (str): HTTP method of the request being retried.
        url (str): Request URL associated with the retry.
        proxy_log (str | None): Redacted proxy identifier displayed in logs.
        next_attempt (int): Attempt number that will run after the sleep.
        max_attempt_number (int): Maximum number of outer request attempts.
        sleep_seconds (float): Delay before the next attempt in seconds.
        reason (str): Exception or result that caused the retry.

    Returns:
        str: Compact warning message containing retry progress and context.
    """
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
    """Return a tenacity before-sleep callback with attempt progress in logs.

    Args:
        logger (_utils.LoggerProtocol): Logger used to emit retry messages.
        log_level (int): Logging level used for retry messages.
        exc_info (bool): Whether exception tracebacks should be attached to failed outcomes.
        sec_format (str): Format string used for sleep seconds in the default message.
        formatter (Callable[[RetryCallState], str] | None): Optional business-specific message formatter.

    Returns:
        Callable[[RetryCallState], None]: Callback suitable for tenacity's ``before_sleep`` hook.
    """

    def log_it(retry_state: "RetryCallState") -> None:
        """Log one retry state immediately before tenacity sleeps.

        Args:
            retry_state (RetryCallState): Tenacity state for the failed attempt and upcoming sleep.

        Returns:
            None: The callback only emits a log record.
        """
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
