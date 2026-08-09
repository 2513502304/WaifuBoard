"""Path normalization helpers used by WaifuBoard storage methods."""

import re

# * =================================================

# 匹配文件名中的无效 Windows/MacOS/Linux 路径字符
INVALID_PATH_REGEX: re.Pattern[str] = re.compile(r'[\\/:*?"<>|]')
# 匹配文件名中的通配符 *, ?, [, ], {, }
INVALID_GLOB_REGEX: re.Pattern[str] = re.compile(r"[][*?{}]")
# ASCII 控制字符在 Windows 文件名中无效，NUL 也会让 POSIX 文件 API 拒绝路径；该规则不允许被调用方通过自定义 regexes 绕过
ASCII_CONTROL_REGEX: re.Pattern[str] = re.compile(r"[\x00-\x1f]")
# Windows 会拒绝这些设备名，即使文件名带有扩展名；跨平台下载任务应在生成路径时统一规避，而不是等到写盘时才失败
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{digit}" for digit in ("\u00b9", "\u00b2", "\u00b3")),
        *(f"LPT{digit}" for digit in ("\u00b9", "\u00b2", "\u00b3")),
    }
)


def normalize_filepath(
    filepath: str,
    regexes: tuple[re.Pattern[str], ...] = (
        INVALID_PATH_REGEX,
        INVALID_GLOB_REGEX,
    ),
) -> str:
    """Return a portable non-empty filename after applying cleanup rules.

    Args:
        filepath (str): File path to normalize.
        regexes (tuple[re.Pattern[str], ...]): Regex patterns matching invalid path characters.

    Returns:
        str: Normalized filename safe for common Windows, macOS, and Linux filesystems.
    """
    # 顺序应用所有清理规则，保持调用方传入自定义 regexes 时的确定性
    for regex in regexes:
        filepath = regex.sub("", filepath)

    # 调用方 regexes 只用于扩展清理策略；基础文件系统安全约束始终由 WaifuBoard 在后续步骤统一执行
    filepath = ASCII_CONTROL_REGEX.sub("", filepath)

    # Windows 不允许文件名以空格或点结尾；清理无效字符后再执行，防止原始字符删除后暴露新的尾随点
    filepath = filepath.rstrip(" .")
    if not filepath:
        return "unnamed"

    # 设备名限制只看第一个点之前的 basename，因此 CON.txt、COM1 与 COM¹ 都需要改名；前缀下划线保留原始名称，且不会与普通清理结果混淆
    basename = filepath.split(".", 1)[0]
    if basename.upper() in WINDOWS_RESERVED_NAMES:
        filepath = f"_{filepath}"

    return filepath
