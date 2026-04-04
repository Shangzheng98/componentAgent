# claude.md — 元器件搜索 MCP Server 项目文档

## 项目概述

一个 MCP Server，支持跨 Bing Search / DigiKey / Mouser 搜索电子元器件，提供参数查询、替代料推荐和需求驱动推荐。

## 架构

```
src/
├── server.py      ← MCP 入口，注册 4 个工具
├── models.py      ← Pydantic 数据模型（输入/输出）
├── clients.py     ← Bing / DigiKey / Mouser API 客户端 + 工厂函数
├── parsers.py     ← 产品页面 HTML 解析 + LLM fallback 提取参数
└── __main__.py    ← 启动入口（stdio / HTTP）
tests/
├── test_digikey.py       ← DigiKey 客户端单元测试
└── test_server_tools.py  ← MCP 工具测试
```

## 4 个 MCP 工具

| 工具名 | 功能 | 只读 |
|--------|------|------|
| `search_components` | 关键字搜索，支持多数据源并发查询 | ✅ |
| `get_component_detail` | 按料号获取完整参数（DigiKey API / Bing 抓取 + LLM fallback） | ✅ |
| `recommend_components` | 根据需求描述推荐元器件，按库存/价格排序 | ✅ |
| `find_alternatives` | 查找替代料（DigiKey 使用官方 Substitutions API） | ✅ |

## API 客户端

| 数据源 | 客户端类 | 认证方式 | 环境变量 |
|--------|----------|----------|----------|
| Bing Search | `BingSearchClient` | API Key in header | `BING_API_KEY` |
| DigiKey | `DigiKeyClient` | OAuth 2.0 Client Credentials | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` |
| Mouser | `MouserClient` | API Key in query string | `MOUSER_API_KEY` |

**降级策略**: 未配置 key 的数据源会静默跳过；Bing 作为基础数据源，DigiKey/Mouser 有 key 时加入。

**LLM Fallback**: `get_component_detail` 在 Bing 模式下抓取产品页面，已知站点（Mouser/DigiKey/LCSC）用 HTML 规则解析，未知站点 fallback 到 Claude Haiku 提取参数（需 `ANTHROPIC_API_KEY`）。

## 启动方式

### 安装

```bash
pip install -e .          # 安装依赖
pip install -e ".[dev]"   # 含测试依赖
```

### stdio 模式（配合 Claude Desktop / Cursor 等 MCP 客户端）

```bash
python -m src
```

### HTTP 模式（独立服务）

```bash
python -m src --transport http --port 8000
```

### 环境变量

```bash
export BING_API_KEY="xxx"            # Bing Search（核心数据源）
export MOUSER_API_KEY="xxx"          # Mouser（可选）
export DIGIKEY_CLIENT_ID="xxx"       # DigiKey（可选）
export DIGIKEY_CLIENT_SECRET="xxx"
export ANTHROPIC_API_KEY="xxx"       # LLM fallback 页面解析（可选）
```

### Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "component-search": {
      "command": "python",
      "args": ["-m", "src"],
      "cwd": "/path/to/componentAgent",
      "env": {
        "BING_API_KEY": "your-key"
      }
    }
  }
}
```

## 关键 API 端点

- Bing Search: `GET https://api.bing.microsoft.com/v7.0/search`
- DigiKey Token: `POST https://api.digikey.com/v1/oauth2/token`
- DigiKey Search: `POST https://api.digikey.com/products/v4/search/keyword` (body: `Limit`/`Offset`/`FilterOptionsRequest`)
- DigiKey Detail: `GET https://api.digikey.com/products/v4/search/{pn}/productdetails`
- DigiKey Substitutions: `GET https://api.digikey.com/products/v4/search/{pn}/substitutions`
- Mouser Search: `POST https://api.mouser.com/api/v1/search/keyword?apiKey=xxx`

## DigiKey API v4 端点清单

| 端点 | 方法 | 用途 | 项目中使用 |
|------|------|------|-----------|
| /search/keyword | POST | 关键字搜索 | ✅ `search_components` |
| /search/{pn}/productdetails | GET | 产品详情 | ✅ `get_component_detail` |
| /search/{pn}/substitutions | GET | 官方替代料 | ✅ `find_alternatives` |
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
| /search/packagetypebyquantity/{pn} | GET | 封装类型（已废弃） | — |

## 测试

```bash
python -m pytest tests/ -v   # 6 tests, mocked HTTP, 无需网络
```

| 测试文件 | 覆盖范围 |
|----------|---------|
| `tests/test_digikey.py` | DigiKey 请求体、响应解析、Locale 头、get_detail、find_alternatives |
| `tests/test_server_tools.py` | find_alternatives MCP 工具集成 |

## 开发记录

### v0.2.0 (Swagger v4 对齐 + 新端点接入)
- DigiKey 客户端全面对齐官方 Swagger v4 规范：
  - 请求体：`Limit`/`Offset`/`FilterOptionsRequest` 替代旧字段
  - 响应解析：嵌套 `Description`/`ManufacturerProductNumber`/`ProductVariations`
  - 新增 `_parse_product()` 静态方法统一解析（DRY）
  - 所有请求添加 `X-DIGIKEY-Locale-*` headers
- 新增 `get_detail()` — 调用 `GET /search/{pn}/productdetails` 获取完整产品参数
- 新增 `find_alternatives()` — 调用 `GET /search/{pn}/substitutions` 查找替代料
- 新增 `find_alternatives` MCP 工具
- 重构为 4 个 MCP 工具（移除未实现的 `bom_analyze`/`component_compare`）
- 6 个单元测试 ✅

### v0.1.0 (初始版本)
- MCP Server 框架搭建（FastMCP + Pydantic v2）
- 三家数据源客户端：Bing Search / DigiKey / Mouser
- 产品页面解析器：已知站点 HTML 规则解析 + LLM fallback

### 待办
- [ ] 申请并接入 Mouser / DigiKey API key 实测
- [ ] 接入 DigiKey `/pricing` 和 `/recommendedproducts` 端点
- [ ] 添加结果缓存层（减少 API 调用）
- [ ] 添加 parametric search（按具体电气参数精确筛选）
- [ ] HTTP 模式部署 + 应用集成
