"""快速验证 LCSC API 是否可用。"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.clients import LCSCClient


async def main():
    client = LCSCClient()
    try:
        print("=" * 50)
        print("测试: 搜索 100nF 0603 电容")
        results = await client.search("100nF 0603", max_results=3)
        for r in results:
            print(f"  {r.part_number} | {r.manufacturer} | {r.package} | ${r.unit_price} | stock={r.stock}")

        print("\n测试: 搜索 STM32F103")
        results = await client.search("STM32F103", max_results=3)
        for r in results:
            print(f"  {r.part_number} | {r.manufacturer} | ${r.unit_price} | stock={r.stock}")

        if results:
            print(f"\n测试: 获取 {results[0].part_number} 详情")
            detail = await client.get_detail(results[0].part_number)
            if detail:
                print(f"  参数: {detail.parameters}")

        print("\nLCSC API 工作正常!")
    except Exception as e:
        print(f"错误: {e}")
    finally:
        await client.close()


asyncio.run(main())
