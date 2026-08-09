"""Filename normalization helpers used by WaifuBoard storage methods."""

import re

# * =================================================

# 采用 Windows 的保守字符集生成跨平台文件名；这些字符在 POSIX 文件名中并非全部无效，但移除后可以避免同一下载结果迁移到 Windows 时无法写盘
INVALID_PATH_REGEX: re.Pattern[str] = re.compile(r'[\\/:*?"<>|]')
# 额外移除 glob 语法中的方括号和花括号，避免保存后的普通文件名被后续 glob 查询误解释为模式；* 与 ? 虽已由上一规则覆盖，仍保留在该规则中使其可独立使用
INVALID_GLOB_REGEX: re.Pattern[str] = re.compile(r"[][*?{}]")
# Windows 普通文件名禁止 U+0000 到 U+001F；其中 NUL 也无法通过以 NUL 结尾字符串为边界的 POSIX 文件 API，因此默认清理整段 ASCII 控制字符
ASCII_CONTROL_REGEX: re.Pattern[str] = re.compile(r"[\x00-\x1f]")
# Win32 会在普通路径进入文件系统前识别 DOS 设备名，命中后指向控制台、串口、打印口或空设备，而不是创建同名普通文件；扩展名不会解除这种设备名解析
# COM/LPT 后的单个数字包含 0 到 9，ISO-8859-1 上标 1/2/3 也会被 Windows 当成数字；CONIN$/CONOUT$ 与历史 CLOCK$ 一并保守规避，保证下载目录可跨 Windows 实现使用
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(10)),
        *(f"LPT{index}" for index in range(10)),
        *(f"COM{digit}" for digit in ("\u00b9", "\u00b2", "\u00b3")),
        *(f"LPT{digit}" for digit in ("\u00b9", "\u00b2", "\u00b3")),
    }
)


def normalize_filepath(
    filepath: str,
    regexes: tuple[re.Pattern[str], ...] = (
        INVALID_PATH_REGEX,
        INVALID_GLOB_REGEX,
        ASCII_CONTROL_REGEX,
    ),
) -> str:
    """Return a portable non-empty filename after applying cleanup rules.

    Args:
        filepath (str): Candidate filename to normalize. Directory separators are treated as filename characters and removed by the default rules.
        regexes (tuple[re.Pattern[str], ...]): Ordered substitution patterns whose matches are removed. Passing this argument replaces all default character rules, including ASCII control-character cleanup; callers that override it are responsible for the resulting filesystem compatibility.

    Returns:
        str: Non-empty normalized filename. With the default patterns, the result avoids common Windows, macOS, Linux, and glob compatibility hazards.
    """
    # 顺序应用默认或调用方完整替换后的清理规则；regexes=() 明确表示保留所有字符，后续只执行不可由正则表达的 Windows 尾部与设备名处理
    for regex in regexes:
        filepath = regex.sub("", filepath)

    # Windows shell 与常规 Win32 路径处理不接受尾随空格或点；清理字符后再执行，防止前一步删除字符时暴露新的非法尾部
    filepath = filepath.rstrip(" .")
    if not filepath:
        return "unnamed"

    # DOS 设备名匹配忽略大小写、扩展名以及扩展名前的尾随空格，因此 CON.txt、PRN .log、COM0.log 与 COM¹ 都不能作为普通文件；前缀下划线保留可辨识的原始名称
    basename = filepath.split(".", 1)[0].rstrip(" ")
    if basename.upper() in WINDOWS_RESERVED_NAMES:
        filepath = f"_{filepath}"

    return filepath
