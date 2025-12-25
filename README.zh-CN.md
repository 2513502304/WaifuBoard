# ***WaifuBoard***

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

用于从图像板站点（例如 Danbooru、Safebooru、Yandere）异步下载图像、标签和元数据的 API。忽略已下载的文件。

## **安装**

```bash
pip install waifuboard
```

**要求**：Python >= 3.9

## **支持的平台和功能**

| 平台                                    | 帖子（下载） | 画集（下载） |
| --------------------------------------- | ------------ | ------------ |
| [Danbooru](https://danbooru.donmai.us/) | ✅            | ✅            |
| [Safebooru](https://safebooru.org/)     | ✅            | ❌            |
| [Yandere](https://yande.re/post)        | ✅            | ✅            |
| 其他平台                                 | ...          | ...          |

## **使用**

**创建一个客户端**（例如 DanbooruClient），并**调用对应组件的下载方法**，例如 `client.posts.download(...)` 或 `client.pools.download(...)`。参数请参考代码中下载方法的文档字符串。

```python
import asyncio
from waifuboard import DanbooruClient


async def main():
	# 创建一个客户端，用于与 API 交互
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

	# 下载帖子
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
	)

	# 下载画集
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

如果这个项目对你有帮助，一个小小的 star 将是我持续开源的不变动力。

## **下载目录结构**

**目录树**：

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

其中 `task` 是下载任务的唯一标识（例如，帖子 ID、画集 ID）。

## **贡献**

欢迎贡献。若要添加新平台或功能：

- **架构**
	- 平台应继承自 `waifuboard.booru.Booru`（*客户端基类*），并设置合适的 `base_url` 和组件。
	- 功能/端点（例如 Posts、Pools）应继承自 `waifuboard.booru.BooruComponent`（*组件基类*），并实现与现有平台一致的 `download(...)` 接口。
	- 复用 `Booru` 的辅助方法（例如 `concurrent_fetch_page`、`concurrent_download_file`、`concurrent_save_raws`、`concurrent_save_tags`）。

- **GitHub 工作流**
	1. 将此仓库 Fork 到你的账号。
	2. 新建分支：`git checkout -b feat/<short-name>`。
	3. 实现你的平台/组件，并在本 README 中补充必要说明。
	4. 本地快速测试，确保基础功能可用。
	5. 提交并推送分支：`git push origin feat/<short-name>`。
	6. 向 `main` 提交 Pull Request，简要说明变更内容、原因及测试方式。

**指南**
- 保持公共 API 与现有实现一致（方法名、参数、返回值）。
- 为新增方法添加文档字符串，尤其是 `download(...)` 的参数与行为说明。
- 遵循现有代码风格与日志模式。
- 避免破坏性变更；若不可避免，请在 PR 中清晰说明。

