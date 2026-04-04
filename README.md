# 元器件搜索 MCP Server

一个 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) Server，让 AI 助手能够搜索电子元器件、查询参数、查找替代料。

支持 Bing Search / DigiKey / Mouser 多数据源。

## 功能

| 工具 | 说明 |
|------|------|
| `search_components` | 关键字搜索元器件（多数据源并发） |
| `get_component_detail` | 按料号获取详细参数和价格 |
| `recommend_components` | 根据需求描述推荐元器件 |
| `find_alternatives` | 查找替代料（DigiKey Substitutions API） |

## 快速开始

### 安装

```bash
pip install -e .
```

### 配置环境变量

```bash
# 至少配置一个数据源
export BING_API_KEY="xxx"            # Bing Search（核心数据源）
export MOUSER_API_KEY="xxx"          # Mouser（可选）
export DIGIKEY_CLIENT_ID="xxx"       # DigiKey（可选）
export DIGIKEY_CLIENT_SECRET="xxx"
export ANTHROPIC_API_KEY="xxx"       # LLM fallback 页面解析（可选）
```

未配置 key 的数据源会静默跳过。

### 启动

```bash
# stdio 模式（配合 Claude Desktop / Cursor 等 MCP 客户端）
python -m src

# HTTP 模式
python -m src --transport http --port 8000
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

## 数据源

| 数据源 | 认证方式 | 能力 |
|--------|----------|------|
| **Bing Search** | API Key | 搜索 + 页面抓取解析详情 |
| **DigiKey** | OAuth 2.0 | 搜索 + 详情 + 替代料（Swagger v4） |
| **Mouser** | API Key | 搜索 |

DigiKey 客户端已对齐官方 Swagger v4 规范，支持 KeywordSearch、ProductDetails、Substitutions 三个端点。

## 开发

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## 技术栈

- Python 3.10+
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP Server 框架
- [Pydantic v2](https://docs.pydantic.dev/) — 数据模型
- [httpx](https://www.python-httpx.org/) — 异步 HTTP 客户端
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML 解析
