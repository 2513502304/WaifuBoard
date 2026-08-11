"""WaifuBoard: Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""

from .booru import Booru
from .sites import DanbooruClient, SafebooruClient, YandereClient
from .typing import DownloadItem, DownloadResult, PageResult

# Package metadata
__author__ = "ChijiangZhai"
__email__ = "chijiangzhai@gmail.com"
__description__ = """Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""
__version__ = "2.0.1"

__all__ = [
    "Booru",
    "DanbooruClient",
    "SafebooruClient",
    "YandereClient",
    "DownloadItem",
    "DownloadResult",
    "PageResult",
]
