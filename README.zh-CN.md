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
	client = DanbooruClient()

	# 下载帖子
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
		concurrency=8,
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
		concurrency=8,
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

