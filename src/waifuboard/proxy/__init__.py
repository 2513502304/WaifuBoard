"""Proxy configuration, selection, and cooldown helpers."""

from .cooldown import (
    DIRECT_PROXY_KEY,
    ProxyCooldownTracker,
    is_proxy_transport_exception,
)
from .pool import (
    PREPARED_PROXY_CACHE_SIZE,
    PreparedProxyPool,
    ProxyResolution,
    ProxySelection,
    ProxySelector,
    format_proxy_key,
    format_proxy_log,
    is_immutable_proxy_pool,
    normalize_proxy,
    prepare_proxy_pool,
    redact_proxy_url,
    resolve_outcome_proxy,
    resolve_proxy,
)

__all__ = [
    "DIRECT_PROXY_KEY",
    "PREPARED_PROXY_CACHE_SIZE",
    "PreparedProxyPool",
    "ProxyCooldownTracker",
    "ProxyResolution",
    "ProxySelection",
    "ProxySelector",
    "format_proxy_key",
    "format_proxy_log",
    "is_immutable_proxy_pool",
    "is_proxy_transport_exception",
    "normalize_proxy",
    "prepare_proxy_pool",
    "redact_proxy_url",
    "resolve_outcome_proxy",
    "resolve_proxy",
]
