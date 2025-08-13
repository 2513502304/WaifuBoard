"""WaifuBoard: Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""

from .danbooru import DanbooruClient
from .safebooru import SafebooruClient
from .moebooru import YandereClient

# Package metadata
__author__ = "ChijiangZhai"
__email__ = "chijiangzhai@gmail.com"
__description__ = """Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files."""

# Version metadata (resolved from installed package metadata if available)
try:
    from importlib import metadata
except ImportError:
    # Running on pre-3.8 Python; use importlib-metadata package
    import importlib_metadata as metadata
try:
    __version__ = metadata.version("waifuboard")
except metadata.PackageNotFoundError:
    __version__ = "0.dev0+unknown"

__all__ = [
    'DanbooruClient',
    'SafebooruClient',
    'YandereClient',
]
