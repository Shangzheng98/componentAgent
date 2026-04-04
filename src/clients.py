"""API 客户端 — Bing Search / Mouser / DigiKey。"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from .models import ComponentResult, DataSource

# 每次导入时从项目根目录加载 .env（不覆盖已有环境变量）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class BaseClient(ABC):
    """所有数据源客户端的基类。"""

    source: str = ""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def close(self) -> None:
        await self._http.aclose()

    @abstractmethod
    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        ...

    async def get_detail(self, part_number: str) -> Optional[ComponentResult]:
        return None

    async def find_alternatives(self, part_number: str) -> list[ComponentResult]:
        return []


# ---------------------------------------------------------------------------
# Bing Search 客户端（元器件垂直搜索）
# ---------------------------------------------------------------------------

BING_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/search"

# 元器件分销商站点，用于限定搜索范围提高精准度
_COMPONENT_SITES = [
    "mouser.com",
    "digikey.com",
    "lcsc.com",
    "arrow.com",
    "element14.com",
    "ti.com",
    "st.com",
    "nxp.com",
    "microchip.com",
    "onsemi.com",
]


class BingSearchClient(BaseClient):
    """通过 Bing Web Search API 搜索元器件信息。"""

    source = "bing"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = os.environ.get("BING_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        if not self.available:
            return []

        # 构建垂直搜索 query：加上元器件相关站点限定
        site_query = " OR ".join(f"site:{s}" for s in _COMPONENT_SITES[:5])
        query = f"{keyword} electronic component ({site_query})"

        try:
            resp = await self._http.get(
                BING_SEARCH_URL,
                params={"q": query, "count": min(max_results * 2, 30), "mkt": "en-US"},
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[ComponentResult] = []
        web_pages = (data.get("webPages") or {}).get("value") or []

        for item in web_pages:
            if len(results) >= max_results:
                break

            name = item.get("name", "")
            snippet = item.get("snippet", "")
            url = item.get("url", "")

            # 尝试从标题/snippet 中提取元器件信息
            part_number = _extract_part_number(name) or _extract_part_number(snippet) or name
            manufacturer = _extract_manufacturer(name, snippet)

            results.append(
                ComponentResult(
                    part_number=part_number,
                    manufacturer=manufacturer,
                    description=snippet[:200] if snippet else name,
                    product_url=url,
                    source=self.source,
                )
            )

        return results

    async def get_detail(self, part_number: str) -> Optional[ComponentResult]:
        """先用 Bing 搜索产品页面，再抓取页面解析详细参数。"""
        if not self.available:
            return None

        from .parsers import parse_product_page

        # 搜索精确型号，找到产品页面
        site_query = " OR ".join(f"site:{s}" for s in _COMPONENT_SITES[:5])
        query = f'"{part_number}" ({site_query})'

        try:
            resp = await self._http.get(
                BING_SEARCH_URL,
                params={"q": query, "count": 5, "mkt": "en-US"},
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        web_pages = (data.get("webPages") or {}).get("value") or []
        if not web_pages:
            return None

        # 取第一个结果的 URL 去抓取详情
        product_url = web_pages[0].get("url", "")
        if not product_url:
            return None

        parsed = await parse_product_page(product_url, part_number)

        return ComponentResult(
            part_number=parsed.part_number or part_number,
            manufacturer=parsed.manufacturer,
            description=parsed.description,
            package=parsed.package,
            unit_price=parsed.unit_price,
            stock=parsed.stock,
            datasheet_url=parsed.datasheet_url,
            product_url=product_url,
            source=self.source,
            parameters=parsed.parameters,
        )


def _extract_part_number(text: str) -> str:
    """尝试从文本中提取元器件型号。"""
    # 常见型号模式：字母+数字组合，如 STM32F103C8T6, LM7805, TPS54331
    match = re.search(r'\b([A-Z]{1,5}\d{2,}[A-Z0-9\-]*)\b', text, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_manufacturer(name: str, snippet: str) -> str:
    """尝试从标题和摘要中提取制造商。"""
    known_manufacturers = [
        "Texas Instruments", "TI", "STMicroelectronics", "ST", "NXP",
        "Microchip", "Analog Devices", "ON Semiconductor", "onsemi",
        "Infineon", "Renesas", "ROHM", "Vishay", "Murata", "TDK",
        "Samsung", "Nexperia", "Maxim", "Diodes Inc",
    ]
    combined = f"{name} {snippet}"
    for mfr in known_manufacturers:
        if mfr.lower() in combined.lower():
            return mfr
    return ""


# ---------------------------------------------------------------------------
# Mouser 客户端
# ---------------------------------------------------------------------------

MOUSER_SEARCH_URL = "https://api.mouser.com/api/v1/search/keyword"
MOUSER_PARTNUMBER_URL = "https://api.mouser.com/api/v1/search/partnumber"


class MouserClient(BaseClient):
    """Mouser 官方 Search API 客户端。"""

    source = "mouser"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = os.environ.get("MOUSER_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _parse_product(item: dict) -> ComponentResult:
        """解析单个 MouserPart 对象。"""
        # 解析 ProductAttributes → parameters + package
        params: dict[str, str] = {}
        package = ""
        for attr in item.get("ProductAttributes") or []:
            name = attr.get("AttributeName", "")
            value = attr.get("AttributeValue", "")
            if name and value:
                params[name] = value
                if "package" in name.lower() or "case" in name.lower():
                    package = value

        # 补充生命周期、交期等信息到 parameters
        for key in ("LifecycleStatus", "LeadTime", "ROHSStatus"):
            val = item.get(key)
            if val:
                params[key] = str(val)
        if item.get("Min"):
            params["MinOrderQty"] = str(item["Min"])
        if item.get("Mult"):
            params["MultOrderQty"] = str(item["Mult"])

        # 库存优先用 AvailabilityInStock（纯数字），fallback 到 Availability 字符串
        stock_val = item.get("AvailabilityInStock")
        if not stock_val:
            stock_val = (item.get("Availability") or "").replace(" In Stock", "").replace(",", "")

        return ComponentResult(
            part_number=item.get("ManufacturerPartNumber") or item.get("MouserPartNumber") or "",
            manufacturer=item.get("Manufacturer") or "",
            description=item.get("Description") or "",
            package=package,
            unit_price=_parse_mouser_price(item.get("PriceBreaks") or []),
            stock=_safe_int(stock_val),
            datasheet_url=item.get("DataSheetUrl") or "",
            product_url=item.get("ProductDetailUrl") or "",
            source="mouser",
            parameters=params,
        )

    async def _search_keyword(self, keyword: str, max_results: int) -> list[dict]:
        """调用 keyword 搜索，返回原始 Parts 列表。"""
        payload = {
            "SearchByKeywordRequest": {
                "keyword": keyword,
                "records": min(max_results, 50),
                "startingRecord": 0,
                "searchOptions": "",
                "searchWithYourSignUpLanguage": "",
            }
        }
        try:
            resp = await self._http.post(
                f"{MOUSER_SEARCH_URL}?apiKey={self._api_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        return (data.get("SearchResults") or {}).get("Parts") or []

    async def _search_partnumber(self, part_number: str) -> list[dict]:
        """调用 partnumber 精确搜索，返回原始 Parts 列表。"""
        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": part_number,
                "partSearchOptions": "Exact",
            }
        }
        try:
            resp = await self._http.post(
                f"{MOUSER_PARTNUMBER_URL}?apiKey={self._api_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        return (data.get("SearchResults") or {}).get("Parts") or []

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        if not self.available:
            return []
        parts = await self._search_keyword(keyword, max_results)
        return [self._parse_product(item) for item in parts[:max_results]]

    async def get_detail(self, part_number: str) -> Optional[ComponentResult]:
        """按料号精确搜索获取完整产品详情，并抓取产品页面补充参数。"""
        if not self.available:
            return None
        parts = await self._search_partnumber(part_number)
        if not parts:
            return None
        result = self._parse_product(parts[0])

        # 如果 API 返回的参数较少，抓取产品页面补充
        if result.product_url and len(result.parameters) < 5:
            from .parsers import parse_product_page
            parsed = await parse_product_page(result.product_url, part_number)
            # 页面解析的参数补充到 API 结果中（API 优先，不覆盖）
            for k, v in parsed.parameters.items():
                if k not in result.parameters:
                    result.parameters[k] = v
            if not result.package and parsed.package:
                result.package = parsed.package
            if not result.datasheet_url and parsed.datasheet_url:
                result.datasheet_url = parsed.datasheet_url

        return result

    async def find_alternatives(self, part_number: str) -> list[ComponentResult]:
        """通过 SuggestedReplacement 字段查找替代料。"""
        if not self.available:
            return []
        parts = await self._search_partnumber(part_number)
        if not parts:
            return []

        results: list[ComponentResult] = []
        for item in parts:
            suggested = item.get("SuggestedReplacement") or ""
            if suggested and suggested != part_number:
                # 搜索推荐的替代料号获取完整信息
                alt_parts = await self._search_partnumber(suggested)
                if alt_parts:
                    results.append(self._parse_product(alt_parts[0]))

            # AlternatePackagings 也是替代选项
            for alt in item.get("AlternatePackagings") or []:
                alt_pn = alt.get("ManufacturerPartNumber") or alt.get("MouserPartNumber") or ""
                if alt_pn and alt_pn != part_number:
                    alt_parts = await self._search_partnumber(alt_pn)
                    if alt_parts:
                        results.append(self._parse_product(alt_parts[0]))

        return results


# ---------------------------------------------------------------------------
# DigiKey 客户端
# ---------------------------------------------------------------------------

DIGIKEY_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
DIGIKEY_SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"
DIGIKEY_DETAIL_URL = "https://api.digikey.com/products/v4/search/{pn}/productdetails"
DIGIKEY_SUBSTITUTIONS_URL = "https://api.digikey.com/products/v4/search/{pn}/substitutions"


class DigiKeyClient(BaseClient):
    """DigiKey 官方 API 客户端 (Client Credentials / 2-legged OAuth)。"""

    source = "digikey"

    def __init__(self) -> None:
        super().__init__()
        self._client_id = os.environ.get("DIGIKEY_CLIENT_ID", "")
        self._client_secret = os.environ.get("DIGIKEY_CLIENT_SECRET", "")
        self._token: str = ""

    @property
    def available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _ensure_token(self) -> None:
        if self._token or not self.available:
            return
        try:
            resp = await self._http.post(
                DIGIKEY_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            self._token = resp.json().get("access_token", "")
        except Exception:
            pass

    @staticmethod
    def _parse_product(item: dict) -> ComponentResult:
        """Parse a single product dict from DigiKey Swagger v4 response."""
        # ManufacturerProductNumber (v4) with fallback to old field name
        part_number = item.get("ManufacturerProductNumber") or item.get("ManufacturerPartNumber") or item.get("DigiKeyPartNumber") or ""

        # Manufacturer is {Id, Name}
        mfr = item.get("Manufacturer") or {}
        manufacturer = mfr.get("Name", "") if isinstance(mfr, dict) else str(mfr)

        # Description is a nested object {ProductDescription, DetailedDescription}
        desc_obj = item.get("Description") or {}
        if isinstance(desc_obj, dict):
            description = desc_obj.get("ProductDescription") or desc_obj.get("DetailedDescription") or ""
        else:
            description = str(desc_obj)

        # UnitPrice is a top-level double; StandardPricing lives inside ProductVariations[]
        unit_price: Optional[float] = None
        raw_unit_price = item.get("UnitPrice")
        if raw_unit_price is not None:
            unit_price = _parse_digikey_price(raw_unit_price)
        if unit_price is None:
            variations = item.get("ProductVariations") or []
            if variations and isinstance(variations[0], dict):
                unit_price = _parse_digikey_price(variations[0].get("StandardPricing"))

        # PackageType is inside ProductVariations[0].PackageType.Name
        package = ""
        variations = item.get("ProductVariations") or []
        if variations and isinstance(variations[0], dict):
            pkg = variations[0].get("PackageType") or {}
            package = pkg.get("Name", "") if isinstance(pkg, dict) else str(pkg)

        # Parameters: [{ParameterText, ValueText}, ...]
        params_list = item.get("Parameters") or []
        parameters: dict[str, str] = {
            p["ParameterText"]: p["ValueText"]
            for p in params_list
            if isinstance(p, dict) and "ParameterText" in p and "ValueText" in p
        }

        return ComponentResult(
            part_number=part_number,
            manufacturer=manufacturer,
            description=description,
            package=package,
            unit_price=unit_price,
            stock=_safe_int(item.get("QuantityAvailable")),
            datasheet_url=item.get("DatasheetUrl") or item.get("PrimaryDatasheet") or "",
            product_url=item.get("ProductUrl") or "",
            source="digikey",
            parameters=parameters,
        )

    async def get_detail(self, part_number: str) -> Optional[ComponentResult]:
        if not self.available:
            return None
        await self._ensure_token()
        if not self._token:
            return None

        url = DIGIKEY_DETAIL_URL.format(pn=part_number)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "X-DIGIKEY-Locale-Currency": "USD",
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
        }
        try:
            resp = await self._http.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        product = data.get("Product")
        if not product:
            return None
        return self._parse_product(product)

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        if not self.available:
            return []
        await self._ensure_token()
        if not self._token:
            return []

        payload = {
            "Keywords": keyword,
            "Limit": min(max_results, 50),
            "Offset": 0,
            "FilterOptionsRequest": {
                "MarketPlaceFilter": "ExcludeMarketPlace",
            },
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "Content-Type": "application/json",
            "X-DIGIKEY-Locale-Currency": "USD",
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
        }
        try:
            resp = await self._http.post(DIGIKEY_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        products = data.get("Products") or data.get("ExactMatches") or []
        return [self._parse_product(item) for item in products[:max_results]]

    async def find_alternatives(self, part_number: str) -> list[ComponentResult]:
        if not self.available:
            return []
        await self._ensure_token()
        if not self._token:
            return []
        url = DIGIKEY_SUBSTITUTIONS_URL.format(pn=part_number)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "X-DIGIKEY-Locale-Currency": "USD",
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
        }
        try:
            resp = await self._http.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        results: list[ComponentResult] = []
        for item in data.get("ProductSubstitutes") or []:
            mfr = item.get("Manufacturer") or {}
            manufacturer = mfr.get("Name", "") if isinstance(mfr, dict) else str(mfr)
            results.append(
                ComponentResult(
                    part_number=item.get("ManufacturerProductNumber") or "",
                    manufacturer=manufacturer,
                    description=item.get("Description") or "",
                    unit_price=_safe_float(item.get("UnitPrice")),
                    stock=_safe_int(item.get("QuantityAvailable")),
                    product_url=item.get("ProductUrl") or "",
                    source="digikey",
                )
            )
        return results


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_clients(source: DataSource = DataSource.ALL) -> list[BaseClient]:
    """根据数据源返回对应客户端列表。"""
    if source == DataSource.BING:
        return [BingSearchClient()]
    if source == DataSource.MOUSER:
        return [MouserClient()]
    if source == DataSource.DIGIKEY:
        return [DigiKeyClient()]

    # ALL: Bing 作为基础数据源，Mouser/DigiKey 有 key 时加入
    clients: list[BaseClient] = []
    bing = BingSearchClient()
    if bing.available:
        clients.append(bing)
    mouser = MouserClient()
    if mouser.available:
        clients.append(mouser)
    digikey = DigiKeyClient()
    if digikey.available:
        clients.append(digikey)
    return clients


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: object) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _parse_mouser_price(price_breaks: list[dict]) -> Optional[float]:
    """从 Mouser PriceBreaks 取最小批量单价。"""
    if not price_breaks:
        return None
    try:
        first = price_breaks[0]
        raw = first.get("Price", "").replace("$", "").replace(",", "").strip()
        return float(raw) if raw else None
    except (ValueError, IndexError):
        return None


def _parse_digikey_price(pricing: object) -> Optional[float]:
    """从 DigiKey 价格信息提取单价。"""
    if pricing is None:
        return None
    if isinstance(pricing, (int, float)):
        return float(pricing)
    if isinstance(pricing, list) and pricing:
        try:
            return float(pricing[0].get("UnitPrice", 0))
        except (ValueError, KeyError, IndexError):
            return None
    return None
