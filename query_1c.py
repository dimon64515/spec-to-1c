import asyncio
import json
import logging
import sys
from pathlib import Path
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from config import get_config


logger = logging.getLogger(__name__)

URL = get_config()["mcp"]["url"]

async def execute(query: str, params=None, limit=100, include_schema=False):
    async with streamable_http_client(URL) as (read_stream, write_stream, get_session_id):
        async with ClientSession(read_stream, write_stream, client_info=Implementation(name="query", version="1.0")) as session:
            await session.initialize()
            result = await session.call_tool("execute_query", arguments={
                "query": query,
                "params": params or {},
                "limit": limit,
                "include_schema": include_schema
            })
            # result.content may contain TextContent objects
            out = []
            for c in result.content:
                out.append(getattr(c, 'text', str(c)))
            return "\n".join(out)

if __name__ == "__main__":
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    out_file = sys.argv[3] if len(sys.argv) > 3 else "query_result.json"
    res = asyncio.run(execute(query, limit=limit, include_schema=True))
    Path(out_file).write_text(res, encoding="utf-8")
    logger.info("Saved %s", out_file)
