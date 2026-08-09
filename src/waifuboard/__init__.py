"""WaifuBoard: Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""

from .booru import Booru
from .sites import DanbooruClient, SafebooruClient, YandereClient

# Package metadata
__author__ = "ChijiangZhai"
__email__ = "chijiangzhai@gmail.com"
__description__ = """Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""
__version__ = "1.0.14"

__all__ = [
    "Booru",
    "DanbooruClient",
    "SafebooruClient",
    "YandereClient",
]
