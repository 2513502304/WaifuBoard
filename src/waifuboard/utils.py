"""General utility helpers and compatibility re-exports for WaifuBoard."""

import re

from .observability import (
    before_sleep_log,
    format_bytes,
    format_elapsed,
    format_response_metrics,
    format_retry_log,
    get_body_size,
    logger,
)
from .proxy import (
    ProxyCooldownTracker,
    format_proxy_key,
    format_proxy_log,
    redact_proxy_url,
    resolve_proxy,
)

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
    Replace invalid path characters with empty strings.

    Args:
        filepath (str): File path to normalize.
        regexes (tuple[re.Pattern[str], ...]): Regex patterns matching invalid path characters.

    Returns:
        str: Normalized file path.
    """
    # 顺序应用所有清理规则，保持调用方传入自定义 regexes 时的确定性。
    for regex in regexes:
        filepath = regex.sub("", filepath)
    return filepath
