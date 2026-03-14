"""API 客户端 — LCSC / Mouser / DigiKey。"""

from __future__ import annotations

import os
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
# LCSC 客户端（免费，无需 API key）
# ---------------------------------------------------------------------------

LCSC_SEARCH_URL = "https://wmsc.lcsc.com/ftps/wm/search/global"
LCSC_DETAIL_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail"


class LCSCClient(BaseClient):
    """LCSC (立创商城) 非官方前端 API 客户端。"""

    source = "lcsc"

    async def search(self, keyword: str, max_results: int = 5) -> list[ComponentResult]:
        payload = {
            "keyword": keyword,
            "currentPage": 1,
            "pageSize": min(max_results, 20),
            "searchSource": "home",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.post(LCSC_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: list[ComponentResult] = []
        products = (data.get("result") or {}).get("tipProductDetailUrlVO") or []
        if not products:
            products = (data.get("result") or {}).get("productSearchResultVO") or []
            if isinstance(products, dict):
                products = products.get("productList") or []

        for item in products[:max_results]:
            results.append(
                ComponentResult(
                    part_number=item.get("productCode") or item.get("productModel") or "",
                    manufacturer=item.get("brandNameEn") or item.get("brandNameCn") or "",
                    description=item.get("productDescEn") or item.get("productDescCn") or "",
                    package=item.get("encapStandard") or "",
                    unit_price=_safe_float(item.get("productPrice")),
                    stock=_safe_int(item.get("stockNumber")),
                    datasheet_url=item.get("pdfUrl") or "",
                    product_url=f"https://www.lcsc.com/product-detail/{item.get('productCode', '')}.html",
                    source=self.source,
                )
            )
        return results

    async def get_detail(self, part_number: str) -> Optional[ComponentResult]:
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        try:
            resp = await self._http.post(
                LCSC_DETAIL_URL,
                json={"productCode": part_number},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        item = (data.get("result") or {})
        if not item:
            return None

        params: dict[str, str] = {}
        for p in item.get("paramVOList") or []:
            name = p.get("paramNameEn") or p.get("paramNameCn") or ""
            value = p.get("paramValueEn") or p.get("paramValueCn") or ""
            if name and value:
                params[name] = value

        return ComponentResult(
            part_number=item.get("productCode") or part_number,
            manufacturer=item.get("brandNameEn") or item.get("brandNameCn") or "",
            description=item.get("productDescEn") or item.get("productDescCn") or "",
            package=item.get("encapStandard") or "",
            unit_price=_safe_float(item.get("productPrice")),
            stock=_safe_int(item.get("stockNumber")),
            datasheet_url=item.get("pdfUrl") or "",
            product_url=f"https://www.lcsc.com/product-detail/{part_number}.html",
            source=self.source,
            parameters=params,
        )


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
    if source == DataSource.LCSC:
        return [LCSCClient()]
    if source == DataSource.MOUSER:
        return [MouserClient()]
    if source == DataSource.DIGIKEY:
        return [DigiKeyClient()]

    clients: list[BaseClient] = [LCSCClient()]
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
