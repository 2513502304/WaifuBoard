from waifuboard import DanbooruClient, SafebooruClient, YandereClient
from waifuboard.utils import logger
import asyncio
import time


async def main() -> None:
    start = time.time()
    client = DanbooruClient()
    await client.pools.download(
        limit=1000,
        query={
            'search[name_matches]': 'k-on!',
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
