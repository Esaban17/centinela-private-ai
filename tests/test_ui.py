"""UI local: assets 100 % locales y simulador hablando el contrato de eventos v1."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from comun.config import RAIZ

WEB = RAIZ / "web"


def test_assets_sin_recursos_externos():
    """Ningún archivo de web/ referencia http(s):// ni //cdn: todo se sirve desde el equipo."""
    externo = re.compile(r"(?:https?:)?//(?!www\.w3\.org/2000/svg)[a-z0-9.-]+\.[a-z]{2,}", re.I)
    for f in WEB.rglob("*"):
        if f.suffix in {".html", ".css", ".js", ".svg"}:
            hallados = externo.findall(f.read_text(encoding="utf-8"))
            assert not hallados, f"{f.relative_to(RAIZ)} referencia recursos externos: {hallados}"


def test_politica_de_seguridad_bloquea_externos():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in html and "default-src 'self'" in html
    assert "DEMO / DATOS FICTICIOS" in html


@pytest.fixture
def simulador(db, cfg, monkeypatch):
    from mcp_server.server import crear_servidor
    from scripts import simulador_ui

    monkeypatch.setattr(simulador_ui, "crear_servidor", lambda: crear_servidor(db, cfg))
    monkeypatch.setattr(simulador_ui.config, "ruta", lambda clave: Path(db))
    monkeypatch.setattr(simulador_ui.asyncio, "sleep", _sin_espera)
    with TestClient(simulador_ui.app) as c:
        yield c


async def _sin_espera(_s):
    return None


def _turno(ws, mensaje, aprobar=None):
    ws.send_json(mensaje)
    eventos = []
    while True:
        ev = ws.receive_json()
        eventos.append(ev)
        if ev["tipo"] == "aprobacion.solicitada" and aprobar is not None:
            ws.send_json({"tipo": "aprobacion", "aprobacion_id": ev["datos"]["aprobacion_id"], "aprobar": aprobar})
        if ev["tipo"] == "sesion.acumulado":
            return eventos


def test_simulador_contrato_y_aprobacion(simulador):
    assert simulador.get("/").status_code == 200
    assert len(simulador.get("/api/guion").json()) >= 5
    with simulador.websocket_connect("/ws/prueba") as ws:
        assert ws.receive_json()["tipo"] == "sesion.lista"
        ev = _turno(ws, {"tipo": "mensaje", "texto": "¿Ve movimientos raros de Lucía Cojtí este mes?"})
        tipos = [e["tipo"] for e in ev]
        for t in ("turno.inicio", "modelo.uso", "llm.delta", "herramienta.inicio", "herramienta.fin",
                  "respuesta.final", "turno.fin", "sesion.acumulado"):
            assert t in tipos, t
        assert all(e["v"] == 1 and {"seq", "sesion_id", "turno_id", "ts", "datos"} <= set(e) for e in ev)
        seqs = [e["seq"] for e in ev]
        assert seqs == sorted(seqs)
        fin = next(e for e in ev if e["tipo"] == "herramienta.fin" and e["datos"]["nombre"] == "detectar_anomalias")
        assert fin["datos"]["resultado"]["total_hallazgos"] >= 3   # resultado real del servidor MCP

        ev = _turno(ws, {"tipo": "mensaje", "texto": "Abra un ticket para fraude"}, aprobar=False)
        resuelta = next(e for e in ev if e["tipo"] == "aprobacion.resuelta")
        assert resuelta["datos"]["aprobada"] is False
        assert "no fue aprobada" in next(e for e in ev if e["tipo"] == "respuesta.final")["datos"]["texto"]
        assert ev[-1]["datos"]["aprobaciones"] == {"aprobadas": 0, "rechazadas": 1}

        ev = _turno(ws, {"tipo": "mensaje", "texto": "", "documento_id": "demo-boleta"})
        final = next(e for e in ev if e["tipo"] == "respuesta.final")["datos"]["texto"]
        assert "titular" in final and "Morales" in final
