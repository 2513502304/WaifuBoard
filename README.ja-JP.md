# ***WaifuBoard***

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

画像掲示板サイト（例：Danbooru、Safebooru、Yandere）から画像、タグ、メタデータを非同期でダウンロードするための API。ダウンロード済みのファイルは無視します。

## **インストール**

```bash
pip install waifuboard
```

**要件**：Python >= 3.9

## **対応プラットフォームと機能**

| プラットフォーム                         | 投稿（ダウンロード） | プール（ダウンロード） |
| --------------------------------------- | ------------------- | ---------------------- |
| [Danbooru](https://danbooru.donmai.us/) | ✅                   | ✅                      |
| [Safebooru](https://safebooru.org/)     | ✅                   | ❌                      |
| [Yandere](https://yande.re/post)        | ✅                   | ✅                      |
| その他                                  | ...                 | ...                    |

## **使い方**

**クライアントを作成**（例：DanbooruClient）し、**対応するコンポーネントのダウンロードメソッド**を呼び出します。例：`client.posts.download(...)` や `client.pools.download(...)`。パラメータはコード内のダウンロードメソッドの docstring を参照してください。

```python
import asyncio
from waifuboard import DanbooruClient


async def main():
	# API とやり取りするためのクライアントを作成
	client = DanbooruClient()

	# 投稿をダウンロード
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
		concurrency=8,
	)

	# プールをダウンロード
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

このプロジェクトが役に立つと感じたら、Star をいただけると今後のオープンソース活動の励みになります。

## **ダウンロードディレクトリ構造**

**ディレクトリツリー**：

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

`task` はダウンロードタスクの一意の識別子（例：投稿 ID、プール ID）です。

## **コントリビューション**

コントリビューションは歓迎です。新しいプラットフォームや機能を追加する場合：

- **アーキテクチャ**
	- プラットフォームは `waifuboard.booru.Booru`（*クライアントベースクラス*）を継承し、適切な `base_url` とコンポーネントを設定します。
	- 機能/エンドポイント（例：Posts、Pools）は `waifuboard.booru.BooruComponent`（*コンポーネント基底クラス*）を継承し、既存プラットフォームと整合する `download(...)` を実装します。
	- `Booru` のヘルパー（`concurrent_fetch_page`、`concurrent_download_file`、`concurrent_save_raws`、`concurrent_save_tags`）を再利用してください。

- **GitHub ワークフロー**
	1. このリポジトリを Fork します。
	2. 新しいブランチを作成：`git checkout -b feat/<short-name>`。
	3. プラットフォーム/コンポーネントを実装し、この README に最小限のドキュメントを追加します。
	4. ローカルで簡単なテストを実行し、基本機能が動作することを確認します。
	5. ブランチをコミットして push：`git push origin feat/<short-name>`。
	6. `main` に対して Pull Request を作成し、変更点・理由・テスト方法を簡潔に記述します。

**ガイドライン**
- 公開 API の一貫性を保つ（メソッド名、パラメータ、戻り値）。
- 新しいメソッドには docstring を追加し、特に `download(...)` のパラメータと動作を明記する。
- 既存のコードスタイルとロギングの方針に従う。
- 破壊的変更は避ける。避けられない場合は PR で明示する。

