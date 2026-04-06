import logging
import re

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)

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
