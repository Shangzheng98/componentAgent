"""数据模型定义 — 元器件搜索结果 & 搜索输入。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DataSource(str, Enum):
    """数据源枚举。"""

    BING = "bing"
    MOUSER = "mouser"
    DIGIKEY = "digikey"
    ALL = "all"


class ComponentResult(BaseModel):
    """单条元器件搜索结果。"""

    part_number: str = Field(description="物料编号")
    manufacturer: str = Field(default="", description="制造商")
    description: str = Field(default="", description="描述")
    package: str = Field(default="", description="封装")
    unit_price: Optional[float] = Field(default=None, description="单价 (USD)")
    stock: Optional[int] = Field(default=None, description="库存数量")
    datasheet_url: str = Field(default="", description="数据手册链接")
    product_url: str = Field(default="", description="产品页面链接")
    source: str = Field(default="", description="数据来源 (mouser/digikey)")
    parameters: dict[str, str] = Field(default_factory=dict, description="元器件参数")


class ComponentSearchInput(BaseModel):
    """搜索工具的输入参数。"""

    keyword: str = Field(description="搜索关键词，例如 'STM32F103' 或 '100nF 0603'")
    source: DataSource = Field(default=DataSource.ALL, description="数据源")
    max_results: int = Field(default=5, ge=1, le=20, description="每个数据源最大返回条数")


class ComponentDetailInput(BaseModel):
    """详情查询工具的输入参数。"""

    part_number: str = Field(description="物料编号")
    source: DataSource = Field(default=DataSource.ALL, description="数据源")
