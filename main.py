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
        proxy_url="http://127.0.0.1:7890",  # 连接代理服务器时使用的 URL。URL 的 scheme 必须为 "http", "https", "socks5", "socks5h" 之一，URL 的形式为 {scheme}://{[username]:[password]@}{host}:{port}/ 或 {scheme}://{host}:{port}/，例如 "http://127.0.0.1:8080/"
        proxy_auth=None,  # 任何代理认证信息，格式为 (username, password) 的 two-tuple。可以是 bytes 类型或仅含 ASCII 字符的 str 类型。注意：优先使用 proxy_url 中解析出的 auth 参数，若 proxy_url 中解析不出任何 auth，且 proxy_auth 参数不为 None，则使用 proxy_auth 参数为 proxy_url 添加身份验证凭据
        proxy_headers=None,  # 用于代理请求的任何 HTTP 头部信息。例如 {"Proxy-Authorization": "Basic <username>:<password>"}
        proxy_ssl_context=None,  # 用于验证连接代理服务器的 SSL 上下文。如果未指定，将使用默认的 httpcore.default_ssl_context()
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
