"""Выполнить произвольный BSL-код на сервере 1С через MCP и напечатать результат.

Usage: .venv/bin/python mcp_exec.py <file_with_bsl_code> [result_var]
Если result_var не задан — ищет в коде переменную Результат (строка) и печатает её.
"""
import asyncio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

URL = "http://127.0.0.1:6005/mcp"


async def main():
    code_file = sys.argv[1]
    result_var = sys.argv[2] if len(sys.argv) > 2 else "Результат"
    code = Path(code_file).read_text(encoding="utf-8")

    async with streamable_http_client(URL) as (read_stream, write_stream, _gid):
        async with ClientSession(read_stream, write_stream,
                                 client_info=Implementation(name="probe", version="1.0")) as session:
            await session.initialize()
            result = await session.call_tool("execute_code", arguments={"code": code})
            for c in result.content:
                print(getattr(c, "text", str(c)))
            if result.isError:
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
