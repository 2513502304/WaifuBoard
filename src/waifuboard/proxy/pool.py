"""Prepared proxy configuration, resolution, and per-request selection."""

import asyncio
import logging
import random
from functools import lru_cache
from typing import Any, Literal, TypeAlias, cast
from urllib.parse import urlparse
from urllib.request import getproxies

from niquests.utils import (
    get_environ_proxies,
    merge_base_url,
    parse_scheme,
    select_proxy,
    should_bypass_proxies,
)
from pydantic import BaseModel, ConfigDict

from ..observability import format_elapsed, logger
from .cooldown import DIRECT_PROXY_KEY, ProxyCooldownTracker

ProxyCandidateType: TypeAlias = dict[str, str] | str
ProxyPoolType: TypeAlias = ProxyCandidateType | tuple[ProxyCandidateType, ...]
_ProxyCandidateCacheKey: TypeAlias = tuple[Literal["mapping", "url"], Any]
_ProxyConfigCacheKey: TypeAlias = tuple[Literal["pool", "single"], Any]
_EnvironmentProxySnapshot: TypeAlias = tuple[
    str | None,
    tuple[tuple[str, str], ...],
]
_EnvironmentProxySnapshots: TypeAlias = tuple[_EnvironmentProxySnapshot, ...]

# prepared cache 的一项代表一整份代理配置快照，而不是池中的单个 proxy；16 项可容纳常见的一个全局池与少量地域性 request override，并为临时配置留出余量
# 每项同时持有内容化 cache key 与规范化代理池，大型配置可能包含数千个 mapping，因此不能按 niquests.proxy_manager 那种“一项只对应一个实际 proxy”的无界缓存处理
# 该上限只约束跨客户端共享查找：LRU 淘汰会删除 cache 对 prepared pool 的引用，但 Booru 实例还保存着自己的强引用，因此已在使用的 pool 对象及其后续请求不会失效；只有未来再次按同一配置查找时可能需要重建
PREPARED_PROXY_CACHE_SIZE = 16
# niquests.proxy_manager 的每个 cache value 只对应一个实际使用过的 proxy，而这里每个 route value 都包含整份代理池的解析结果，不能按 niquests 的无界字典处理
# functools.lru_cache 装饰的是类方法函数，因此 32 项由所有 PreparedProxyPool 实例共同使用，cache key 包含“PreparedProxyPool 实例 + scheme/authority + 环境代理快照”，并会在对应 route 淘汰前保持该 pool 存活
# 同一 origin 的不同 path/query 复用整池解析结果，而不同 scheme/host 必须分开，因为 niquests 支持 host-specific proxy key 且 no_proxy 也按目标主机判断；32 项可覆盖少量配置访问常见 API/CDN origin，同时限制大型代理池解析快照的内存占用
PROXY_ROUTE_CACHE_SIZE = 32


class ProxyResolution(BaseModel):
    """Resolved proxy identity for both internal cooldown tracking and logs.

    Attributes:
        key (str | None): Raw proxy identity used only for internal health tracking.
        log (str | None): Redacted proxy identity safe to include in logs.
    """

    model_config = ConfigDict(frozen=True)

    key: str | None
    log: str | None


class ProxySelection(ProxyResolution):
    """One normalized proxy selection and its internal and log-safe identities.

    Attributes:
        proxies (dict[str, str]): Normalized mapping passed to niquests.
        key (str | None): Raw proxy identity used only for internal health tracking.
        log (str | None): Redacted proxy identity safe to include in logs.
    """

    proxies: dict[str, str]


class _ProxyCandidate(BaseModel):
    """One resolved pool candidate with a stable per-request identity.

    Attributes:
        index (int): Stable candidate index within the prepared proxy pool.
        selection (ProxySelection): Reusable normalized and resolved proxy metadata.
    """

    model_config = ConfigDict(frozen=True)

    index: int
    selection: ProxySelection


# * =================================================


def normalize_proxy(value: ProxyCandidateType) -> dict[str, str]:
    """Normalize one proxy candidate into the dict shape expected by niquests.

    Args:
        value (ProxyCandidateType): Proxy mapping or one URL used for both HTTP schemes.

    Returns:
        dict[str, str]: Independent proxy mapping accepted by niquests request methods.
    """
    if isinstance(value, str):
        return {"http": value, "https": value}
    # prepared pool 会跨请求复用，因此必须复制用户 mapping，避免调用方后续修改使 cache key 与实际内容不一致
    return value.copy()


def is_immutable_proxy_pool(proxies: ProxyPoolType | None) -> bool:
    """Return whether a proxy configuration cannot change after preparation.

    Args:
        proxies (ProxyPoolType | None): Proxy configuration to classify.

    Returns:
        bool: True for None, one string, or a tuple containing only strings.
    """
    return proxies is None or isinstance(proxies, str) or (
        isinstance(proxies, tuple)
        and all(isinstance(candidate, str) for candidate in proxies)
    )


def redact_proxy_url(proxy: str) -> str:
    """Return a log-safe proxy URL while keeping enough detail to identify it.

    Args:
        proxy (str): Raw proxy URL that may contain credentials.

    Returns:
        str: Proxy URL with username and password replaced by redaction markers.
    """
    parsed = urlparse(proxy)

    if parsed.netloc and "@" in parsed.netloc:
        redacted_netloc = f"***:***@{parsed.netloc.rsplit('@', 1)[1]}"
        return parsed._replace(netloc=redacted_netloc).geturl()

    if not parsed.netloc and "@" in proxy:
        return f"***:***@{proxy.rsplit('@', 1)[1]}"

    return proxy


def resolve_proxy(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> ProxyResolution:
    """Resolve one proxy mapping into a raw cooldown key and redacted log value.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        ProxyResolution: Internal proxy key and corresponding redacted log value.
    """
    # {"no_proxy": "*"} 是 WaifuBoard 在 request-level proxies=None 时注入的显式直连哨兵，它用 request-level 配置压过 env/session 代理
    # niquests.select_proxy() 对该哨兵返回 None；这里需要在调用 select_proxy 前先记为 direct，否则无法在日志与 cooldown identity 中区分“显式直连”和“没有解析到代理”
    if proxies.get("no_proxy") == "*" and len(proxies) == 1:
        return ProxyResolution(key=DIRECT_PROXY_KEY, log=DIRECT_PROXY_KEY)

    request_url = merge_base_url(base_url, url) or url
    proxy = select_proxy(request_url, proxies)

    if proxy is None:
        return ProxyResolution(key=None, log=None)

    # 空字符串是另一种直连路径：它来自 requests/niquests 风格的 scheme-level 配置，例如 {"https": ""} 命中当前请求 scheme
    # 这个分支不能和上方 no_proxy="*" 合并：前者是 select_proxy 返回的实际值，后者必须在 select_proxy 把语义折叠成 None 之前识别
    if proxy == "":
        return ProxyResolution(key=DIRECT_PROXY_KEY, log=DIRECT_PROXY_KEY)

    return ProxyResolution(key=proxy, log=redact_proxy_url(proxy))


def _finalize_proxy_resolution(resolution: ProxyResolution) -> ProxyResolution:
    """Convert a fully resolved no-proxy route into an explicit direct identity.

    Args:
        resolution (ProxyResolution): Proxy result after request, session, and environment policies have been applied.

    Returns:
        ProxyResolution: Original proxy identity, or the explicit direct sentinel when no proxy matched.
    """
    if resolution.key is None:
        # resolve_proxy 的 None 在合并环境代理前仍表示“尚未解析”；只有调用方确认全部代理来源都已合并后，才能把它收敛成真实的 direct 路由
        return ProxyResolution(key=DIRECT_PROXY_KEY, log=DIRECT_PROXY_KEY)
    return resolution


def _proxy_route_url(url: str, base_url: str | None) -> str:
    """Return the URL portion that can affect niquests proxy routing.

    Args:
        url (str): Absolute or relative request URL.
        base_url (str | None): Optional session base URL used for relative requests.

    Returns:
        str: URL containing only scheme and authority for bounded route caching.
    """
    request_url = merge_base_url(base_url, url) or url
    parsed = urlparse(request_url)
    # select_proxy 根据 scheme/hostname 匹配代理，而 no_proxy 还可能按 netloc/port 判断旁路；因此保留完整 authority 作为保守边界，path/query/fragment 则不会改变代理路由，可从 cache key 删除
    return parsed._replace(path="", params="", query="", fragment="").geturl()


# * =================================================


class PreparedProxyPool:
    """Store immutable normalized proxies and cache URL-dependent resolutions.

    A prepared pool contains no request-local retry state and can therefore be
    shared safely by concurrent requests. Each call to :meth:`selector` creates
    an independent selector that tracks only that request's attempted proxies.
    """

    def __init__(self, proxies: ProxyPoolType):
        """Normalize a reusable proxy configuration snapshot.

        Args:
            proxies (ProxyPoolType): Single proxy configuration or ordered proxy pool.
        """
        self._is_pool = isinstance(proxies, tuple)
        candidates = proxies if isinstance(proxies, tuple) else (proxies,)
        # tuple 的顺序、重复项和空池语义都必须保留：重复候选可被用户用作随机权重
        self._normalized_candidates = tuple(
            normalize_proxy(candidate) for candidate in candidates
        )
        # no_proxy 可以按候选配置；初始化时提取唯一值，request 热路径只需为每种策略读取一次环境代理，而不是按候选数量重复读取
        self._no_proxy_values = tuple(
            dict.fromkeys(
                candidate.get("no_proxy")
                for candidate in self._normalized_candidates
            )
        ) or (None,)

    def selector(
        self,
        *,
        url: str,
        tracker: ProxyCooldownTracker,
        base_url: str | None = None,
        trust_env: bool = False,
    ) -> "ProxySelector":
        """Create independent retry selection state for one logical request.

        Args:
            url (str): Absolute or relative request URL used for proxy resolution.
            tracker (ProxyCooldownTracker): Shared health tracker used to skip cooling proxies.
            base_url (str | None): Session base URL used to resolve relative request URLs.
            trust_env (bool): Whether niquests will merge process environment proxies into the request.

        Returns:
            ProxySelector: Request-local selector backed by cached prepared metadata.
        """
        route_url = _proxy_route_url(url, base_url)
        environment_snapshots = (
            self._environment_proxy_snapshots(route_url) if trust_env else ()
        )
        return ProxySelector(
            candidates=self._resolve_route(route_url, environment_snapshots),
            is_pool=self._is_pool,
            tracker=tracker,
        )

    @lru_cache(maxsize=PROXY_ROUTE_CACHE_SIZE)
    def _resolve_route(
        self,
        route_url: str,
        environment_snapshots: _EnvironmentProxySnapshots,
    ) -> tuple[_ProxyCandidate, ...]:
        """Resolve every prepared candidate once for one proxy-routing origin.

        Args:
            route_url (str): Scheme-and-authority URL used by niquests ``select_proxy``.
            environment_snapshots (_EnvironmentProxySnapshots): Current environment proxy mappings grouped by candidate ``no_proxy`` value.

        Returns:
            tuple[_ProxyCandidate, ...]: Resolved candidates in original pool order.
        """
        environment_by_no_proxy = {
            no_proxy: dict(items)
            for no_proxy, items in environment_snapshots
        }
        if not self._normalized_candidates and environment_snapshots:
            # 显式空 tuple 没有候选，但 niquests 在 trust_env=True 时仍会把空 request mapping 与环境代理合并；构造一个虚拟候选才能让实际连接、日志和 cooldown 保持一致
            effective_proxies = environment_by_no_proxy[None]
            if effective_proxies:
                resolution = _finalize_proxy_resolution(
                    resolve_proxy(route_url, effective_proxies)
                )
                return (
                    _ProxyCandidate(
                        index=0,
                        selection=ProxySelection(
                            proxies=effective_proxies,
                            key=resolution.key,
                            log=resolution.log,
                        ),
                    ),
                )

        resolved_candidates = []
        for index, proxies in enumerate(self._normalized_candidates):
            if environment_snapshots:
                # AsyncSession.merge_environment_settings 使用 environment 在前、request mapping 在后的合并顺序；prepared metadata 必须使用同一 effective mapping，才能让日志与 cooldown identity 对应实际连接代理
                effective_proxies = {
                    **environment_by_no_proxy[proxies.get("no_proxy")],
                    **proxies,
                }
            else:
                effective_proxies = proxies
            # environment snapshot 已在上方合并完成；此处仍未命中代理就代表 niquests 会直连，提前保存 direct 可让 INFO/retry/cooldown 日志使用同一身份
            resolution = _finalize_proxy_resolution(
                resolve_proxy(route_url, effective_proxies)
            )
            resolved_candidates.append(
                _ProxyCandidate(
                    index=index,
                    selection=ProxySelection(
                        proxies=effective_proxies,
                        key=resolution.key,
                        log=resolution.log,
                    ),
                )
            )
        return tuple(resolved_candidates)

    def _environment_proxy_snapshots(
        self,
        route_url: str,
    ) -> _EnvironmentProxySnapshots:
        """Capture hashable environment mappings for every candidate bypass policy.

        Args:
            route_url (str): Scheme-and-authority URL used to evaluate environment proxy bypass rules.

        Returns:
            _EnvironmentProxySnapshots: Current environment mappings suitable for a route-cache key.
        """
        if self._no_proxy_values == ("*",):
            # 显式 direct 已通过 no_proxy="*" 压过所有环境代理；跳过 getproxies 可避免每个 request-level proxies=None 请求读取无用的系统配置
            return (("*", ()),)

        # 环境变量可能在长时间运行的进程中变化；把当前快照放进 route-cache key，既复用稳定环境下的解析结果，也不会在变化后继续使用陈旧代理
        # urllib/niquests 的 get_environ_proxies 会先调用 getproxies 再判断 no_proxy；这里先读取一次共享快照，避免候选池包含多种 bypass 规则时重复查询系统代理配置
        environment_proxies = getproxies()
        return tuple(
            (
                no_proxy,
                (
                    ()
                    if not environment_proxies
                    or should_bypass_proxies(route_url, no_proxy=no_proxy)
                    else tuple(sorted(environment_proxies.items()))
                ),
            )
            for no_proxy in self._no_proxy_values
        )


def _candidate_cache_key(candidate: ProxyCandidateType) -> _ProxyCandidateCacheKey:
    """Convert one mutable proxy candidate into a stable cache key.

    Args:
        candidate (ProxyCandidateType): Proxy URL or mapping supplied by the caller.

    Returns:
        _ProxyCandidateCacheKey: Tagged, hashable snapshot of the candidate.
    """
    if isinstance(candidate, str):
        return ("url", candidate)
    # 不能按 dict identity 缓存：调用方原地修改 mapping 时对象 id 不变；按当前内容排序生成快照后，修改会自然形成新 key，而键顺序不同但语义相同的 mapping 仍可共享缓存
    return ("mapping", tuple(sorted(candidate.items())))


def _proxy_config_cache_key(proxies: ProxyPoolType) -> _ProxyConfigCacheKey:
    """Convert one proxy configuration into a stable content-based cache key.

    Args:
        proxies (ProxyPoolType): Single proxy configuration or ordered proxy pool.

    Returns:
        _ProxyConfigCacheKey: Tagged key preserving pool order and duplicate weights.
    """
    if isinstance(proxies, tuple):
        # tuple 顺序与重复项会影响随机权重，必须完整进入 key；mapping/url 标签则避免结构相似的不同输入形态发生碰撞
        return ("pool", tuple(_candidate_cache_key(item) for item in proxies))
    return ("single", _candidate_cache_key(proxies))


def _candidate_from_cache_key(key: _ProxyCandidateCacheKey) -> ProxyCandidateType:
    """Rebuild an isolated proxy candidate from its immutable cache key.

    Args:
        key (_ProxyCandidateCacheKey): Tagged candidate cache key.

    Returns:
        ProxyCandidateType: Reconstructed proxy URL or mapping.
    """
    kind, value = key
    if kind == "url":
        return cast(str, value)
    return dict(cast(tuple[tuple[str, str], ...], value))


@lru_cache(maxsize=PREPARED_PROXY_CACHE_SIZE)
def _prepare_proxy_pool_cached(key: _ProxyConfigCacheKey) -> PreparedProxyPool:
    """Build or reuse one immutable prepared proxy pool.

    Args:
        key (_ProxyConfigCacheKey): Content-based proxy configuration key.

    Returns:
        PreparedProxyPool: Cached immutable pool shared across equivalent configurations.
    """
    kind, value = key
    if kind == "pool":
        candidate_keys = cast(tuple[_ProxyCandidateCacheKey, ...], value)
        proxies: ProxyPoolType = tuple(
            _candidate_from_cache_key(candidate_key)
            for candidate_key in candidate_keys
        )
    else:
        proxies = _candidate_from_cache_key(cast(_ProxyCandidateCacheKey, value))
    return PreparedProxyPool(proxies)


def prepare_proxy_pool(proxies: ProxyPoolType) -> PreparedProxyPool:
    """Return a bounded-LRU prepared snapshot for a proxy configuration.

    Args:
        proxies (ProxyPoolType): Single proxy configuration or ordered proxy pool.

    Returns:
        PreparedProxyPool: Reusable normalized pool without request-local state.
    """
    # 每次 request override 只计算轻量内容 key；相同配置复用 normalize 与 route cache，修改后的 dict 因 key 改变而不会命中陈旧配置
    return _prepare_proxy_pool_cached(_proxy_config_cache_key(proxies))


def resolve_outcome_proxy(
    selection: ProxySelection,
    url: str,
    *,
    trust_env: bool = False,
    base_url: str | None = None,
) -> ProxySelection:
    """Resolve the proxy identity used by a final response or failed redirect.

    Args:
        selection (ProxySelection): Effective mapping and identity selected for the initial request URL.
        url (str): Final prepared request URL associated with the response or exception.
        trust_env (bool): Whether niquests may add an environment proxy while rebuilding a redirected request.
        base_url (str | None): Optional session base URL for a relative request URL.

    Returns:
        ProxySelection: Isolated mapping with identity resolved for the supplied URL.
    """
    request_url = merge_base_url(base_url, url) or url
    effective_proxies = selection.proxies
    resolution = resolve_proxy(request_url, effective_proxies)

    if resolution.key is None and trust_env:
        # niquests 在 redirect rebuild 时只为当前 scheme 补充缺失的 environment proxy；仅在现有 mapping 无法解析代理时执行同样操作，避免普通响应重复读取环境
        environment_proxies = get_environ_proxies(
            request_url,
            no_proxy=effective_proxies.get("no_proxy"),
        )
        scheme = parse_scheme(request_url)
        environment_proxy = environment_proxies.get(
            scheme,
            environment_proxies.get("all"),
        )
        if environment_proxy:
            effective_proxies = effective_proxies.copy()
            effective_proxies.setdefault(scheme, environment_proxy)
            resolution = resolve_proxy(request_url, effective_proxies)

    # redirect route 的 request mapping 与可选环境代理均已处理完毕；剩余的 unresolved 状态就是最终直连，而不是未知代理
    resolution = _finalize_proxy_resolution(resolution)

    return ProxySelection(
        # Pydantic 会复制 dict 字段；直接传入可避免在 redirect outcome 热路径做两次浅拷贝
        proxies=effective_proxies,
        key=resolution.key,
        log=resolution.log,
    )


# * =================================================


class ProxySelector:
    """Select prepared proxies while honoring cooldown and per-request rotation."""

    def __init__(
        self,
        *,
        candidates: tuple[_ProxyCandidate, ...],
        is_pool: bool,
        tracker: ProxyCooldownTracker,
    ):
        """Initialize request-local selection state over prepared candidates.

        Args:
            candidates (tuple[_ProxyCandidate, ...]): Prepared candidates resolved for the request route.
            is_pool (bool): Whether the original input was a tuple proxy pool.
            tracker (ProxyCooldownTracker): Shared health tracker used to skip cooling proxies.
        """
        self._candidates = candidates
        self._is_pool = is_pool
        self._tracker = tracker
        # 用候选在 tuple 中的索引记录已尝试项，而不是用 proxy key：重复配置可能被用户有意放入池中作为权重
        self._used_candidate_indexes: set[int] = set()

    async def select(self) -> ProxySelection:
        """Return the next available proxy, waiting when every choice is cooling.

        Returns:
            ProxySelection: Available proxy selected for the next outer request attempt.
        """
        if not self._is_pool:
            # 单配置总是包含一个 candidate；即使 mapping 为空也会解析成明确的直连选择
            return await self._select_single(self._candidates[0].selection)

        if not self._candidates:
            # 空 tuple 表示没有配置可轮换代理，因此按直连处理，并避免把空池传给 random.choice 触发 IndexError
            return ProxySelection(
                proxies={},
                key=DIRECT_PROXY_KEY,
                log=DIRECT_PROXY_KEY,
            )

        if not self._tracker.has_active_cooldowns:
            # 未启用 cooldown 或当前没有活跃窗口时无需扫描整个代理池；成功请求只做一次 O(1) random.choice，外层 retry 才按已用索引查找下一个候选
            return self._select_without_cooldown()

        while True:
            trackable_keys = (
                cast(str, candidate.selection.key)
                for candidate in self._candidates
                if candidate.selection.key not in (None, DIRECT_PROXY_KEY)
            )
            remaining_by_key = self._tracker.remaining_many(trackable_keys)
            available_candidates: list[_ProxyCandidate] = []
            # DEBUG 关闭时不创建冷却候选临时列表；warning 等待只需要最短剩余时间，正常 INFO/WARNING 热路径不会为逐代理 skip 日志付出额外分配
            cooling_candidates: list[tuple[ProxySelection, float]] | None = (
                [] if logger.isEnabledFor(logging.DEBUG) else None
            )

            for candidate in self._candidates:
                selection = candidate.selection
                remaining = remaining_by_key.get(selection.key or "", 0.0)
                if remaining <= 0:
                    available_candidates.append(candidate)
                    continue

                if cooling_candidates is not None:
                    cooling_candidates.append((selection, remaining))

            if cooling_candidates:
                # 先完成整池扫描再打印 skip，确保每条日志中的 available 都是同一时刻的最终池余量，而不是扫描到当前位置的中间计数
                availability_log = self._format_availability(
                    len(available_candidates),
                    len(self._candidates),
                )
                for selection, remaining in cooling_candidates:
                    logger.debug(
                        f"proxy.skip proxy={selection.log} reason=cooldown "
                        f"remaining={format_elapsed(remaining)} {availability_log}"
                    )

            if available_candidates:
                # 外层 retry 优先使用本次 request 尚未尝试过的代理；只有当前可用候选全部尝试过后才开启下一轮，cooldown 中候选不会被误记为已尝试
                unused_candidates = [
                    candidate
                    for candidate in available_candidates
                    if candidate.index not in self._used_candidate_indexes
                ]
                if not unused_candidates:
                    self._used_candidate_indexes.clear()
                    unused_candidates = available_candidates

                selected = random.choice(unused_candidates)
                self._used_candidate_indexes.add(selected.index)
                return self._copy_selection(selected.selection)

            wait_seconds = min(remaining_by_key.values(), default=0.0)
            availability_log = self._format_availability(0, len(self._candidates))
            logger.warning(
                "All proxies are cooling down; waiting "
                f"{format_elapsed(wait_seconds)} before retrying proxy selection "
                f"({availability_log})"
            )
            # 所有候选均在 cooldown 时等待最早恢复项；该等待发生在 HTTP 请求前，因此不消耗 tenacity attempt 或 HTTP timeout
            await asyncio.sleep(wait_seconds)

    def _select_without_cooldown(self) -> ProxySelection:
        """Select an untried candidate without building full temporary lists.

        Returns:
            ProxySelection: Uniformly selected candidate from the unused subset.
        """
        if len(self._used_candidate_indexes) == len(self._candidates):
            self._used_candidate_indexes.clear()

        if not self._used_candidate_indexes:
            selected = random.choice(self._candidates)
        else:
            # nth-unused 选择保持与 random.choice(unused_candidates) 相同的均匀分布，但只在发生 outer retry 时扫描，不让成功请求承担 O(pool_size) 成本
            unused_count = len(self._candidates) - len(self._used_candidate_indexes)
            unused_offset = random.choice(range(unused_count))
            selected = self._candidates[0]
            for candidate in self._candidates:
                if candidate.index in self._used_candidate_indexes:
                    continue
                if unused_offset == 0:
                    selected = candidate
                    break
                unused_offset -= 1

        self._used_candidate_indexes.add(selected.index)
        return self._copy_selection(selected.selection)

    async def _select_single(self, selection: ProxySelection) -> ProxySelection:
        """Wait for and return the only configured proxy selection.

        Args:
            selection (ProxySelection): Prepared single proxy selection.

        Returns:
            ProxySelection: Single proxy after any active cooldown has expired.
        """
        if not self._tracker.enabled or selection.key in (None, DIRECT_PROXY_KEY):
            return self._copy_selection(selection)

        while True:
            remaining = self._tracker.remaining(cast(str, selection.key))
            if remaining <= 0:
                break
            logger.warning(
                f"Proxy {selection.log} is cooling down; waiting "
                f"{format_elapsed(remaining)} before retrying proxy selection "
                f"({self._format_availability(0, 1)})"
            )
            # 单代理没有替代候选，只能等待 cooldown 到期；若在此直接失败，调用方会在已配置代理仍可恢复的情况下提前收到异常
            await asyncio.sleep(remaining)

        return self._copy_selection(selection)

    def record_outcome(
        self,
        selection: ProxySelection,
        *,
        failed: bool,
    ) -> None:
        """Record one selected proxy outcome and log a new cooldown window.

        Args:
            selection (ProxySelection): Proxy metadata associated with the completed attempt.
            failed (bool): Whether the attempt counts as a proxy-health failure.

        Returns:
            None: The shared tracker and logger are updated in place.
        """
        # selection.key 保留凭据用于区分代理身份，selection.log 只用于日志；任何日志调用都不能使用未脱敏 key
        cooled_down = self._tracker.record(selection.key, failed=failed)
        if cooled_down:
            # cooldown 由同一 proxy 跨多次请求累计触发，因此不关联单个 method/URL；余量只描述当前 selector 的候选池，避免混入 tracker 中其他配置的历史代理
            logger.warning(
                f"proxy.cooldown proxy={selection.log} "
                f"failures={self._tracker.threshold} "
                f"cooldown={format_elapsed(self._tracker.cooldown_seconds)} "
                f"{self.availability_log()}"
            )

    def availability(self) -> tuple[int, int]:
        """Return selectable and total candidate-slot counts for this request.

        Returns:
            tuple[int, int]: Available candidate slots followed by total candidate slots; duplicate proxies remain separate weighting slots.
        """
        total = len(self._candidates)
        if not self._tracker.enabled:
            return total, total

        # remaining_many 对重复 identity 只查询一次时钟与 tracker 状态，随后仍按候选槽位计数，保留 tuple 中重复项表达的随机权重
        trackable_keys = (
            cast(str, candidate.selection.key)
            for candidate in self._candidates
            if candidate.selection.key not in (None, DIRECT_PROXY_KEY)
        )
        remaining_by_key = self._tracker.remaining_many(trackable_keys)
        available = sum(
            remaining_by_key.get(candidate.selection.key or "", 0.0) <= 0
            for candidate in self._candidates
        )
        return available, total

    def availability_log(self) -> str:
        """Format current candidate availability for lifecycle logs.

        Returns:
            str: Compact ``available=n/total`` snapshot for this request's proxy route.
        """
        return self._format_availability(*self.availability())

    @staticmethod
    def _format_availability(available: int, total: int) -> str:
        """Format one proxy candidate availability snapshot.

        Args:
            available (int): Candidate slots not currently cooling down.
            total (int): Total candidate slots in the current request route.

        Returns:
            str: Compact availability field suitable for logs.
        """
        return f"available={available}/{total}"

    @staticmethod
    def _copy_selection(selection: ProxySelection) -> ProxySelection:
        """Detach one returned selection from globally cached proxy metadata.

        Args:
            selection (ProxySelection): Cached immutable-by-convention candidate metadata.

        Returns:
            ProxySelection: Request-local selection whose mapping may be safely consumed or mutated downstream.
        """
        # frozen Pydantic model 只阻止字段重新赋值，dict 内容仍可变；request-local model copy 可防止 hook、niquests 未来版本或外部 helper 调用方污染全局 prepared cache
        return ProxySelection(
            # Pydantic 会为 dict 字段创建独立容器；无需先手动 copy 再让模型重复复制
            proxies=selection.proxies,
            key=selection.key,
            log=selection.log,
        )


def format_proxy_log(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the redacted proxy value displayed in request logs.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        str | None: Redacted selected proxy, ``direct``, or None when unresolved.
    """
    return resolve_proxy(url, proxies, base_url).log


def format_proxy_key(
    url: str,
    proxies: dict[str, str],
    base_url: str | None = None,
) -> str | None:
    """Return the raw proxy value used for internal cooldown tracking.

    Args:
        url (str): Absolute or relative request URL used for proxy selection.
        proxies (dict[str, str]): Normalized niquests proxy mapping.
        base_url (str | None): Session base URL used to resolve relative request URLs.

    Returns:
        str | None: Raw selected proxy, ``direct``, or None when unresolved.
    """
    return resolve_proxy(url, proxies, base_url).key
