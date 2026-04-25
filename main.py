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
        proxies="http://127.0.0.1:7897",  # Dictionary mapping protocol or protocol and host to the URL of the proxy (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to be used on each Request <Request>. If a single string is provided, it will be used for both http and https. It can also be a tuple of such values; an element will be randomly selected per request. When not provided and trust_env is True, the process environment's proxy settings are captured as the default, giving an effective priority of request > session > env
        retries=5,  # Configure a number of times a request must be automatically retried before giving up
        max_attempt_number=3,  # Default outer retry budget (tenacity-level) for request methods. Used when a request method does not pass its own max_attempt_number. If still None at request time, the underlying call falls back to a single attempt
        rate_limit=10.0,  # Maximum requests per second
        timeout=None,  # Default timeout configuration to be used if no timeout is provided in exposed methods
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
