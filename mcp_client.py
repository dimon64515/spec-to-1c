import asyncio
import json
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from config import get_config

URL = get_config()["mcp"]["url"]

async def main():
    async with streamable_http_client(URL) as (read_stream, write_stream, get_session_id):
        async with ClientSession(read_stream, write_stream, client_info=Implementation(name="test", version="1.0")) as session:
            init_result = await session.initialize()
            print("Initialized:", init_result)
            print("Session ID:", get_session_id())
            tools = await session.list_tools()
            print("Tools:")
            for t in tools.tools:
                print(f"  {t.name}: {t.description}")
                if t.inputSchema:
                    print(f"    schema: {json.dumps(t.inputSchema, ensure_ascii=False, indent=2)[:500]}")

if __name__ == "__main__":
    asyncio.run(main())
