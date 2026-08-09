"""Site-specific WaifuBoard clients."""

from .danbooru import DanbooruClient
from .moebooru import YandereClient
from .safebooru import SafebooruClient

__all__ = [
    "DanbooruClient",
    "SafebooruClient",
    "YandereClient",
]
