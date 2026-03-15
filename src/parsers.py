"""产品页面解析器 — HTML 规则解析 + LLM fallback 提取元器件参数。"""

from __future__ import annotations

import json
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# 页面抓取
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_page(url: str, timeout: float = 15) -> Optional[str]:
    """抓取产品页面 HTML。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 解析结果数据结构
# ---------------------------------------------------------------------------


class ParsedComponent:
    """从页面提取的元器件参数。"""

    def __init__(
        self,
        part_number: str = "",
        manufacturer: str = "",
        description: str = "",
        package: str = "",
        unit_price: Optional[float] = None,
        stock: Optional[int] = None,
        datasheet_url: str = "",
        parameters: Optional[dict[str, str]] = None,
    ) -> None:
        self.part_number = part_number
        self.manufacturer = manufacturer
        self.description = description
        self.package = package
        self.unit_price = unit_price
        self.stock = stock
        self.datasheet_url = datasheet_url
        self.parameters = parameters or {}


# ---------------------------------------------------------------------------
# 已知站点 HTML 解析器
# ---------------------------------------------------------------------------


def _match_domain(url: str, domain: str) -> bool:
    """检查 URL 是否属于指定域名。"""
    host = urlparse(url).netloc.lower()
    return host == domain or host.endswith(f".{domain}")


def parse_mouser_page(html: str) -> ParsedComponent:
    """解析 Mouser 产品页面。"""
    soup = BeautifulSoup(html, "html.parser")
    result = ParsedComponent()

    # 型号
    el = soup.select_one("#spnManufacturerPartNumber, [id*='ManufacturerPartNumber']")
    if el:
        result.part_number = el.get_text(strip=True)

    # 制造商
    el = soup.select_one("#lnkManufacturerName, [id*='ManufacturerName']")
    if el:
        result.manufacturer = el.get_text(strip=True)

    # 描述
    el = soup.select_one("#spnDescription, [id*='Description']")
    if el:
        result.description = el.get_text(strip=True)

    # Datasheet
    el = soup.select_one("a[href*='datasheet'], a.pdp-datasheet-link")
    if el and isinstance(el, Tag):
        result.datasheet_url = el.get("href", "") or ""

    # 参数表格
    result.parameters = _parse_spec_table(
        soup, "div.specs-table table, table#product-details, table.specs-table"
    )

    # 价格
    el = soup.select_one("span.price, span[id*='UnitPrice']")
    if el:
        result.unit_price = _parse_price_text(el.get_text(strip=True))

    # 库存
    el = soup.select_one("span[id*='Availability'], span.availibility-value")
    if el:
        result.stock = _parse_stock_text(el.get_text(strip=True))

    return result


def parse_digikey_page(html: str) -> ParsedComponent:
    """解析 DigiKey 产品页面。"""
    soup = BeautifulSoup(html, "html.parser")
    result = ParsedComponent()

    # 型号 — DigiKey 页面标题通常包含型号
    el = soup.select_one(
        "[data-testid='mfr-number'], "
        "td[data-field='manufacturer-part-number'], "
        "h1.product-details-mfr-part-number"
    )
    if el:
        result.part_number = el.get_text(strip=True)

    # 制造商
    el = soup.select_one(
        "[data-testid='manufacturer'], "
        "td[data-field='manufacturer'] a, "
        "span.product-details-manufacturer"
    )
    if el:
        result.manufacturer = el.get_text(strip=True)

    # 描述
    el = soup.select_one(
        "[data-testid='product-description'], "
        "td[data-field='description']"
    )
    if el:
        result.description = el.get_text(strip=True)

    # Datasheet
    el = soup.select_one("a[href*='datasheet'], a[data-testid='datasheet-link']")
    if el and isinstance(el, Tag):
        result.datasheet_url = el.get("href", "") or ""

    # 参数表 — DigiKey 用 table 或 div 列表
    result.parameters = _parse_spec_table(
        soup,
        "table.product-details-table, "
        "div.product-details-specs-container table, "
        "table[id*='ProductAttributes']"
    )

    # 价格
    el = soup.select_one(
        "[data-testid='pricing'] td:nth-child(2), "
        "span.price-value"
    )
    if el:
        result.unit_price = _parse_price_text(el.get_text(strip=True))

    # 库存
    el = soup.select_one(
        "[data-testid='stock-value'], "
        "span.product-details-stock-value"
    )
    if el:
        result.stock = _parse_stock_text(el.get_text(strip=True))

    return result


def parse_lcsc_page(html: str) -> ParsedComponent:
    """解析 LCSC 产品页面。"""
    soup = BeautifulSoup(html, "html.parser")
    result = ParsedComponent()

    # LCSC 页面参数通常在 JSON-LD 或 table 中
    # 尝试 JSON-LD
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Product":
                result.part_number = data.get("mpn", "") or data.get("sku", "")
                result.manufacturer = (data.get("brand") or {}).get("name", "") if isinstance(data.get("brand"), dict) else ""
                result.description = data.get("description", "")
                offers = data.get("offers")
                if isinstance(offers, dict):
                    result.unit_price = _parse_price_text(str(offers.get("price", "")))
                break
        except (json.JSONDecodeError, TypeError):
            continue

    # 参数表格
    params = _parse_spec_table(soup, "table.product-attrs, table.info-cont-table")
    if params:
        result.parameters = params

    return result


# ---------------------------------------------------------------------------
# 通用 HTML 解析辅助
# ---------------------------------------------------------------------------


def _parse_spec_table(soup: BeautifulSoup, selector: str) -> dict[str, str]:
    """从参数表格中提取 key-value 对。"""
    params: dict[str, str] = {}

    for table in soup.select(selector):
        rows = table.select("tr")
        for row in rows:
            cells = row.select("th, td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                if key and value and len(key) < 100 and len(value) < 500:
                    params[key] = value

    # 也尝试 dl/dt/dd 结构
    if not params:
        for dl in soup.select("dl"):
            dts = dl.select("dt")
            dds = dl.select("dd")
            for dt, dd in zip(dts, dds):
                key = dt.get_text(strip=True)
                value = dd.get_text(strip=True)
                if key and value:
                    params[key] = value

    return params


def _parse_price_text(text: str) -> Optional[float]:
    """从价格文本中提取数字。"""
    cleaned = re.sub(r"[^\d.]", "", text)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_stock_text(text: str) -> Optional[int]:
    """从库存文本中提取数字。"""
    cleaned = re.sub(r"[^\d]", "", text)
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 通用 HTML 提取（适用于未知站点，best-effort）
# ---------------------------------------------------------------------------


def parse_generic_page(html: str) -> ParsedComponent:
    """通用页面解析 — 尝试从任意产品页面提取信息。"""
    soup = BeautifulSoup(html, "html.parser")
    result = ParsedComponent()

    # 尝试 JSON-LD
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Product":
                result.part_number = data.get("mpn", "") or data.get("sku", "") or data.get("name", "")
                result.manufacturer = (data.get("brand") or {}).get("name", "") if isinstance(data.get("brand"), dict) else ""
                result.description = data.get("description", "")
                offers = data.get("offers")
                if isinstance(offers, dict):
                    result.unit_price = _parse_price_text(str(offers.get("price", "")))
                break
        except (json.JSONDecodeError, TypeError):
            continue

    # 尝试从所有 table 中提取参数
    result.parameters = _parse_spec_table(soup, "table")

    # 从 title 获取信息
    title = soup.title
    if title and not result.part_number:
        result.part_number = title.get_text(strip=True)[:100]

    # 从 meta description 获取描述
    meta_desc = soup.select_one("meta[name='description']")
    if meta_desc and isinstance(meta_desc, Tag) and not result.description:
        result.description = (meta_desc.get("content") or "")[:300]

    return result


# ---------------------------------------------------------------------------
# LLM Fallback 提取器
# ---------------------------------------------------------------------------


async def extract_with_llm(html: str, part_number: str = "") -> ParsedComponent:
    """使用 Claude API 从页面文本中提取结构化的元器件参数。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ParsedComponent()

    # 将 HTML 转为纯文本，减少 token 消耗
    soup = BeautifulSoup(html, "html.parser")
    # 移除无关标签
    for tag in soup.select("script, style, nav, footer, header, iframe, noscript"):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # 截断到合理长度
    text = text[:8000]

    prompt = f"""从以下电子元器件产品页面文本中提取结构化信息。

返回一个 JSON 对象，包含以下字段（没有的留空字符串或 null）：
- part_number: 元器件型号/物料编号
- manufacturer: 制造商
- description: 产品描述（一行）
- package: 封装形式
- unit_price: 单价（数字，单位 USD，null 如果没有）
- stock: 库存数量（整数，null 如果没有）
- datasheet_url: 数据手册链接
- parameters: 关键电气参数的 key-value 对象（如电压、电流、温度范围、电容值等）

{f"提示：元器件型号可能是 {part_number}" if part_number else ""}

页面文本：
{text}

只返回 JSON，不要其他内容。"""

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        # 提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return ParsedComponent()

        data = json.loads(json_match.group())
        return ParsedComponent(
            part_number=data.get("part_number", ""),
            manufacturer=data.get("manufacturer", ""),
            description=data.get("description", ""),
            package=data.get("package", ""),
            unit_price=data.get("unit_price"),
            stock=data.get("stock"),
            datasheet_url=data.get("datasheet_url", ""),
            parameters=data.get("parameters") or {},
        )
    except Exception:
        return ParsedComponent()


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

# 域名 → 解析器 映射
_SITE_PARSERS: dict[str, type] = {}  # 不用 class，用函数

_DOMAIN_PARSER_MAP = {
    "mouser.com": parse_mouser_page,
    "www.mouser.com": parse_mouser_page,
    "mouser.cn": parse_mouser_page,
    "digikey.com": parse_digikey_page,
    "www.digikey.com": parse_digikey_page,
    "digikey.cn": parse_digikey_page,
    "lcsc.com": parse_lcsc_page,
    "www.lcsc.com": parse_lcsc_page,
}


async def parse_product_page(url: str, part_number: str = "") -> ParsedComponent:
    """抓取并解析产品页面，自动选择最佳解析策略。

    1. 已知站点 → HTML 规则解析
    2. 未知站点 → 通用 HTML 解析
    3. 如果结果参数太少，且配置了 ANTHROPIC_API_KEY → LLM 提取
    """
    html = await fetch_page(url)
    if not html:
        return ParsedComponent()

    # 1. 匹配已知站点解析器
    host = urlparse(url).netloc.lower()
    parser_fn = None
    for domain, fn in _DOMAIN_PARSER_MAP.items():
        if host == domain or host.endswith(f".{domain}"):
            parser_fn = fn
            break

    if parser_fn:
        result = parser_fn(html)
    else:
        # 2. 通用解析
        result = parse_generic_page(html)

    # 3. 如果参数太少，尝试 LLM fallback
    has_enough = bool(result.part_number and len(result.parameters) >= 3)
    if not has_enough and os.environ.get("ANTHROPIC_API_KEY"):
        llm_result = await extract_with_llm(html, part_number)
        # 合并：LLM 结果补充 HTML 解析缺失的字段
        if not result.part_number:
            result.part_number = llm_result.part_number
        if not result.manufacturer:
            result.manufacturer = llm_result.manufacturer
        if not result.description:
            result.description = llm_result.description
        if not result.package:
            result.package = llm_result.package
        if result.unit_price is None:
            result.unit_price = llm_result.unit_price
        if result.stock is None:
            result.stock = llm_result.stock
        if not result.datasheet_url:
            result.datasheet_url = llm_result.datasheet_url
        # 参数合并（LLM 补充 HTML 未解析到的）
        for k, v in llm_result.parameters.items():
            if k not in result.parameters:
                result.parameters[k] = v

    return result
