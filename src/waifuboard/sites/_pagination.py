"""Shared HTML pagination parsing for site-specific clients."""

from urllib.parse import parse_qs, urlparse

from parsel import Selector


def max_numeric_link_text(
    html: str | None,
    links_xpath: str,
    *,
    default: int = 1,
) -> int:
    """Return the largest numeric label among selected pagination links.

    Args:
        html (str | None): HTML document containing a pagination control, or None for an empty response body.
        links_xpath (str): XPath selecting pagination anchor elements.
        default (int): Site-specific first page returned when no numeric link exists.

    Returns:
        int: Largest numeric page label, or ``default`` for a single-page result.
    """
    # Response.text 的底层类型允许 None；空 body 与没有分页器具有相同语义，应回落到站点默认页而不是让 Parsel 构造失败
    selector = Selector(text=html or "", type="html")
    page_numbers: list[int] = []

    # 只接受完全由十进制数字组成的标签，避免依赖“倒数第二项”这类容易被 Next、Previous 或站点新增控件破坏的位置假设
    for link in selector.xpath(links_xpath):
        label = link.xpath("normalize-space(.)").get(default="")
        if label.isdecimal():
            page_numbers.append(int(label))

    return max(page_numbers, default=default)


def max_query_parameter(
    html: str | None,
    hrefs_xpath: str,
    *,
    parameter: str,
    default: int,
) -> int:
    """Return the largest integer query parameter among selected links.

    Args:
        html (str | None): HTML document containing pagination links, or None for an empty response body.
        hrefs_xpath (str): XPath selecting link ``href`` attribute values.
        parameter (str): Query-string parameter carrying the site page offset.
        default (int): Site-specific first offset returned when no value exists.

    Returns:
        int: Largest valid parameter value, or ``default`` when pagination is absent.
    """
    # 与数字页码 parser 保持相同空 body 语义，确保探测函数始终返回调用方指定的站点起始值
    selector = Selector(text=html or "", type="html")
    values: list[int] = []

    # 使用 URL 查询参数解析器而不是正则截取，确保参数顺序、HTML 实体解码和同名参数都不会改变结果
    for href in selector.xpath(hrefs_xpath).getall():
        for value in parse_qs(urlparse(href).query).get(parameter, ()):
            if value.isdecimal():
                values.append(int(value))

    return max(values, default=default)
