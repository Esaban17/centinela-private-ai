"""Panel de Soberanía: monitor de conexiones, red, Ollama, costo y rutas /api/soberania."""

import json
import socket
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.soberania import MonitorSoberania, conexiones, costos, crear_router, ollama, red


# ───────────── Conexiones ─────────────

@pytest.mark.parametrize("ip,esperado", [
    ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), ("::ffff:127.0.0.1", True), ("::1%lo0", True),
    ("10.0.0.5", False), ("192.168.1.10", False), ("8.8.8.8", False), ("2001:db8::1", False), ("basura", False),
])
def test_es_loopback(ip, esperado):
    assert conexiones.es_loopback(ip) is esperado


def test_detecta_saliente_y_omite_loopback():
    # Servidor y cliente TCP en loopback: no cuentan.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    local = socket.create_connection(srv.getsockname())
    # UDP "conectado" a una IP no loopback: aparece con dirección remota sin enviar ningún paquete.
    saliente = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        saliente.connect(("10.255.255.1", 9))
    except OSError:
        pytest.skip("El entorno no tiene ruta para conectar un socket UDP de prueba")
    try:
        res = conexiones.revisar([])
        remotos = [c.remoto for c in res.salientes]
        assert "10.255.255.1:9" in remotos
        assert all(not r.startswith("127.") for r in remotos)
        assert res.loopback >= 1
        assert any(p["pid"] for p in res.procesos)
    finally:
        saliente.close()
        local.close()
        srv.close()
    assert "10.255.255.1:9" not in [c.remoto for c in conexiones.revisar([]).salientes]


def test_monitor_recuerda_el_pico(monkeypatch):
    falsa = conexiones.ResultadoConexiones(salientes=[conexiones.Conexion(1, "ollama", "a", "1.2.3.4:443", "ESTABLISHED", "tcp")])
    monkeypatch.setattr(conexiones, "revisar", lambda *a: falsa)
    monkeypatch.setattr(ollama, "modelos_cargados", lambda url: {"disponible": False, "modelos": []})
    m = MonitorSoberania({"costos": {}})
    assert m.instantanea()["conexiones"]["salientes"] == 1
    monkeypatch.setattr(conexiones, "revisar", lambda *a: conexiones.ResultadoConexiones())
    s = m.instantanea()["conexiones"]
    assert s["salientes"] == 0 and s["max_sesion"] == 1 and s["historial"][0]["remoto"] == "1.2.3.4:443"


# ───────────── Red ─────────────

Dir = namedtuple("Dir", "family address")
Est = namedtuple("Est", "isup")


def _red(monkeypatch, addrs, stats):
    monkeypatch.setattr(red.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(red.psutil, "net_if_stats", lambda: stats)
    return red.estado_red()


def test_sin_red_si_solo_hay_loopback_y_virtuales(monkeypatch):
    addrs = {"lo0": [Dir(socket.AF_INET, "127.0.0.1")], "utun3": [Dir(socket.AF_INET6, "fd00::1")],
             "en0": [Dir(socket.AF_INET6, "fe80::1%en0")]}  # Wi-Fi apagado: solo link-local
    stats = {k: Est(True) for k in addrs}
    assert _red(monkeypatch, addrs, stats)["online"] is False


def test_en_linea_con_ip_en_interfaz_activa(monkeypatch):
    addrs = {"lo0": [Dir(socket.AF_INET, "127.0.0.1")], "en0": [Dir(socket.AF_INET, "192.168.0.20")]}
    assert _red(monkeypatch, addrs, {"lo0": Est(True), "en0": Est(True)})["online"] is True
    assert _red(monkeypatch, addrs, {"lo0": Est(True), "en0": Est(False)})["online"] is False


# ───────────── Ollama ─────────────

@pytest.fixture
def ollama_falso():
    respuesta = {"models": [{"name": "qwen3:4b-instruct-2507-q4_K_M", "size": 3 * 1024 ** 3, "size_vram": 3 * 1024 ** 3,
                             "details": {"parameter_size": "4B", "quantization_level": "Q4_K_M"}, "context_length": 4096}]}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            cuerpo = json.dumps(respuesta).encode()
            self.send_response(200 if self.path == "/api/ps" else 404)
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ollama_ps(ollama_falso):
    r = ollama.modelos_cargados(ollama_falso)
    assert r["disponible"] and r["memoria_total_gb"] == 3.0
    assert r["modelos"][0]["gpu_pct"] == 100 and r["modelos"][0]["nombre"].startswith("qwen3")


def test_ollama_no_disponible_y_no_local():
    libre = socket.socket()
    libre.bind(("127.0.0.1", 0))
    puerto = libre.getsockname()[1]
    libre.close()
    assert ollama.modelos_cargados(f"http://127.0.0.1:{puerto}", timeout=0.5)["disponible"] is False
    remoto = ollama.modelos_cargados("http://ollama.ejemplo.com:11434")
    assert remoto["disponible"] is False and "no es local" in remoto["error"]


# ───────────── Costo ─────────────

def test_costo_sin_precios_no_inventa():
    r = costos.costo_equivalente(10_000, 500, {"moneda": "USD", "precio_entrada_millon": None})
    assert r["configurado"] is False and r["monto"] is None and r["costo_local_gtq"] == 0.0
    assert r["rotulo"] == "Estimación referencial"


def test_costo_con_precios_y_tipo_de_cambio():
    r = costos.costo_equivalente(2_000_000, 100_000, {"moneda": "USD", "tipo_cambio_gtq": 7.7,
                                                      "precio_entrada_millon": 2, "precio_salida_millon": 10})
    assert r["monto"] == pytest.approx(5.0) and r["monto_gtq"] == pytest.approx(38.5)


# ───────────── Rutas ─────────────

def test_rutas_soberania(cfg, ollama_falso):
    cfg["ollama"]["url"] = ollama_falso
    cfg["costos"].update(precio_entrada_millon=1, precio_salida_millon=1)
    app = FastAPI()
    app.include_router(crear_router(cfg))
    c = TestClient(app)
    s = c.get("/api/soberania", params={"tokens_entrada": 500_000, "tokens_salida": 500_000}).json()
    assert {"conexiones", "red", "ollama", "sistema", "costo"} <= set(s)
    assert s["costo"]["monto"] == pytest.approx(1.0) and s["ollama"]["disponible"]
    assert "salientes" in c.get("/api/soberania/conexiones").json()
    assert c.post("/api/soberania/reiniciar").json() == {"ok": True}
    assert c.get("/api/soberania", params={"tokens_entrada": -1}).status_code == 422
