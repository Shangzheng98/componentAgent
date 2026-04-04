# tests/test_digikey.py
"""DigiKey client tests — mocked HTTP, no network needed."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clients import DigiKeyClient


@pytest.fixture
def digikey():
    """Create a DigiKeyClient with fake credentials."""
    with patch.dict("os.environ", {
        "DIGIKEY_CLIENT_ID": "test-id",
        "DIGIKEY_CLIENT_SECRET": "test-secret",
    }):
        client = DigiKeyClient()
        client._token = "fake-token"  # skip OAuth
        return client


KEYWORD_RESPONSE = {
    "Products": [
        {
            "ManufacturerProductNumber": "STM32F103C8T6",
            "Manufacturer": {"Id": 630, "Name": "STMicroelectronics"},
            "Description": {
                "ProductDescription": "IC MCU 32BIT 64KB FLASH 48LQFP",
                "DetailedDescription": "ARM Cortex-M3 STM32F1 Microcontroller IC",
            },
            "UnitPrice": 2.57,
            "QuantityAvailable": 12345,
            "DatasheetUrl": "https://example.com/ds.pdf",
            "ProductUrl": "https://www.digikey.com/product/STM32F103C8T6",
            "ProductVariations": [
                {
                    "DigiKeyProductNumber": "497-6164-ND",
                    "PackageType": {"Id": 1, "Name": "Tape & Reel"},
                    "StandardPricing": [
                        {"BreakQuantity": 1, "UnitPrice": 2.57, "TotalPrice": 2.57},
                    ],
                }
            ],
            "Parameters": [
                {"ParameterId": 1, "ValueId": "1", "ValueText": "ARM Cortex-M3", "ParameterText": "Core Processor"},
            ],
            "ProductStatus": {"Id": 0, "Status": "Active"},
        }
    ],
    "ProductsCount": 1,
}


@pytest.mark.asyncio
async def test_search_request_body_uses_swagger_fields(digikey):
    """Request body must use Limit/Offset/FilterOptionsRequest, not old fields."""
    captured_kwargs = {}

    async def mock_post(url, **kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = KEYWORD_RESPONSE
        resp.raise_for_status = lambda: None
        return resp

    digikey._http.post = mock_post

    await digikey.search("STM32F103", max_results=5)

    body = captured_kwargs["json"]
    assert "Limit" in body, "Should use 'Limit' not 'RecordCount'"
    assert "Offset" in body, "Should use 'Offset' not 'RecordStartPosition'"
    assert "RecordCount" not in body, "Old field 'RecordCount' must be removed"
    assert "RecordStartPosition" not in body, "Old field must be removed"
    assert "ExcludeMarketPlaceProducts" not in body, "Use FilterOptionsRequest instead"
    assert body["FilterOptionsRequest"]["MarketPlaceFilter"] == "ExcludeMarketPlace"


@pytest.mark.asyncio
async def test_search_parses_swagger_response_fields(digikey):
    """Response parsing must handle nested Description, ManufacturerProductNumber, etc."""
    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = KEYWORD_RESPONSE
        resp.raise_for_status = lambda: None
        return resp

    digikey._http.post = mock_post

    results = await digikey.search("STM32F103", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.part_number == "STM32F103C8T6"
    assert r.manufacturer == "STMicroelectronics"
    assert r.description == "IC MCU 32BIT 64KB FLASH 48LQFP"
    assert r.unit_price == 2.57
    assert r.stock == 12345
    assert r.datasheet_url == "https://example.com/ds.pdf"
    assert r.product_url == "https://www.digikey.com/product/STM32F103C8T6"
    assert r.source == "digikey"


@pytest.mark.asyncio
async def test_search_sends_locale_headers(digikey):
    """DigiKey requests must include X-DIGIKEY-Locale-* headers."""
    captured_kwargs = {}

    async def mock_post(url, **kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = KEYWORD_RESPONSE
        resp.raise_for_status = lambda: None
        return resp

    digikey._http.post = mock_post

    await digikey.search("STM32F103", max_results=5)

    headers = captured_kwargs["headers"]
    assert "X-DIGIKEY-Locale-Currency" in headers
    assert "X-DIGIKEY-Locale-Site" in headers


PRODUCT_DETAILS_RESPONSE = {
    "Product": {
        "ManufacturerProductNumber": "STM32F103C8T6",
        "Manufacturer": {"Id": 630, "Name": "STMicroelectronics"},
        "Description": {
            "ProductDescription": "IC MCU 32BIT 64KB FLASH 48LQFP",
            "DetailedDescription": "ARM Cortex-M3 STM32F1 Microcontroller IC",
        },
        "UnitPrice": 2.57,
        "QuantityAvailable": 12345,
        "DatasheetUrl": "https://example.com/ds.pdf",
        "ProductUrl": "https://www.digikey.com/product/STM32F103C8T6",
        "ProductVariations": [
            {
                "DigiKeyProductNumber": "497-6164-ND",
                "PackageType": {"Id": 1, "Name": "Tape & Reel"},
                "StandardPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 2.57, "TotalPrice": 2.57},
                ],
            }
        ],
        "Parameters": [
            {"ParameterId": 1, "ValueText": "ARM Cortex-M3", "ParameterText": "Core Processor"},
            {"ParameterId": 2, "ValueText": "64KB", "ParameterText": "Program Memory Size"},
            {"ParameterId": 3, "ValueText": "20KB", "ParameterText": "RAM Size"},
        ],
        "ProductStatus": {"Id": 0, "Status": "Active"},
    },
    "SearchLocaleUsed": {"Site": "US", "Language": "en", "Currency": "USD"},
}


@pytest.mark.asyncio
async def test_get_detail_calls_productdetails_endpoint(digikey):
    """get_detail() should use GET /search/{pn}/productdetails."""
    captured_url = {}

    async def mock_get(url, **kwargs):
        captured_url["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = PRODUCT_DETAILS_RESPONSE
        resp.raise_for_status = lambda: None
        return resp

    digikey._http.get = mock_get
    result = await digikey.get_detail("STM32F103C8T6")
    assert result is not None
    assert "productdetails" in captured_url["url"]
    assert result.part_number == "STM32F103C8T6"
    assert result.manufacturer == "STMicroelectronics"
    assert result.description == "IC MCU 32BIT 64KB FLASH 48LQFP"
    assert result.stock == 12345
    assert result.parameters["Core Processor"] == "ARM Cortex-M3"
    assert result.parameters["Program Memory Size"] == "64KB"
