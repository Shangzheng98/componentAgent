# tests/test_mouser.py
"""Mouser client tests — mocked HTTP, no network needed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.clients import MouserClient


@pytest.fixture
def mouser():
    """Create a MouserClient with fake credentials."""
    with patch.dict("os.environ", {"MOUSER_API_KEY": "test-key"}):
        return MouserClient()


KEYWORD_RESPONSE = {
    "SearchResults": {
        "NumberOfResult": 1,
        "Parts": [
            {
                "ManufacturerPartNumber": "STM32F103C8T6",
                "MouserPartNumber": "511-STM32F103C8T6",
                "Manufacturer": "STMicroelectronics",
                "Description": "ARM Microcontrollers - MCU 32BIT Cortex M3 64KB Flash",
                "DataSheetUrl": "https://example.com/ds.pdf",
                "ProductDetailUrl": "https://www.mouser.com/ProductDetail/STM32F103C8T6",
                "Availability": "2,000 In Stock",
                "AvailabilityInStock": "2000",
                "LifecycleStatus": "New Product",
                "LeadTime": "6 weeks",
                "ROHSStatus": "RoHS Compliant",
                "Min": "1",
                "Mult": "1",
                "SuggestedReplacement": "",
                "AlternatePackagings": [],
                "PriceBreaks": [
                    {"Quantity": 1, "Price": "$5.91", "Currency": "USD"},
                    {"Quantity": 10, "Price": "$5.32", "Currency": "USD"},
                ],
                "ProductAttributes": [
                    {"AttributeName": "Package / Case", "AttributeValue": "48-LQFP"},
                    {"AttributeName": "Core Processor", "AttributeValue": "ARM Cortex-M3"},
                    {"AttributeName": "Program Memory Size", "AttributeValue": "64KB"},
                    {"AttributeName": "RAM Size", "AttributeValue": "20KB"},
                ],
            }
        ],
    }
}


def _mock_post_response(data):
    """Create a mock HTTP response."""
    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = data
        resp.raise_for_status = lambda: None
        return resp
    return mock_post


@pytest.mark.asyncio
async def test_search_parses_product_attributes(mouser):
    """search() should parse ProductAttributes into parameters."""
    mouser._http.post = _mock_post_response(KEYWORD_RESPONSE)

    results = await mouser.search("STM32F103", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.part_number == "STM32F103C8T6"
    assert r.manufacturer == "STMicroelectronics"
    assert r.package == "48-LQFP"
    assert r.unit_price == 5.91
    assert r.stock == 2000
    assert r.source == "mouser"
    # parameters should include ProductAttributes + lifecycle info
    assert r.parameters["Core Processor"] == "ARM Cortex-M3"
    assert r.parameters["Program Memory Size"] == "64KB"
    assert r.parameters["LifecycleStatus"] == "New Product"
    assert r.parameters["LeadTime"] == "6 weeks"
    assert r.parameters["MinOrderQty"] == "1"


@pytest.mark.asyncio
async def test_search_uses_availability_in_stock(mouser):
    """search() should prefer AvailabilityInStock over Availability string."""
    mouser._http.post = _mock_post_response(KEYWORD_RESPONSE)

    results = await mouser.search("STM32F103", max_results=5)
    assert results[0].stock == 2000


@pytest.mark.asyncio
async def test_get_detail_uses_partnumber_endpoint(mouser):
    """get_detail() should call /search/partnumber with Exact option."""
    captured = {}

    async def mock_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = KEYWORD_RESPONSE
        resp.raise_for_status = lambda: None
        return resp

    mouser._http.post = mock_post
    result = await mouser.get_detail("STM32F103C8T6")

    assert "partnumber" in captured["url"]
    assert captured["json"]["SearchByPartRequest"]["mouserPartNumber"] == "STM32F103C8T6"
    assert captured["json"]["SearchByPartRequest"]["partSearchOptions"] == "Exact"
    assert result is not None
    assert result.part_number == "STM32F103C8T6"
    assert result.parameters["Package / Case"] == "48-LQFP"


PART_WITH_REPLACEMENT = {
    "SearchResults": {
        "NumberOfResult": 1,
        "Parts": [
            {
                "ManufacturerPartNumber": "OLD-PART-123",
                "Manufacturer": "TestMfr",
                "Description": "Old part",
                "SuggestedReplacement": "NEW-PART-456",
                "AlternatePackagings": [],
                "PriceBreaks": [],
                "ProductAttributes": [],
                "Availability": "0",
                "ProductDetailUrl": "",
                "DataSheetUrl": "",
            }
        ],
    }
}

REPLACEMENT_RESPONSE = {
    "SearchResults": {
        "NumberOfResult": 1,
        "Parts": [
            {
                "ManufacturerPartNumber": "NEW-PART-456",
                "Manufacturer": "TestMfr",
                "Description": "Replacement part",
                "SuggestedReplacement": "",
                "AlternatePackagings": [],
                "PriceBreaks": [{"Quantity": 1, "Price": "$1.00", "Currency": "USD"}],
                "ProductAttributes": [],
                "Availability": "500 In Stock",
                "AvailabilityInStock": "500",
                "ProductDetailUrl": "https://mouser.com/new-part",
                "DataSheetUrl": "",
            }
        ],
    }
}


@pytest.mark.asyncio
async def test_find_alternatives_uses_suggested_replacement(mouser):
    """find_alternatives() should follow SuggestedReplacement."""
    call_count = {"n": 0}

    async def mock_post(url, **kwargs):
        call_count["n"] += 1
        body = kwargs.get("json", {})
        pn = body.get("SearchByPartRequest", {}).get("mouserPartNumber", "")
        data = REPLACEMENT_RESPONSE if pn == "NEW-PART-456" else PART_WITH_REPLACEMENT
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = data
        resp.raise_for_status = lambda: None
        return resp

    mouser._http.post = mock_post
    results = await mouser.find_alternatives("OLD-PART-123")

    assert len(results) == 1
    assert results[0].part_number == "NEW-PART-456"
    assert results[0].stock == 500
