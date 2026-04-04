# claude.md — 元器件搜索 MCP Server 项目文档

## 项目概述

一个 MCP Server，支持跨 LCSC / DigiKey / Mouser 搜索电子元器件，提供参数筛选、替代料推荐和 BOM 智能分析。

## 架构

```
src/
├── server.py      ← MCP 入口，注册 5 个工具
├── models.py      ← Pydantic 数据模型（输入/输出）
├── clients.py     ← 三家 API 客户端 + 工厂函数
└── formatters.py  ← Markdown / JSON 格式化器
```

## 5 个 MCP 工具

| 工具名 | 功能 | 只读 |
|--------|------|------|
| `component_search` | 关键字搜索，支持品类/封装/厂商筛选 | ✅ |
| `component_detail` | 按料号获取完整参数和价格 | ✅ |
| `component_find_alternatives` | 替代料推荐（匹配参数和封装） | ✅ |
| `bom_analyze` | BOM 分析：匹配/核价/库存/替代 | ✅ |
| `component_compare` | 多料号并排对比 | ✅ |

## API 客户端

| 数据源 | 客户端类 | 认证方式 | 环境变量 |
|--------|----------|----------|----------|
| LCSC (免费) | `LCSCClient` | 无需 key，非官方 API | — |
| DigiKey | `DigiKeyClient` | OAuth 2.0 Client Credentials | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` |
| Mouser | `MouserClient` | API Key in query string | `MOUSER_API_KEY` |

**降级策略**: 未配置 key 的数据源会静默跳过，至少 LCSC 始终可用。

## 关键 API 端点

- LCSC: `GET https://wwwapi.lcsc.com/v1/search/global-search?keyword=xxx`
- DigiKey Token: `POST https://api.digikey.com/v1/oauth2/token`
- DigiKey Search: `POST https://api.digikey.com/products/v4/search/keyword`
- Mouser Search: `POST https://api.mouser.com/api/v2/search/keyword?apiKey=xxx`
- Mouser Part: `POST https://api.mouser.com/api/v2/search/partnumber?apiKey=xxx`

## 开发记录

### v0.1.0 (初始版本)
- 搭建完整 MCP Server 框架（FastMCP + Pydantic v2）
- 实现 5 个工具：search / detail / alternatives / bom_analyze / compare
- 三家 API 客户端：LCSC（免费非官方）、DigiKey（OAuth 2.0）、Mouser（API Key）
- 双格式输出（Markdown / JSON）
- 37 个 stdlib-only 单元测试全部通过
- 验证项目：语法、代码结构、MCP 规范、格式化逻辑、解析辅助函数

### v0.1.1 (DigiKey Swagger 对齐)
- 根据 DigiKey 官方 Swagger (ProductSearch.json) 重写 DigiKeyClient：
  - 请求体字段：`Limit`/`Offset` 替代原来的 `RecordCount`/`RecordStartPosition`
  - InStock 筛选：通过 `FilterOptionsRequest.SearchOptions: ["InStock"]` 实现
  - MarketPlace 排除：通过 `FilterOptionsRequest.MarketPlaceFilter: "ExcludeMarketPlace"` 实现
  - 响应解析：`ProductStatus` 是 `{Id, Status}` 对象；`Description` 是 `{ProductDescription, DetailedDescription}` 对象
  - DigiKey 料号从 `ProductVariations[0].DigiKeyProductNumber` 提取
  - 添加 Locale headers (`X-DIGIKEY-Locale-Currency: USD`, `X-DIGIKEY-Locale-Site: US`)
- 新增 `_parse_product()` 静态方法统一 Product 对象解析（DRY）
- 替代料查找升级：优先使用 `GET /search/{pn}/substitutions` 官方端点，失败后降级为关键字搜索
- 全量 API endpoints 确认（14 个端点，已对接核心 3 个：KeywordSearch, ProductDetails, Substitutions）

### DigiKey API v4 完整端点清单
| 端点 | 方法 | 用途 | 项目中使用 |
|------|------|------|-----------|
| /search/keyword | POST | 关键字搜索 | ✅ component_search |
| /search/{pn}/productdetails | GET | 产品详情 | ✅ component_detail |
| /search/{pn}/substitutions | GET | 官方替代料 | ✅ find_alternatives |
| /search/{pn}/pricing | GET | 产品定价 | 待接入 |
| /search/{pn}/recommendedproducts | GET | 推荐产品 | 待接入 |
| /search/{pn}/alternatepackaging | GET | 替代封装 | 待接入 |
| /search/{pn}/pricingbyquantity/{qty} | GET | 按数量定价 | 待接入 |
| /search/{pn}/digireelpricing | GET | DigiReel 定价 | — |
| /search/{pn}/associations | GET | 关联产品 | — |
| /search/{pn}/media | GET | 产品媒体 | — |
| /search/manufacturers | GET | 厂商列表 | — |
| /search/categories | GET | 品类列表 | — |
| /search/categories/{id} | GET | 品类详情 | — |
| /search/packagetypebyquantity/{pn} | GET | 封装类型 | — |

### 测试状态 ✅ 37 tests (stdlib-only, 无需网络)
- `tests/test_server.py`: 完整测试（需 pytest + httpx + mcp 依赖）

### v0.1.2 (Substitutions 响应修正)
- 修正 DigiKey `find_alternatives` 中的 Substitutions 响应解析：
  - 字段从 `AssociatedProducts` 改为 Swagger 规范的 `ProductSubstitutes`
  - 每项是 `ProductSubstitute` 简化对象（非完整 Product），独立解析
- 修复性能 bug：`get_detail(part_number)` 从循环内移到循环外（避免 N 次重复请求）
- 37 tests ✅

### 待办
- [ ] Phase 1: 用户本地部署 + LCSC API 实际验证
- [ ] Phase 2: 申请并接入 Mouser / DigiKey API key
- [ ] Phase 3: HTTP 模式部署 + 应用集成
- [ ] 添加 LCSC 官方 API 支持（需申请 key）
- [ ] 添加结果缓存层（减少 API 调用）
- [ ] 添加 parametric search（按具体电气参数精确筛选）
