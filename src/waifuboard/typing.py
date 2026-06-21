"""
Shared public data models used by WaifuBoard helper APIs.
"""

from typing import Any

import pandas as pd
from niquests.typing import HeadersType
from pydantic import BaseModel, ConfigDict


class DownloadItem(BaseModel):
    """
    A single file download request with its related sidecar metadata.

    The model keeps URL, filepath, request metadata, and optional post metadata
    together so concurrent download completion order cannot break their
    relationship.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    filepath: str
    headers: HeadersType | None = None
    referer: str | None = None
    raw: pd.DataFrame | None = None
    tags: str | None = None


class DownloadResult(BaseModel):
    """
    Result of a completed file download.

    The original DownloadItem is returned with the filepath to preserve the
    caller's business context even when downloads finish out of order.
    """

    item: DownloadItem
    filepath: str


class PageResult(BaseModel):
    """
    Result of a completed page fetch.

    The page number is carried explicitly because concurrent page requests are
    yielded in completion order rather than input order.
    """

    page: int
    content: list[dict[str, Any]]
