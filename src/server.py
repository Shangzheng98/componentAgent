"""MCP Server — 元器件搜索工具。"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from .clients import get_clients
from .models import ComponentDetailInput, ComponentResult, ComponentSearchInput, DataSource

mcp = FastMCP(
    "component-search",
    description="电子元器件垂直搜索服务 — 支持 Bing Search / Mouser / DigiKey 多源查询",
)


# ---------------------------------------------------------------------------
# Tool: search_components — 搜索元器件
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_components(
    keyword: str,
    source: str = "all",
    max_results: int = 5,
) -> str:
    """搜索电子元器件。

    根据关键词搜索元器件，通过 Bing 垂直搜索元器件分销商网站，
    也可使用 Mouser / DigiKey 官方 API（需配置 API key）。

    Args:
        keyword: 搜索关键词，例如 "STM32F103" 或 "100nF 0603 电容"
        source: 数据源 (bing / mouser / digikey / all)，默认 all
        max_results: 每个数据源最大返回条数 (1-20)，默认 5
    """
    params = ComponentSearchInput(
        keyword=keyword,
        source=DataSource(source),
        max_results=max_results,
    )

    clients = get_clients(params.source)
    all_results: list[ComponentResult] = []

    tasks = [client.search(params.keyword, params.max_results) for client in clients]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    for results in results_per_source:
        if isinstance(results, list):
            all_results.extend(results)

    for client in clients:
        await client.close()

    if not all_results:
        return json.dumps({"message": f"未找到与 '{keyword}' 相关的元器件", "results": []}, ensure_ascii=False)

    return json.dumps(
        {
            "message": f"找到 {len(all_results)} 条结果",
            "results": [r.model_dump() for r in all_results],
        },
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Tool: get_component_detail — 获取元器件详情
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_component_detail(
    part_number: str,
    source: str = "all",
) -> str:
    """获取元器件详细参数。

    根据物料编号查询元器件的详细参数（电气参数、封装等）。

    Args:
        part_number: 物料编号
        source: 数据源 (bing / mouser / digikey / all)，默认 all
    """
    params = ComponentDetailInput(part_number=part_number, source=DataSource(source))
    clients = get_clients(params.source)

    detail = None
    for client in clients:
        detail = await client.get_detail(params.part_number)
        await client.close()
        if detail:
            break

    if not detail:
        return json.dumps({"message": f"未找到 {part_number} 的详细信息", "detail": None}, ensure_ascii=False)

    return json.dumps(
        {
            "message": f"已获取 {part_number} 的详情",
            "detail": detail.model_dump(),
        },
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Tool: recommend_components — 物料推荐
# ---------------------------------------------------------------------------


@mcp.tool()
async def recommend_components(
    requirement: str,
    category: str = "",
    max_results: int = 5,
) -> str:
    """根据需求推荐元器件。

    描述你的需求（如 "3.3V LDO 稳压器，输出 500mA"），系统会搜索并
    按库存和价格排序，给出推荐列表。

    Args:
        requirement: 需求描述，例如 "3.3V LDO 500mA" 或 "100uF 低ESR 电解电容"
        category: 可选的器件类别提示，例如 "电容" "电阻" "MCU" "LDO"
        max_results: 最大推荐条数 (1-20)，默认 5
    """
    search_keyword = f"{requirement} {category}".strip()

    clients = get_clients(DataSource.ALL)
    all_results: list[ComponentResult] = []

    tasks = [client.search(search_keyword, max_results * 2) for client in clients]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    for results in results_per_source:
        if isinstance(results, list):
            all_results.extend(results)

    for client in clients:
        await client.close()

    if not all_results:
        return json.dumps(
            {"message": f"未找到与 '{requirement}' 相关的推荐", "recommendations": []},
            ensure_ascii=False,
        )

    # 排序：有库存优先，然后按价格升序
    def sort_key(r: ComponentResult) -> tuple:
        has_stock = 1 if (r.stock and r.stock > 0) else 0
        price = r.unit_price if r.unit_price is not None else 9999999
        return (-has_stock, price)

    all_results.sort(key=sort_key)
    recommended = all_results[:max_results]

    return json.dumps(
        {
            "message": f"为 '{requirement}' 推荐 {len(recommended)} 款元器件",
            "recommendations": [r.model_dump() for r in recommended],
        },
        ensure_ascii=False,
        indent=2,
    )
