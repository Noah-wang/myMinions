import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _serialize(value: Any) -> Any:
	if hasattr(value, "model_dump"):
		return value.model_dump(mode="json")
	if isinstance(value, list):
		return [_serialize(item) for item in value]
	if isinstance(value, dict):
		return {key: _serialize(item) for key, item in value.items()}
	return value


@asynccontextmanager
async def coros_session() -> AsyncIterator[ClientSession]:
	url = os.getenv("COROS_MCP_URL", "https://mcpus.coros.com/mcp")
	server = StdioServerParameters(
		command="npx",
		args=["mcp-remote", url],
	)

	async with stdio_client(server) as streams:
		async with ClientSession(*streams) as session:
			await session.initialize()
			yield session


async def list_coros_tools() -> list[dict[str, Any]]:
	async with coros_session() as session:
		result = await session.list_tools()
		return _serialize(result.tools)


async def call_coros_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
	async with coros_session() as session:
		result = await session.call_tool(name, arguments or {})
		return _serialize(result)


def compact_json(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, indent=2)
