# **_WaifuBoard_**

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

画像掲示板サイト（例：Danbooru、Safebooru、Yandere）から画像、タグ、メタデータを非同期でダウンロードするための API。ダウンロード済みのファイルは無視します。

## **インストール**

```bash
pip install waifuboard
```

**要件**：Python >= 3.10

## **対応プラットフォームと機能**

| プラットフォーム                        | 投稿（ダウンロード） | プール（ダウンロード） |
| --------------------------------------- | -------------------- | ---------------------- |
| [Danbooru](https://danbooru.donmai.us/) | ✅                   | ✅                     |
| [Safebooru](https://safebooru.org/)     | ✅                   | ❌                     |
| [Yandere](https://yande.re/post)        | ✅                   | ✅                     |
| その他                                  | ...                  | ...                    |

## **使い方**

**クライアントを作成**（例：DanbooruClient）し、**対応するコンポーネントのダウンロードメソッド**を呼び出します。例：`client.posts.download(...)` や `client.pools.download(...)`。パラメータはコード内のダウンロードメソッドの docstring を参照してください。

```python
import asyncio
import logging

from waifuboard import DanbooruClient


async def main():
	# API とやり取りするためのクライアントを作成
	client = DanbooruClient(
        directory="./downloads",  # 現在のクライアントプラットフォームのファイル保存ルートディレクトリ
        logger_level=logging.INFO,  # ログレベル
        base_url=None,  # 各リクエストに自動的に URL プレフィックス（またはベース URL）を設定する（該当する場合）
        proxies="http://127.0.0.1:7897",  # プロトコルまたはプロトコルとホストからプロキシ URL へのマッピング辞書（例: {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}）。各リクエストに適用される。単一の文字列を指定した場合は http と https の両方に使用される。これらの値からなるタプルも指定可能で、リクエストごとにランダムに 1 つが選ばれる。未指定かつ trust_env=True の場合はプロセス環境のプロキシ設定がデフォルトとして取り込まれ、最終的な優先順位は request > session > env となる
        retries=5,  # リクエストが失敗するまでの自動リトライ回数
        max_attempt_number=3,  # リクエストメソッドのデフォルト外側リトライ予算（tenacity レベル）。リクエストメソッドが自前の max_attempt_number を渡さない場合に使用される。リクエスト時に依然 None の場合は内部呼び出しが 1 回の試行にフォールバックする
        rate_limit=10.0,  # 1 秒あたりの最大リクエスト数
        timeout=None,  # デフォルトのタイムアウト設定。公開メソッドでタイムアウトが指定されていない場合に使用
    )

	# 投稿をダウンロード
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
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
    - プラットフォームは `waifuboard.booru.Booru`（_クライアントベースクラス_）を継承し、適切な `base_url` とコンポーネントを設定します。
    - 機能/エンドポイント（例：Posts、Pools）は `waifuboard.booru.BooruComponent`（_コンポーネント基底クラス_）を継承し、既存プラットフォームと整合する `download(...)` を実装します。
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
