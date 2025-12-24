import logging
import re

from rich.logging import RichHandler

# 提取文件名中的无效 Windows/MacOS/Linux 路径字符规则
INVALID_CHARS_PATTERN = re.compile(r'[\\/:*?"<>|]')

# 日志记录
logging.basicConfig(
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.WARNING,
    handlers=[
        RichHandler(
            level=logging.NOTSET,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            tracebacks_suppress=[],
            tracebacks_max_frames=100,
        )
    ],
    force=False,
)
logger = logging.getLogger("WaifuBoard")
