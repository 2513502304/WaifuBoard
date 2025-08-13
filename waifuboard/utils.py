from rich.logging import RichHandler
import logging

# 日志记录
logging.basicConfig(
    format='%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO,
    handlers=[RichHandler()],
)

logger = logging.getLogger('WaifuBoard')
