# ***WaifuBoard***

[English README](https://github.com/2513502304/WaifuBoard/blob/main/README.md) | [简体中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-CN.md) | [繁體中文 README](https://github.com/2513502304/WaifuBoard/blob/main/README.zh-TW.md) | [日本語 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ja-JP.md) | [한국어 README](https://github.com/2513502304/WaifuBoard/blob/main/README.ko-KR.md)

이미지 보드 사이트(예: Danbooru, Safebooru, Yandere)에서 이미지, 태그 및 메타데이터를 비동기로 다운로드하기 위한 API입니다. 이미 다운로드한 파일은 무시합니다.

## **설치**

```bash
pip install waifuboard
```

**요구 사항**: Python >= 3.9

## **지원 플랫폼 및 기능**

| 플랫폼                                   | 게시물(다운로드) | 풀(다운로드) |
| --------------------------------------- | ---------------- | ------------ |
| [Danbooru](https://danbooru.donmai.us/) | ✅                | ✅            |
| [Safebooru](https://safebooru.org/)     | ✅                | ❌            |
| [Yandere](https://yande.re/post)        | ✅                | ✅            |
| 기타 플랫폼                              | ...              | ...          |

## **사용 방법**

**클라이언트를 생성**(예: DanbooruClient)하고, `client.posts.download(...)` 또는 `client.pools.download(...)`처럼 **해당 컴포넌트의 다운로드 메서드**를 호출하세요. 매개변수는 코드의 다운로드 메서드 docstring을 참고하세요.

```python
import asyncio
from waifuboard import DanbooruClient


async def main():
	# API와 상호작용할 클라이언트를 생성
	client = DanbooruClient(
        max_clients=8,  # 최대 클라이언트 수는 전역 동시 요청 수의 상한을 제한하는 데 사용되며, 이는 동시성 비율에 영향을 미칩니다. None이거나 양수가 아닌 경우, 이 상한은 제한되지 않습니다.
        directory="./downloads",  # 현재 클라이언트 플랫폼의 파일 저장 루트 디렉토리
        max_connections=100,  # 최대 동시 연결 수
        max_keepalive_connections=20,  # 연결 풀에서 유지할 수 있는 최대 연결 수. max_connections보다 작거나 같아야 합니다.
        keepalive_expiry=30.0,  # 비활성 장시간 연결의 시간 제한(초 단위)
        max_attempt_number=5,  # 최대 시도 횟수
        default_headers=True,  # 기본 브라우저 헤더를 설정할지 여부
        logger_level=logging.INFO,  # 로깅 수준
    )

	# 게시물 다운로드
	await client.posts.download(
		limit=200,
		all_page=True,
		tags="k-on!",
		save_raws=True,
		save_tags=True,
	)

	# 풀 다운로드
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

이 프로젝트가 도움이 되었다면, 작은 별(Star)이 제가 오픈소스를 지속하는 변함없는 원동력이 됩니다.

## **다운로드 디렉터리 구조**

**디렉터리 트리**:

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

`task`는 다운로드 작업의 고유 식별자입니다(예: 게시물 ID, 풀 ID).

## **기여**

기여를 환영합니다. 새 플랫폼이나 기능을 추가하려면:

- **아키텍처**
	- 플랫폼은 `waifuboard.booru.Booru`(• 기본 클라이언트)를 상속하고, 적절한 `base_url`과 컴포넌트를 설정하세요.
	- 기능/엔드포인트(예: Posts, Pools)는 `waifuboard.booru.BooruComponent`(• 기본 컴포넌트)를 상속하고, 기존 플랫폼과 일관된 `download(...)` 인터페이스를 구현하세요.
	- `Booru`의 도우미(`concurrent_fetch_page`, `concurrent_download_file`, `concurrent_save_raws`, `concurrent_save_tags`)를 재사용하세요.

- **GitHub 워크플로우**
	1. 이 저장소를 포크하세요.
	2. 변경 사항을 위한 새 브랜치를 만드세요: `git checkout -b feat/<short-name>`.
	3. 플랫폼/컴포넌트를 구현하고, 이 README에 최소한의 문서를 추가하세요.
	4. 기본 기능이 동작하는지 빠르게 로컬 테스트하세요.
	5. 브랜치를 커밋하고 푸시하세요: `git push origin feat/<short-name>`.
	6. `main`으로 풀 리퀘스트를 열고, 무엇/왜/테스트 방법을 간단히 설명하세요.

**가이드라인**
- 공개 API를 기존과 일관되게 유지하세요(메서드명, 매개변수, 반환값).
- 새로운 메서드에는 docstring을 추가하세요. 특히 `download(...)`의 매개변수와 동작을 명시하세요.
- 기존 코드 스타일과 로깅 방식을 따르세요.
- 파괴적인 변경은 피하세요. 불가피하다면 PR에서 명확히 밝혀주세요.
