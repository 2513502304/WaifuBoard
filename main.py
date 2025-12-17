import asyncio
import logging
import time

from waifuboard import Booru, DanbooruClient, SafebooruClient, YandereClient
from waifuboard.utils import logger


async def main() -> None:
    start = time.time()
    client = DanbooruClient(
        max_clients=None,
        directory="./downloads",
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
        max_attempt_number=5,
        default_headers=True,
        logger_level=logging.INFO,
    )
    await client.pools.download(
        limit=1000,
        query={
            "search[name_matches]": "k-on!",
        },
        all_page=True,
        concurrency=8,
        save_raws=True,
        save_tags=True,
    )
    end = time.time()
    logger.info(f"Total time taken: {end - start:.2f} seconds")


if __name__ == "__main__":
    asyncio.run(main())
