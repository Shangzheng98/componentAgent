"""API 客户端 — Bing Search / Mouser / DigiKey。"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from .models import ComponentResult, DataSource

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


class MouserClient(BaseClient):
    """Mouser 官方 Search API 客户端。"""

    source = "mouser"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = os.environ.get("MOUSER_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        if not self.available:
            return []

        payload = {
            "SearchByKeywordRequest": {
                "keyword": keyword,
                "records": min(max_results, 20),
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

        results: list[ComponentResult] = []
        parts = (data.get("SearchResults") or {}).get("Parts") or []
        for item in parts[:max_results]:
            price = _parse_mouser_price(item.get("PriceBreaks") or [])
            results.append(
                ComponentResult(
                    part_number=item.get("ManufacturerPartNumber") or item.get("MouserPartNumber") or "",
                    manufacturer=item.get("Manufacturer") or "",
                    description=item.get("Description") or "",
                    package=item.get("Mfr") or "",
                    unit_price=price,
                    stock=_safe_int(item.get("Availability", "").replace(" In Stock", "").replace(",", "")),
                    datasheet_url=item.get("DataSheetUrl") or "",
                    product_url=item.get("ProductDetailUrl") or "",
                    source=self.source,
                )
            )
        return results


# ---------------------------------------------------------------------------
# DigiKey 客户端
# ---------------------------------------------------------------------------

DIGIKEY_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
DIGIKEY_SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"


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

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        if not self.available:
            return []
        await self._ensure_token()
        if not self._token:
            return []

        payload = {
            "Keywords": keyword,
            "RecordCount": min(max_results, 20),
            "RecordStartPosition": 0,
            "ExcludeMarketPlaceProducts": True,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.post(DIGIKEY_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[ComponentResult] = []
        products = data.get("Products") or data.get("ExactManufacturerProducts") or []
        for item in products[:max_results]:
            price = _parse_digikey_price(item.get("StandardPricing") or item.get("UnitPrice"))
            results.append(
                ComponentResult(
                    part_number=item.get("ManufacturerPartNumber") or item.get("DigiKeyPartNumber") or "",
                    manufacturer=item.get("Manufacturer", {}).get("Name", "") if isinstance(item.get("Manufacturer"), dict) else str(item.get("Manufacturer", "")),
                    description=item.get("ProductDescription") or item.get("DetailedDescription") or "",
                    package=item.get("PackageType") or "",
                    unit_price=price,
                    stock=_safe_int(item.get("QuantityAvailable")),
                    datasheet_url=item.get("DatasheetUrl") or item.get("PrimaryDatasheet") or "",
                    product_url=item.get("ProductUrl") or "",
                    source=self.source,
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
