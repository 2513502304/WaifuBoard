import asyncio
import logging
import time

from waifuboard import Booru, DanbooruClient, SafebooruClient, YandereClient
from waifuboard.utils import logger


async def main() -> None:
    start = time.time()
    client = DanbooruClient(
        max_clients=8,  # 最大客户端数量，用以限制全局并发请求数量的上限，这会影响并发率。若为 None 或一个非正数，则不限制该上限
        directory="./downloads",  # 当前客户端平台的存储文件根目录
        max_connections=100,  # 可建立的最大并发连接数
        max_keepalive_connections=20,  # 允许连接池在此数值以下维持长连接的数量。该值应小于或等于 max_connections
        keepalive_expiry=30.0,  # 空闲长连接的时间限制（以秒为单位）
        max_attempt_number=5,  # 最大尝试次数
        default_headers=True,  # 是否设置默认浏览器 headers
        logger_level=logging.INFO,  # 日志级别
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
