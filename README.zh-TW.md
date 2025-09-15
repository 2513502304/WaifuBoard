# ***WaifuBoard***

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

用於從圖像板網站（例如 Danbooru、Safebooru、Yandere）非同步下載影像、標籤與中介資料的 API。會忽略已下載的檔案。

## **安裝**

```bash
pip install waifuboard
```

**需求**：Python >= 3.9

## **支援的平台與功能**

| 平台                                    | 帖文（下載） | 合集（下載） |
| --------------------------------------- | ------------ | ------------ |
| [Danbooru](https://danbooru.donmai.us/) | ✅            | ✅            |
| [Safebooru](https://safebooru.org/)     | ✅            | ❌            |
| [Yandere](https://yande.re/post)        | ✅            | ✅            |
| 其他平台                                 | ...          | ...          |

## **使用方式**

**建立用戶端**（例如 DanbooruClient），並**呼叫相對應元件的下載方法**，例如 `client.posts.download(...)` 或 `client.pools.download(...)`。參數請參考程式碼中下載方法的說明字串（docstring）。

```python
import asyncio
from waifuboard import DanbooruClient


async def main():
	# 建立用戶端，用於與 API 互動
	client = DanbooruClient()

	# 下載帖文
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
		concurrency=8,
	)

	# 下載合集
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

如果這個專案對你有幫助，一個小小的 star 將會是我持續開源的不變動力。

## **下載目錄結構**

**目錄樹**：

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

其中 `task` 是下載任務的唯一識別碼（例如：帖文 ID、合集 ID）。

## **貢獻**

歡迎貢獻。若要新增平台或功能：

- **架構**
	- 平台應繼承自 `waifuboard.booru.Booru`（*基底用戶端*），並設定合適的 `base_url` 與元件。
	- 功能/端點（例如 Posts、Pools）應繼承自 `waifuboard.booru.BooruComponent`（*基底元件*），並實作與現有平台一致的 `download(...)` 介面。
	- 重用 `Booru` 的輔助方法（例如 `concurrent_fetch_page`、`concurrent_download_file`、`concurrent_save_raws`、`concurrent_save_tags`）。

- **GitHub 工作流程**
	1. 將此儲存庫 Fork 到你的帳號。
	2. 建立新分支：`git checkout -b feat/<short-name>`。
	3. 實作你的平台/元件，並在此 README 中補充最少文件。
	4. 在本地快速測試，確保基本功能可用。
	5. 提交並推送分支：`git push origin feat/<short-name>`。
	6. 向 `main` 發起 Pull Request，簡要描述變更內容（做了什麼/為什麼/如何測試）。

**準則**
- 保持公開 API 與既有實作一致（方法名稱、參數、回傳值）。
- 為新增方法撰寫說明字串，尤其是 `download(...)` 的參數與行為。
- 遵循現有的程式碼風格與日誌模式。
- 避免破壞性變更；若無法避免，請在 PR 中清楚說明。
