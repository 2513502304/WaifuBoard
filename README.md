# **_WaifuBoard_**

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

Asynchronous API for downloading images, tags, and metadata from image board sites (e.g., Danbooru, Safebooru, Yandere). Ignore the downloaded files.

## **Installation**

```bash
pip install waifuboard
```

**Requires**: Python >= 3.10

## **Supported platforms and features**

| Platform                                | Posts (download) | Pools (download) |
| --------------------------------------- | ---------------- | ---------------- |
| [Danbooru](https://danbooru.donmai.us/) | ✅               | ✅               |
| [Safebooru](https://safebooru.org/)     | ✅               | ❌               |
| [Yandere](https://yande.re/post)        | ✅               | ✅               |
| Other platforms                         | ...              | ...              |

## **Usage**

**Create a client** (e.g., DanbooruClient) and **call the download method of the corresponding component**, such as `client.posts.download(...)` or `client.pools.download(...)`. For parameters, please refer to the download method docstrings in the code.

```python
import asyncio
import logging

from waifuboard import DanbooruClient


async def main():
    # Create a client, which will be used to interact with the API
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
    - Platforms should inherit from `waifuboard.booru.Booru` (_Base Client_) and set the appropriate `base_url` and components.
    - Features/endpoints (e.g., Posts, Pools) should inherit from `waifuboard.booru.BooruComponent` (_Base Component_) and implement the `download(...)` interface consistent with existing platforms.
    - Reuse helpers from `Booru` (e.g., `concurrent_fetch_page`, `concurrent_download_file`, `concurrent_save_raws`, `concurrent_save_tags`).

- **GitHub workflow**
    1.  Fork this repository to your account.
    2.  Create a new branch for your change: `git checkout -b feat/<short-name>`.
    3.  Implement your platform/component and add minimal docs in this README.
    4.  Run a quick local test to ensure basic functionality works.
    5.  Commit and push your branch: `git push origin feat/<short-name>`.
    6.  Open a Pull Request to `main` with a concise description (what/why/how to test).

**Guidelines**

- Keep public APIs consistent with existing ones (method names, parameters, return types).
- Add docstrings to new methods, especially `download(...)` parameters and behavior.
- Follow the existing code style and logging patterns.
- Avoid breaking changes; if unavoidable, call them out clearly in the PR.
