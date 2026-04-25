import logging
import re
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
