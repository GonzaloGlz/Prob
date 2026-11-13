# mcp_client.py
"""Cliente ligero para LiveScore MCP (sin API key, límite 30 req/min por IP)."""
import asyncio
from fastmcp import Client

LIVESCORE_MCP_URL = "https://livescoremcp.com/mcp"


async def _get_h2h_async(home: str, away: str) -> str:
    async with Client(LIVESCORE_MCP_URL) as client:
        result = await client.call_tool(
            "get_head_to_head",
            {"team_a": home, "team_b": away},
        )
        if hasattr(result, "content"):
            if isinstance(result.content, list):
                return "\n".join(
                    str(item.text) if hasattr(item, "text") else str(item)
                    for item in result.content
                )
            return str(result.content)
        return str(result)


def get_livescore_h2h(home: str, away: str) -> str:
    """Función síncrona para usar desde Streamlit/Ollama."""
    try:
        return asyncio.run(_get_h2h_async(home, away))
    except Exception as e:
        return f"[LiveScore MCP no disponible] {e}"