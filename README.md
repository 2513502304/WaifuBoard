# ***WaifuBoard***

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files.

## **Installation**

```bash
pip install waifuboard
```

**Requires**: Python >= 3.9

## **Supported platforms and features**

| Platform                                | Posts (download) | Pools (download) |
| --------------------------------------- | ---------------- | ---------------- |
| [Danbooru](https://danbooru.donmai.us/) | ✅                | ✅                |
| [Safebooru](https://safebooru.org/)     | ✅                | ❌                |
| [Yandere](https://yande.re/post)        | ✅                | ✅                |
| Other platforms                         | ...              | ...              |

## **Usage**

**Create a client** (e.g., DanbooruClient) and **call the download method of the corresponding component**, such as `client.posts.download(...)` or `client.pools.download(...)`. For parameters, please refer to the download method docstrings in the code.

```python
import asyncio
from waifuboard import DanbooruClient


async def main():
    # Create a client, which will be used to interact with the API
	client = DanbooruClient(
        max_clients=8,  # The maximum number of clients to create, which is used to limit the upper limit of the global concurrent request count. If it is None or a non-positive number, the limit is not applied
        directory="./downloads",  # The root directory for storing files of the current client platform
        proxy_url="http://127.0.0.1:7890",  # The URL used to connect to the proxy server. The scheme must be one of "http", "https", "socks5", "socks5h", and the form is {scheme}://{[username]:[password]@}{host}:{port}/ or {scheme}://{host}:{port}/, for example "http://127.0.0.1:8080/"
        proxy_auth=None,  # Any proxy authentication information, in the format of a two-tuple of (username, password). Can be bytes type or str type containing only ASCII characters. Note: The auth parameter parsed from proxy_url is used first. If no auth is parsed from proxy_url and proxy_auth is not None, the proxy_auth parameter is used to add authentication credentials to proxy_url
        proxy_headers=None,  # Any HTTP header information for proxy requests. For example, {"Proxy-Authorization": "Basic <username>:<password>"}
        proxy_ssl_context=None,  # The SSL context used to verify the connection to the proxy server. If not specified, the default httpcore.default_ssl_context() is used
        max_connections=100,  # The maximum number of concurrent connections that can be established
        max_keepalive_connections=20,  # The number of long connections that can be maintained below this value. This value should be less than or equal to max_connections
        keepalive_expiry=30.0,  # The time limit for idle long connections (in seconds)
        max_attempt_number=5,  # The maximum number of attempts
        default_headers=True,  # Whether to set default browser headers
        logger_level=logging.INFO,  # The log level
    )

	# Download posts
	await client.posts.download(
		limit=200,
        all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
	)

	# Download pools
	await client.pools.download(
		limit=1000,
		query={
            'search[name_matches]': 'k-on!',
        },
        all_page=True,
		save_raws=True,
		save_tags=True,
	)


if __name__ == "__main__":
	asyncio.run(main())
```

If this project is helpful to you, a small star would be the unwavering motivation for me to keep the project open-source.

## **Download directory structure**

**Directory tree**:

```
{directory}/
└─ {Platform}/
	└─ {Component}/
		└─ task/
			├─ images/
			│  └─ ...
			├─ tags/
			│  └─ ...
			└─ raws/
				└─ ...
```

where `task` is the unique identifier for the download task (e.g., post ID, pool ID).

## **Contributing**

Contributions are welcome. To add new platforms or features:

- **Architecture**
	- Platforms should inherit from `waifuboard.booru.Booru` (*Base Client*) and set the appropriate `base_url` and components.
	- Features/endpoints (e.g., Posts, Pools) should inherit from `waifuboard.booru.BooruComponent` (*Base Component*) and implement the `download(...)` interface consistent with existing platforms.
	- Reuse helpers from `Booru` (e.g., `concurrent_fetch_page`, `concurrent_download_file`, `concurrent_save_raws`, `concurrent_save_tags`).

- **GitHub workflow**
	1. Fork this repository to your account.
	2. Create a new branch for your change: `git checkout -b feat/<short-name>`.
	3. Implement your platform/component and add minimal docs in this README.
	4. Run a quick local test to ensure basic functionality works.
	5. Commit and push your branch: `git push origin feat/<short-name>`.
	6. Open a Pull Request to `main` with a concise description (what/why/how to test).

**Guidelines**
- Keep public APIs consistent with existing ones (method names, parameters, return types).
- Add docstrings to new methods, especially `download(...)` parameters and behavior.
- Follow the existing code style and logging patterns.
- Avoid breaking changes; if unavoidable, call them out clearly in the PR.
