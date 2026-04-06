import asyncio
import logging
import time

from waifuboard import Booru, DanbooruClient, SafebooruClient, YandereClient
from waifuboard.utils import logger


async def main() -> None:
    start = time.time()
    client = DanbooruClient(
        directory="./downloads",  # The root directory of the storage files for the current client platform
        logger_level=logging.INFO,  # The log level
        base_url=None,  # Automatically set a URL prefix (or base url) on every request emitted if applicable
        proxies="http://127.0.0.1:7897",  # Dictionary mapping protocol or protocol and host to the URL of the proxy (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to be used on each Request <Request>
        retries=5,  # Configure a number of times a request must be automatically retried before giving up
        timeout=None,  # Default timeout configuration to be used if no timeout is provided in exposed methods
        hooks=None,  # Default hooks to be used on every request emitted. Can be a dictionary mapping hook names to lists of callables, or a LifeCycleHook instance
    )
    await client.pools.download(
        limit=1000,
        query={
            "search[name_matches]": "k-on!",
        },
        all_page=True,
        save_raws=True,
        save_tags=True,
    )
    end = time.time()
    logger.info(f"Total time taken: {end - start:.2f} seconds")


if __name__ == "__main__":
    asyncio.run(main())
