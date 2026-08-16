import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_knowledge_mcp_stdio_handshake_and_tools(tmp_path: Path) -> None:
    knowledge_root = tmp_path / "knowledge_base"
    knowledge_root.mkdir()
    (knowledge_root / "sample.txt").write_text("Тендерный документ", encoding="utf-8")

    async def verify() -> None:
        env = dict(os.environ)
        env["TENDER_KNOWLEDGE_BASE"] = str(knowledge_root)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tender_parser.knowledge_mcp"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == "tlt-tender-knowledge"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "knowledge_status",
                    "list_documents",
                    "search_documents",
                    "read_document",
                }
                result = await session.call_tool("knowledge_status", {})
                assert result.is_error is False

    asyncio.run(verify())
