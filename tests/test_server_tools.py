"""Tests for MCP server tools."""
from __future__ import annotations
import json
from unittest.mock import AsyncMock, patch
import pytest
from src.models import ComponentResult

@pytest.mark.asyncio
async def test_find_alternatives_tool_returns_results():
    mock_results = [
        ComponentResult(
            part_number="STM32F103CBT6",
            manufacturer="STMicroelectronics",
            description="IC MCU 32BIT 128KB FLASH",
            stock=8000,
            source="digikey",
        ),
    ]
    with patch("src.server.get_clients") as mock_get_clients:
        mock_client = AsyncMock()
        mock_client.find_alternatives.return_value = mock_results
        mock_client.close = AsyncMock()
        mock_get_clients.return_value = [mock_client]
        from src.server import find_alternatives
        result = await find_alternatives(part_number="STM32F103C8T6")
    data = json.loads(result)
    assert len(data["alternatives"]) == 1
    assert data["alternatives"][0]["part_number"] == "STM32F103CBT6"
