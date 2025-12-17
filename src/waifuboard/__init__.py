"""WaifuBoard: Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""

from .booru import Booru
from .danbooru import DanbooruClient
from .safebooru import SafebooruClient
from .moebooru import YandereClient

# Package metadata
__author__ = "ChijiangZhai"
__email__ = "chijiangzhai@gmail.com"
__description__ = """Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""
__version__ = "0.1.4"

__all__ = [
    "Booru",
    "DanbooruClient",
    "SafebooruClient",
    "YandereClient",
]
