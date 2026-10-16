"""El servidor arranca como proceso separado por stdio, igual que lo usarán el orquestador o MCP Inspector."""

import os
import sys

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from comun.config import RAIZ


async def test_servidor_por_stdio(db):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server"], cwd=str(RAIZ),
        env={**os.environ, "CENTINELA__rutas__db": str(db), "CENTINELA_LOG": "WARNING"},
    )
    async with Client(params) as c:
        nombres = {t.name for t in (await c.list_tools()).tools}
        r = await c.call_tool("buscar_cliente", {"texto": "lucia cojti"})
    assert "detectar_anomalias" in nombres
    assert r.structured_content["clientes"][0]["id"] == "CLI-0042"
