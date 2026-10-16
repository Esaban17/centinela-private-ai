"""Fase 6: revisiones de `make warmup` y `make check-offline`, y el precalentado de la voz en el orquestador."""

import copy

import httpx
import pytest

from comun import config
from orchestrator.ganchos import Ganchos
from orchestrator.mcp_host import HostMCP
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador
from scripts import check_offline, warmup
from tests.ollama_simulado import OllamaFalso

GB = 1024 ** 3
MODELOS = {"agente": "qwen3.6:35b-a3b-q4_K_M", "rapido": "qwen3:4b-instruct-2507-q4_K_M",
           "vision": "qwen3-vl:4b-instruct", "embeddings": "bge-m3"}
SIEMPRE = "2318-08-27T10:11:12.123456-06:00"  # lo que pone Ollama con keep_alive=-1


def estados(chequeos):
    return [c.estado for c in chequeos]


# ───────────── warmup ─────────────

@pytest.mark.parametrize("nombre,nube", [("qwen3.6:35b-a3b-q4_K_M", False), ("bge-m3", False),
                                         ("gpt-oss:120b-cloud", True), ("deepseek-v4:cloud", True),
                                         ("kimi-k3:1t-cloud", True), ("cloudy/modelo:7b", False)])
def test_detecta_modelos_cloud(nombre, nube):
    assert warmup.es_nube(nombre) is nube


def test_modelos_descargados_tolera_latest():
    ch = warmup.revisar_descargados(MODELOS, ["bge-m3:latest", "qwen3.6:35b-a3b-q4_K_M", "qwen3-vl:4b-instruct"])
    assert estados(ch) == ["ok", "falla", "ok", "ok"]
    assert "ollama pull qwen3:4b-instruct-2507-q4_K_M" in ch[1].detalle


def _ps(*modelos):
    return {"models": [{"name": n, "size": int(gb * GB), "size_vram": int(vram * GB), "expires_at": exp}
                       for n, gb, vram, exp in modelos]}


def test_ps_todo_cargado_sin_vencimiento_y_en_presupuesto():
    ps = _ps(("qwen3.6:35b-a3b-q4_K_M", 25, 25, SIEMPRE), ("qwen3:4b-instruct-2507-q4_K_M", 3, 3, SIEMPRE),
             ("qwen3-vl:4b-instruct", 4, 4, SIEMPRE), ("bge-m3:latest", 1.2, 1.2, SIEMPRE))
    ch = warmup.analizar_ps(ps, MODELOS, 35)
    assert estados(ch) == ["ok"] * 5
    assert "33.2 GB de un presupuesto de 35 GB" in ch[-1].detalle


def test_ps_detecta_cpu_vencimiento_faltantes_ajenos_y_exceso():
    ps = _ps(("qwen3.6:35b-a3b-q4_K_M", 30, 24, SIEMPRE), ("qwen3:4b-instruct-2507-q4_K_M", 3, 3,
                                                            "2026-10-16T22:00:00Z"),
             ("bge-m3:latest", 1.2, 1.2, SIEMPRE), ("llama3:8b", 5, 5, SIEMPRE))
    ch = {c.nombre: c for c in warmup.analizar_ps(ps, MODELOS, 35)}
    assert ch["agente cargado"].estado == "aviso" and "80 % en GPU" in ch["agente cargado"].detalle
    assert ch["rapido cargado"].estado == "aviso" and "vence" in ch["rapido cargado"].detalle
    assert ch["vision cargado"].estado == "falla"
    assert ch["embeddings cargado"].estado == "ok"
    assert "llama3:8b" in ch["modelos ajenos a la demo en memoria"].detalle
    assert ch["memoria de los modelos"].estado == "aviso"


def test_archivos_de_la_demo(cfg, tmp_path):
    cfg = copy.deepcopy(cfg)
    cfg["rutas"].update(db=str(tmp_path / "no.db"), vectores=str(tmp_path / "no-vec.db"),
                        documentos_demo=str(tmp_path))
    ch = warmup.revisar_archivos(cfg)
    assert estados(ch) == ["falla", "falla", "falla"]
    assert "make documentos" in ch[2].detalle and "boleta_deposito_discrepancia.png" in ch[2].detalle


def _orquestador_falso(estado: dict, arriba: bool = True) -> httpx.MockTransport:
    def manejar(req: httpx.Request) -> httpx.Response:
        if not arriba:
            raise httpx.ConnectError("apagado", request=req)
        if req.url.path == "/api/salud":
            return httpx.Response(200, json={"ollama": "0.30"})
        assert req.url.path == "/api/precalentar" and req.method == "POST"
        return httpx.Response(200, json=estado)
    return httpx.MockTransport(manejar)


async def test_voz_se_precalienta_en_el_orquestador(cfg):
    ch = await warmup.precalentar_voz(cfg, _orquestador_falso({"stt": "ok · mlx · 900 ms",
                                                               "tts": "ok · kokoro · 300 ms"}))
    assert estados(ch) == ["ok", "ok"] and "en el orquestador" in ch[0].nombre


async def test_voz_avisa_si_habla_el_respaldo_o_falta_whisper(cfg):
    ch = await warmup.precalentar_voz(cfg, _orquestador_falso({"stt": "no disponible", "tts": "ok · say · 80 ms"}))
    assert estados(ch) == ["aviso", "aviso"]
    assert "botones del guion" in ch[0].detalle and "kokoro no cargó" in ch[1].detalle


# ───────────── check-offline ─────────────

def test_config_del_repo_es_local():
    ch = check_offline.revisar_config(config.cargar())
    assert "falla" not in estados(ch)


def test_config_que_sale_del_equipo_falla(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["ollama"]["url"] = "http://10.0.0.20:11434"
    cfg["orquestador"]["host"] = "0.0.0.0"
    cfg["ollama"]["servidor"]["OLLAMA_HOST"] = "0.0.0.0:11434"
    cfg["modelos"]["agente"]["nombre"] = "gpt-oss:120b-cloud"
    cfg["soberania"]["bloquear_salida"] = False
    ch = {c.nombre.split(" =")[0]: c.estado for c in check_offline.revisar_config(cfg)}
    assert ch == {"ollama.url": "falla", "orquestador.host": "falla", "ui.host": "ok",
                  "ollama.servidor.OLLAMA_HOST": "falla", "modelos locales (ningún :cloud)": "falla",
                  "guardia de salida": "aviso"}


def test_variables_offline():
    assert estados(check_offline.revisar_entorno(dict(check_offline.VARIABLES_OFFLINE))) == ["ok"]
    (ch,) = check_offline.revisar_entorno({"HF_HUB_OFFLINE": "1"})
    assert ch.estado == "aviso" and "TRANSFORMERS_OFFLINE=1" in ch.detalle


def test_la_ui_del_repo_no_carga_nada_de_afuera():
    assert check_offline.assets_externos(config.RAIZ / "web") == []
    assert estados(check_offline.revisar_web(config.RAIZ / "web")) == ["ok", "ok"]


def test_detecta_cdn_fuentes_y_urls_relativas_al_protocolo(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="https://cdn.ejemplo.com/vue.js"></script>\n<img src="//img.ejemplo.com/logo.png">\n'
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n<a href="http://127.0.0.1:8765/api/docs">api</a>\n',
        encoding="utf-8")
    (tmp_path / "app.css").write_text("@import url(https://fonts.ejemplo.com/css?family=X);\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("// comentario: no es una URL\nfetch('http://localhost:8765/api/salud');\n"
                                     "new WebSocket('wss://eco.ejemplo.com');\n", encoding="utf-8")
    hallazgos = check_offline.assets_externos(tmp_path)
    assert hallazgos == ["app.css:1 → https://fonts.ejemplo.com/css?family=X", "app.js:3 → wss://eco.ejemplo.com",
                         "index.html:1 → https://cdn.ejemplo.com/vue.js",
                         "index.html:2 → //img.ejemplo.com/logo.png"]
    ch = check_offline.revisar_web(tmp_path)
    assert estados(ch) == ["falla", "aviso"]


def test_red_del_equipo(monkeypatch):
    monkeypatch.setattr(check_offline.red, "estado_red", lambda: {"online": False, "interfaces": []})
    assert estados(check_offline.revisar_red(estricto=True)) == ["ok"]
    monkeypatch.setattr(check_offline.red, "estado_red",
                        lambda: {"online": True, "interfaces": [{"interfaz": "en0", "ips": ["192.168.1.5"]}]})
    assert estados(check_offline.revisar_red(estricto=False)) == ["aviso"]
    (ch,) = check_offline.revisar_red(estricto=True)
    assert ch.estado == "falla" and "en0" in ch.detalle


def _monitor(conexiones: dict | None) -> httpx.MockTransport:
    def manejar(req: httpx.Request) -> httpx.Response:
        if conexiones is None:
            raise httpx.ConnectError("apagado", request=req)
        return httpx.Response(200, json=conexiones)
    return httpx.MockTransport(manejar)


async def test_monitor_del_orquestador(cfg):
    limpio = {"salientes": 0, "max_sesion": 0, "detalle": [], "procesos": [{"pid": 1}, {"pid": 2}], "historial": []}
    assert estados(await check_offline.revisar_orquestador(cfg, _monitor(limpio))) == ["ok"]
    pico = {**limpio, "max_sesion": 1, "historial": [{"hora": "10:00:00", "proceso": "ollama",
                                                      "remoto": "34.1.2.3:443"}]}
    (ch,) = await check_offline.revisar_orquestador(cfg, _monitor(pico))
    assert ch.estado == "falla" and "ollama → 34.1.2.3:443" in ch.detalle
    assert estados(await check_offline.revisar_orquestador(cfg, _monitor(None))) == ["aviso"]


# ───────────── precalentado en el orquestador ─────────────

class VozFalsa:
    disponible = True

    def __init__(self, motor, falla=False):
        self.motor, self.falla, self.llamadas = motor, falla, 0

    async def precalentar(self):
        self.llamadas += 1
        if self.falla:
            raise RuntimeError("sin pesos")


async def test_orquestador_precalienta_su_voz(servidor, cfg, db):
    stt, tts = VozFalsa("mlx"), VozFalsa("kokoro", falla=True)
    g = Ganchos.vacios()
    g.transcriptor, g.sintetizador = stt, tts
    orq = Orquestador(cfg, Ollama(cfg, transport=OllamaFalso().transport), HostMCP(servidor), g, db=db)
    estado = await orq.precalentar()
    assert estado["stt"].startswith("ok · mlx · ") and estado["tts"] == "error: RuntimeError: sin pesos"
    assert (await orq.salud())["precalentado"] == estado
    orq.ganchos = Ganchos.vacios()
    assert await orq.precalentar() == {"stt": "no disponible", "tts": "no disponible"}
    await orq.cerrar()


def test_api_precalienta_al_arrancar_y_a_pedido(servidor, cfg, db):
    from fastapi.testclient import TestClient

    from orchestrator.app import crear_app

    stt, tts = VozFalsa("mlx"), VozFalsa("kokoro")
    g = Ganchos.vacios()
    g.transcriptor, g.sintetizador = stt, tts
    orq = Orquestador(cfg, Ollama(cfg, transport=OllamaFalso().transport), HostMCP(servidor), g, db=db)
    with TestClient(crear_app(orq)) as c:
        r = c.post("/api/precalentar").json()
        assert set(r) == {"stt", "tts"} and all(v.startswith("ok") for v in r.values())
        assert c.get("/api/salud").json()["precalentado"] == r
    assert stt.llamadas == tts.llamadas == 2  # al arrancar y a pedido
