# 实施指南 — 元器件搜索 MCP Server (Windows)

## 总览

三步走策略：
1. **Phase 1**: 本地装好 → LCSC 免费 API 跑通（无需任何 key）
2. **Phase 2**: 申请 Mouser / DigiKey API → 接入三源
3. **Phase 3**: 集成到你的应用 → HTTP 模式对外提供服务

---

## Phase 1: 本地搭建 + LCSC 跑通 (30 分钟)

### 1.1 环境准备

```powershell
# 确认 Python 版本 >= 3.10
python --version

# 克隆项目（或直接复制文件）
cd C:\Projects
mkdir component-search-mcp
# 将项目文件复制到此目录

# 创建虚拟环境
cd component-search-mcp
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install "mcp[cli]>=1.0.0" httpx pydantic
pip install pytest pytest-asyncio   # 开发依赖
```

### 1.2 验证安装

```powershell
# 语法检查
python -c "from src.models import ComponentSearchInput; print('models OK')"
python -c "from src.clients import LCSCClient; print('clients OK')"
python -c "from src.server import mcp; print('server OK')"
```

### 1.3 本地调试 — LCSC 搜索测试

创建 `scripts/test_lcsc.py`：

```python
"""快速验证 LCSC API 是否可用。"""
import asyncio
from src.clients import LCSCClient

async def main():
    client = LCSCClient()
    try:
        # 测试 1: 搜索电容
        print("=" * 50)
        print("测试: 搜索 100nF 0603 电容")
        results = await client.search("100nF 0603", max_results=3)
        for r in results:
            print(f"  {r.part_number} | {r.manufacturer} | {r.package} | ${r.unit_price} | stock={r.stock}")

        # 测试 2: 搜索 MCU
        print("\n测试: 搜索 STM32F103")
        results = await client.search("STM32F103", max_results=3)
        for r in results:
            print(f"  {r.part_number} | {r.manufacturer} | ${r.unit_price} | stock={r.stock}")

        # 测试 3: 获取详情
        if results:
            print(f"\n测试: 获取 {results[0].part_number} 详情")
            detail = await client.get_detail(results[0].part_number)
            if detail:
                print(f"  参数: {detail.parameters}")

        print("\n✅ LCSC API 工作正常!")
    except Exception as e:
        print(f"❌ 错误: {e}")
    finally:
        await client.close()

asyncio.run(main())
```

运行：
```powershell
python scripts/test_lcsc.py
```

### 1.4 启动 MCP Server (stdio 模式)

```powershell
python -m src
```

### 1.5 用 MCP Inspector 测试

```powershell
npx @modelcontextprotocol/inspector python -m src
```

Inspector 会在浏览器打开一个 UI，你可以在里面测试每个工具。

---

## Phase 2: 接入 DigiKey + Mouser (各 15-30 分钟)

### 2.1 Mouser API Key（最简单，推荐先做）

1. **注册**: 访问 https://www.mouser.com 创建账号
2. **获取 Key**: 登录后进入 My Account → APIs → 填表 → Generate Key
3. **配置**:

```powershell
# Windows 环境变量 (当前会话)
set MOUSER_API_KEY=你的key

# 或写入 .env 文件
echo MOUSER_API_KEY=你的key > .env
```

4. **验证**:

```python
# scripts/test_mouser.py
import asyncio, os
os.environ["MOUSER_API_KEY"] = "你的key"
from src.clients import MouserClient

async def main():
    client = MouserClient()
    results = await client.search("STM32F103C8T6", max_results=3)
    for r in results:
        print(f"  {r.part_number} | {r.manufacturer} | ${r.unit_price} | stock={r.stock}")
    await client.close()

asyncio.run(main())
```

### 2.2 DigiKey API（需要 OAuth，稍复杂）

1. **注册开发者账号**: https://developer.digikey.com/
2. **创建 Application**:
   - 登录 → My Apps → Create App
   - 选 "Product Information" API → "Production"
   - **OAuth Callback URL**: 随便填 `https://localhost/callback`（2-legged flow 不需要）
   - 记下 **Client ID** 和 **Client Secret**
3. **配置**:

```powershell
set DIGIKEY_CLIENT_ID=你的client_id
set DIGIKEY_CLIENT_SECRET=你的client_secret
```

4. **验证**:

```python
# scripts/test_digikey.py
import asyncio, os
os.environ["DIGIKEY_CLIENT_ID"] = "你的id"
os.environ["DIGIKEY_CLIENT_SECRET"] = "你的secret"
from src.clients import DigiKeyClient

async def main():
    client = DigiKeyClient()
    results = await client.search("LM7805", max_results=3)
    for r in results:
        print(f"  {r.part_number} | {r.manufacturer} | ${r.unit_price}")
    await client.close()

asyncio.run(main())
```

> **注意**: DigiKey Sandbox 和 Production 的 URL 不同。代码默认使用 Production URL。
> 如果想先用 Sandbox 测试，修改 `clients.py` 中的 URL 常量为 `sandbox-api.digikey.com`。

---

## Phase 3: 你的应用调用 MCP Server

### 方案 A: HTTP 模式（推荐用于自己的应用）

启动 HTTP server：
```powershell
python -m src --transport http --port 8000
```

你的应用通过标准 MCP Streamable HTTP 协议连接：
```
POST http://localhost:8000/mcp
```

### 方案 B: 作为子进程 (stdio)

你的应用 spawn 一个子进程：
```python
import subprocess
proc = subprocess.Popen(
    ["python", "-m", "src"],
    cwd="C:/Projects/component-search-mcp",
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env={**os.environ, "MOUSER_API_KEY": "xxx", "DIGIKEY_CLIENT_ID": "xxx", "DIGIKEY_CLIENT_SECRET": "xxx"},
)
# 通过 stdin/stdout 发送 JSON-RPC 消息
```

### 方案 C: 直接 import 作为 Python 库

```python
import asyncio
from src.clients import get_clients
from src.models import DataSource

async def search_components(keyword: str):
    clients = get_clients(DataSource.ALL)
    all_results = []
    for client in clients:
        results = await client.search(keyword=keyword, max_results=5)
        all_results.extend(results)
        await client.close()
    return all_results

# 在你的 FastAPI / Flask / Django 中调用
results = asyncio.run(search_components("STM32F103"))
```

---

## 环境变量汇总

| 变量 | 必须 | 说明 |
|------|------|------|
| `MOUSER_API_KEY` | 可选 | Mouser Search API key |
| `DIGIKEY_CLIENT_ID` | 可选 | DigiKey OAuth Client ID |
| `DIGIKEY_CLIENT_SECRET` | 可选 | DigiKey OAuth Client Secret |
| `LCSC_API_KEY` | 可选 | LCSC 官方 API key（如有） |
| `LCSC_API_SECRET` | 可选 | LCSC 官方 API secret（如有） |

> 不配置任何 key 也可以运行 — LCSC 非官方 API 免费无需 key。

---

## 常见问题

**Q: LCSC 非官方 API 稳定吗？**
A: 它是 lcsc.com 前端用的接口，可能随网站改版而变化。建议同时申请 LCSC 官方 API (lcsc.com/agent) 作为备选。限制：~100 req/min。

**Q: DigiKey API 有免费额度吗？**
A: 有。Production API 免费，但有速率限制。Sandbox 可以用来开发测试。

**Q: Mouser API 限制？**
A: 免费注册即可使用 Search API，默认速率限制足够个人/小团队使用。

**Q: 能否部署成公网服务？**
A: 可以用 HTTP 模式部署，但要注意 API key 安全和速率限制。建议加一层认证。
