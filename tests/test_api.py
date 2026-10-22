"""API del orquestador: WebSocket (con aprobación), REST, SSE y el host MCP por stdio."""

import os
import sys

import pytest
from fastapi.testclient import TestClient

from comun.config import RAIZ
from orchestrator.app import crear_app
from orchestrator.mcp_host import HostMCP
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador
from tests.ollama_simulado import OllamaFalso, llamar
from tests.test_agente import TICKET


@pytest.fixture
def cliente(servidor, cfg, db):
    def _cliente(falso: OllamaFalso) -> TestClient:
        orq = Orquestador(cfg, Ollama(cfg, transport=falso.transport), HostMCP(servidor), db=db)
        return TestClient(crear_app(orq))
    return _cliente


def recibir_hasta(ws, tipo: str) -> list[dict]:
    eventos = []
    while True:
        e = ws.receive_json()
        eventos.append(e)
        if e["tipo"] == tipo:
            return eventos


def test_websocket_turno_con_aprobacion(cliente, db):
    import sqlite3

    with cliente(OllamaFalso(list(TICKET))) as c, c.websocket_connect("/ws/sala-1") as ws:
        lista = ws.receive_json()
        assert lista["tipo"] == "sesion.lista" and lista["v"] == 1
        datos = lista["datos"]
        assert datos["marca_demo"] == "DEMO / DATOS FICTICIOS" and datos["voz_disponible"] is False
        assert {h["nombre"]: h["escritura"] for h in datos["herramientas"]}["crear_ticket"] is True

        ws.send_json({"tipo": "mensaje", "texto": "Abra un ticket de fraude"})
        sol = recibir_hasta(ws, "aprobacion.solicitada")[-1]["datos"]
        ws.send_json({"tipo": "aprobacion", "aprobacion_id": sol["aprobacion_id"], "aprobar": True})
        eventos = recibir_hasta(ws, "turno.fin")
        assert any(e["tipo"] == "aprobacion.resuelta" and e["datos"]["aprobada"] for e in eventos)
        assert recibir_hasta(ws, "sesion.acumulado")[-1]["datos"]["herramientas_ejecutadas"] == 1

        ws.send_json({"tipo": "ping"})
        assert ws.receive_json()["tipo"] == "pong"
        ws.send_json({"tipo": "otra_cosa"})
        assert ws.receive_json()["datos"]["codigo"] == "mensaje_invalido"

        auditoria = c.get("/api/auditoria?limite=5").json()
        assert auditoria[0]["herramienta"] == "crear_ticket" and auditoria[0]["estado"] == "ok"
        historial = c.get("/api/sesiones/sala-1/historial").json()
        assert historial[0] == {"role": "user", "content": "Abra un ticket de fraude"}
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets WHERE creado_por LIKE 'centinela/%'"
                                       ).fetchone()[0] == 1


def test_rest_y_reanudar_por_websocket(cliente):
    falso = OllamaFalso([{"llamadas": [llamar("buscar_cliente", texto="Lucía Cojtí")]},
                         {"contenido": "Encontré a la clienta."}])
    with cliente(falso) as c:
        with c.websocket_connect("/ws/sala-2") as ws:
            ws.receive_json()
            r = c.post("/api/sesiones/sala-2/mensajes", json={"texto": "¿Quién es Lucía Cojtí?"})
            assert r.status_code == 202 and r.json() == {"turno_id": "t-0001"}
            eventos = recibir_hasta(ws, "turno.fin")
        fin = [e for e in eventos if e["tipo"] == "herramienta.fin"][0]
        assert fin["datos"]["resultado"]["coincidencias"] == 1
        # Una UI que se reconecta pide lo que se perdió desde un seq conocido.
        with c.websocket_connect(f"/ws/sala-2?desde_seq={fin['seq']}") as ws:
            primero = ws.receive_json()
            assert primero["seq"] == fin["seq"] + 1


def test_rest_varios(cliente):
    with cliente(OllamaFalso()) as c:
        salud = c.get("/api/salud").json()
        assert salud["mcp"] is True and salud["ollama"] == "0.30.0-falso" and salud["herramientas"] == 9
        assert salud["fecha_demo"] == "2026-11-20"
        assert [p["id"] for p in c.get("/api/guion").json()][:2] == ["cartera", "fraude"]
        assert c.post("/api/aprobaciones/ap-inexistente", json={"aprobar": True}).status_code == 404
        # 405 cuando web/ está montada en "/": la ruta normalizada cae en los archivos estáticos.
        assert c.post("/api/sesiones/../mensajes", json={"texto": "x"}).status_code in (400, 404, 405)
        assert c.post("/api/sesiones/s 1/mensajes", json={"texto": "x"}).status_code == 400

        doc = c.post("/api/documentos", files={"archivo": ("boleta.png", b"\x89PNG", "image/png")}).json()
        assert doc["nombre"] == "boleta.png"
        assert doc["documento_id"] in [d["documento_id"] for d in c.get("/api/documentos").json()]
        assert c.get(f"/api/documentos/{doc['documento_id']}").content == b"\x89PNG"
        assert c.get("/api/documentos/demo-..%2F..%2Fconfig.yaml").status_code == 404
        assert c.post("/api/documentos", files={"archivo": ("x.exe", b"MZ", "application/x")}).status_code == 415
        assert c.post("/api/sesiones/s1/preferencias", json={"hablar": False}).json() == {"ok": True}
        assert "Centinela" in c.get("/").text


def test_sse_entrega_eventos(servidor, cfg, db):
    """SSE con un uvicorn real (el TestClient espera a que termine la respuesta y un flujo SSE no termina)."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    falso = OllamaFalso([{"contenido": "Hola, ¿en qué le ayudo?"}])
    orq = Orquestador(cfg, Ollama(cfg, transport=falso.transport), HostMCP(servidor), db=db)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        puerto = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(crear_app(orq), host="127.0.0.1", port=puerto, log_level="warning"))
    hilo = threading.Thread(target=srv.run, daemon=True)
    hilo.start()
    try:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        base = f"http://127.0.0.1:{puerto}"
        with httpx.Client(base_url=base, trust_env=False, timeout=10) as c:
            assert c.post("/api/sesiones/sala-3/mensajes", json={"texto": "Hola"}).status_code == 202
            # Reanudar desde seq 0 devuelve todo el turno y luego queda escuchando.
            with c.stream("GET", "/api/sesiones/sala-3/eventos", headers={"Last-Event-ID": "0"}) as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                tipos, ids = [], []
                for linea in r.iter_lines():
                    if linea.startswith("id: "):
                        ids.append(int(linea[4:]))
                    if linea.startswith("event: "):
                        tipos.append(linea[7:])
                        if tipos[-1] == "sesion.acumulado":
                            break
        assert tipos[0] == "turno.inicio" and "respuesta.final" in tipos and "turno.fin" in tipos
        assert ids == sorted(ids) and ids[0] == 1
    finally:
        srv.should_exit = True
        hilo.join(10)


async def test_host_mcp_por_stdio_con_aprobacion(db, cfg):
    """El orquestador arranca el servidor MCP como proceso hijo, como en la demo."""
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], cwd=str(RAIZ),
                                   env={**os.environ, "CENTINELA__rutas__db": str(db), "CENTINELA_LOG": "WARNING"})
    host = HostMCP(params)

    class Aprobador:
        mensajes: list[str] = []

        async def pedir_aprobacion(self, mensaje, esquema):
            self.mensajes.append(mensaje)
            return True, "ok"

    aprobador = Aprobador()
    await host.conectar()
    try:
        assert len(host.herramientas) == 9
        r = await host.llamar("crear_ticket", {"tipo": "servicio", "prioridad": "baja", "titulo": "Prueba stdio",
                                               "detalle": "x"}, {"usuario": "prueba", "modelo": "m"}, 20,
                              solicitante=aprobador)
    finally:
        await host.cerrar()
    assert r.ok and r.datos["creado"] is True
    assert "Centinela quiere crear este ticket" in aprobador.mensajes[0]


def test_panel_de_soberania_se_monta_si_existe(cliente, monkeypatch):
    """Con el módulo de la Fase 5 presente, sus rutas quedan bajo /api/soberania; sin él, la API arranca igual."""
    import sys
    import types

    from fastapi import APIRouter

    def crear_router(cfg):
        r = APIRouter(prefix="/api/soberania")
        r.get("")(lambda: {"conexiones_salientes": 0})
        return r

    # Sin el módulo (None en sys.modules hace que el import falle con ModuleNotFoundError).
    monkeypatch.setitem(sys.modules, "orchestrator.soberania", None)
    with cliente(OllamaFalso()) as c:
        assert c.get("/api/soberania").status_code in (404, 405)
    monkeypatch.setitem(sys.modules, "orchestrator.soberania", types.SimpleNamespace(crear_router=crear_router))
    with cliente(OllamaFalso()) as c:
        assert c.get("/api/soberania").json() == {"conexiones_salientes": 0}
