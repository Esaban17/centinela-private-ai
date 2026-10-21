"""Selección de modelos por RAM: perfiles, estimación de memoria, alerta y rutas /api/modelos."""

import copy

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from comun import config
from comun import modelos as sm
from orchestrator import modelos_api

GPU_HOLGADA = (100.0, "prueba")


@pytest.fixture
def cfg_archivos() -> dict:
    """config.yaml tal cual, antes de aplicar perfil (como lo ve `config.cargar()` por dentro)."""
    return yaml.safe_load((config.RAIZ / "config.yaml").read_text(encoding="utf-8"))


def test_auto_elige_perfil_por_ram(cfg_archivos):
    for ram, perfil, agente in ((48, "48gb", "qwen3.6:35b-a3b-q4_K_M"), (64, "48gb", "qwen3.6:35b-a3b-q4_K_M"),
                                (24, "24gb", "gemma4:12b-it-qat"), (36, "24gb", "gemma4:12b-it-qat")):
        c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), ram)
        assert c["seleccion_modelos"]["resuelto"]["perfil"] == perfil, ram
        assert c["modelos"]["agente"]["nombre"] == agente
        assert c["modelos"]["stt"] == cfg_archivos["modelos"]["stt"]  # la voz no la toca el perfil


def test_perfil_24gb_comparte_agente_y_vision_con_mismo_contexto(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 24)
    assert c["modelos"]["vision"]["nombre"] == c["modelos"]["agente"]["nombre"]
    assert c["modelos"]["vision"]["num_ctx"] == c["modelos"]["agente"]["num_ctx"]
    assert c["modelos"]["embeddings"]["nombre"] == "bge-m3"  # mismo índice de políticas en ambos perfiles


def test_perfil_manual_y_desconocido(cfg_archivos):
    c = copy.deepcopy(cfg_archivos)
    c["seleccion_modelos"]["perfil"] = "manual"
    c["seleccion_modelos"]["manual"] = {"rapido": {"nombre": "qwen3.5:2b"}}
    sm.aplicar_perfil(c, 24)
    assert c["modelos"]["rapido"]["nombre"] == "qwen3.5:2b"
    assert c["modelos"]["agente"]["nombre"] == "qwen3.6:35b-a3b-q4_K_M"
    c = copy.deepcopy(cfg_archivos)
    c["seleccion_modelos"]["perfil"] = "8gb"
    sm.aplicar_perfil(c, 24)
    assert c["seleccion_modelos"]["resuelto"]["perfil"] == "manual" and "no existe" in c["seleccion_modelos"]["resuelto"]["motivo"]


def test_cambiar_de_perfil_no_arrastra_el_anterior(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 24)
    m = sm.modelos_de_perfil(c, "48gb")
    assert m["agente"]["nombre"] == "qwen3.6:35b-a3b-q4_K_M" and "think" in m["agente"]
    assert m["vision"]["nombre"] == "qwen3-vl:4b-instruct" and m["vision"]["num_ctx"] == 8192


def test_entorno_le_gana_al_perfil(monkeypatch):
    monkeypatch.setenv("CENTINELA_RAM_GB", "24")
    monkeypatch.setenv("CENTINELA__seleccion_modelos__perfil", "auto")
    monkeypatch.setenv("CENTINELA__modelos__agente__nombre", "gemma4:26b-a4b-it-qat")
    config.cargar.cache_clear()
    try:
        c = config.cargar()
        assert c["seleccion_modelos"]["resuelto"]["perfil"] == "24gb"
        assert c["modelos"]["agente"]["nombre"] == "gemma4:26b-a4b-it-qat"
        assert c["modelos"]["rapido"]["nombre"] == "qwen3:4b-instruct-2507-q4_K_M"
    finally:
        config.cargar.cache_clear()


def test_config_local_pisa_a_config_yaml(tmp_path, monkeypatch):
    local = tmp_path / "config.local.yaml"
    monkeypatch.setenv("CENTINELA_CONFIG_LOCAL", str(local))
    monkeypatch.delenv("CENTINELA__seleccion_modelos__perfil", raising=False)
    config.guardar_local({"seleccion_modelos": {"perfil": "24gb"}})
    config.guardar_local({"ollama": {"keep_alive": "10m"}})  # se fusiona, no reemplaza
    config.cargar.cache_clear()
    try:
        c = config.cargar()
        assert c["seleccion_modelos"]["resuelto"]["perfil"] == "24gb"
        assert c["ollama"]["keep_alive"] == "10m" and c["ollama"]["url"] == "http://127.0.0.1:11434"
        assert local.read_text(encoding="utf-8").startswith("# Escrito por Centinela")
    finally:
        config.cargar.cache_clear()


# ───────────── estimación ─────────────

def test_estimacion_48gb_en_48_cabe_y_en_24_excede(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 48)
    ok = sm.estimar(c["modelos"], c, 48, limite_gpu=GPU_HOLGADA)
    assert ok["nivel"] == "ok" and 30 < ok["total_gb"] < 40, ok
    mal = sm.estimar(c["modelos"], c, 24, limite_gpu=GPU_HOLGADA)
    assert mal["nivel"] == "excede" and "no cabe" in mal["mensaje"]


def test_estimacion_24gb_en_24_cabe_y_cuenta_una_vez_el_modelo_compartido(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 24)
    est = sm.estimar(c["modelos"], c, 24, limite_gpu=GPU_HOLGADA)
    assert est["nivel"] == "ok", est
    vision = next(f for f in est["filas"] if f["rol"] == "vision")
    assert vision["gb"] == 0 and vision["comparte_con"] == "agente"
    assert est["total_gb"] == round(est["modelos_gb"] + est["reserva_gb"], 1)


def test_estimacion_justo_avisos_y_desconocidos(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 48)
    assert sm.estimar(c["modelos"], c, 40, limite_gpu=GPU_HOLGADA)["nivel"] == "justo"

    m = copy.deepcopy(c["modelos"])
    m["rapido"] = {"nombre": "kimi-k3:cloud"}
    m["vision"] = {"nombre": "qwen3.6:35b-a3b-q4_K_M", "num_ctx": 8192}   # mismo modelo que el agente, otro contexto
    m["embeddings"] = {"nombre": "mi-embed:1b"}
    est = sm.estimar(m, c, 48, tamanos={"mi-embed:1b": 1.0}, limite_gpu=GPU_HOLGADA)
    texto = " ".join(est["avisos"])
    assert ":cloud" in texto and "num_ctx distinto" in texto
    emb = next(f for f in est["filas"] if f["rol"] == "embeddings")
    assert emb["fuente"] == "ollama" and emb["gb"] > 1.0
    est = sm.estimar({"agente": {"nombre": "nadie:1b"}}, c, 48, limite_gpu=GPU_HOLGADA)
    assert est["filas"][0]["gb"] is None and "no hay dato" in est["avisos"][0]


def test_contexto_mayor_suma_cache_kv(cfg_archivos):
    cat = cfg_archivos["seleccion_modelos"]["catalogo"]
    base, _ = sm.memoria_modelo("qwen3.6:35b-a3b-q4_K_M", 16384, cat)
    doble, _ = sm.memoria_modelo("qwen3.6:35b-a3b-q4_K_M", 32768, cat)
    assert base == 21.4 and doble > base


def test_aviso_de_gpu(cfg_archivos, monkeypatch):
    monkeypatch.setenv("CENTINELA_RAM_GB", "24")
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 24)
    est = sm.estimar(c["modelos"], c, 24, limite_gpu=(8.0, "prueba"))
    assert any("GPU" in a for a in est["avisos"])
    assert sm.limite_gpu_gb(24) == (16.0, "valor por omisión de macOS")
    assert sm.limite_gpu_gb(48)[0] == 36.0


def test_opciones_por_rol(cfg_archivos):
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 48)
    op = sm.opciones_por_rol(c, ["bge-m3:latest", "qwen3.6:35b-a3b-q4_K_M", "llama3:8b", "algo:cloud", "otro-embed:1b"])
    agentes = [o["nombre"] for o in op["agente"]]
    assert "gemma4:12b-it-qat" in agentes and "llama3:8b" in agentes and "algo:cloud" not in agentes
    assert "otro-embed:1b" in [o["nombre"] for o in op["embeddings"]] and "otro-embed:1b" not in agentes
    assert next(o for o in op["embeddings"] if o["nombre"] == "bge-m3")["descargado"] is True
    assert next(o for o in op["agente"] if o["nombre"] == "gemma4:12b-it-qat")["descargado"] is False


# ───────────── /api/modelos ─────────────

class ClienteFalso:
    def __init__(self, descargados):
        self.tags = descargados
        self.liberados: list[str] = []
        self.precargados: list[tuple[str, int | None]] = []

    async def descargados(self):
        return None if self.tags is None else {sm.con_etiqueta(n): 5.0 for n in self.tags}

    async def liberar(self, nombre):
        self.liberados.append(nombre)

    async def precargar(self, nombre, num_ctx, keep_alive):
        self.precargados.append((nombre, num_ctx))


TODOS = ["qwen3.6:35b-a3b-q4_K_M", "qwen3:4b-instruct-2507-q4_K_M", "qwen3-vl:4b-instruct", "bge-m3",
         "gemma4:12b-it-qat", "gemma4:26b-a4b-it-qat"]


@pytest.fixture
def api(cfg_archivos, tmp_path, monkeypatch):
    monkeypatch.setenv("CENTINELA_CONFIG_LOCAL", str(tmp_path / "config.local.yaml"))
    c = sm.aplicar_perfil(copy.deepcopy(cfg_archivos), 24)
    estado = {"ocupado": False, "anuncios": 0}

    def armar(descargados=TODOS):
        falso = ClienteFalso(descargados)
        app = FastAPI()
        app.include_router(modelos_api.crear_router(c, lambda: estado["ocupado"],
                                                    lambda: estado.__setitem__("anuncios", estado["anuncios"] + 1), falso))
        return TestClient(app), falso

    return c, estado, armar, tmp_path / "config.local.yaml"


def test_api_lista_perfiles_y_estimacion(api):
    c, _, armar, _ = api
    cli, _ = armar()
    d = cli.get("/api/modelos").json()
    assert d["perfil"] == "24gb" and d["ollama_disponible"] is True
    assert {p["id"] for p in d["perfiles"]} == {"48gb", "24gb"}
    p48 = next(p for p in d["perfiles"] if p["id"] == "48gb")
    assert p48["estimacion"]["nivel"] == "excede"  # en 24 GB el perfil grande no cabe
    assert d["estimacion"]["nivel"] == "ok" and d["seleccion"]["agente"] == "gemma4:12b-it-qat"
    est = cli.post("/api/modelos/estimar", json={"perfil": "24gb", "modelos": {"agente": "gemma4:26b-a4b-it-qat"}}).json()
    assert est["nivel"] in {"justo", "excede"} and est["total_gb"] > d["estimacion"]["total_gb"]


def test_api_aplica_en_caliente_y_guarda(api):
    c, estado, armar, local = api
    cli, falso = armar()
    r = cli.post("/api/modelos", json={"perfil": "24gb", "modelos": {"rapido": "qwen3-vl:4b-instruct"}})
    assert r.status_code == 200, r.text
    assert r.json()["perfil"] == "manual"
    assert c["modelos"]["rapido"]["nombre"] == "qwen3-vl:4b-instruct"
    assert falso.liberados == ["qwen3:4b-instruct-2507-q4_K_M"]
    assert falso.precargados == [("qwen3-vl:4b-instruct", 4096)]
    assert estado["anuncios"] == 1
    guardado = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert guardado["seleccion_modelos"]["perfil"] == "manual"
    assert guardado["seleccion_modelos"]["manual"]["rapido"]["nombre"] == "qwen3-vl:4b-instruct"


def test_api_volver_a_un_perfil_guarda_solo_el_perfil(api):
    c, _, armar, local = api
    cli, _ = armar()
    assert cli.post("/api/modelos", json={"perfil": "24gb", "modelos": {}}).json()["perfil"] == "24gb"
    assert yaml.safe_load(local.read_text(encoding="utf-8")) == {"seleccion_modelos": {"perfil": "24gb"}}


def test_api_rechaza_excede_sin_forzar_y_nube_y_no_descargado(api):
    c, estado, armar, _ = api
    cli, _ = armar()
    r = cli.post("/api/modelos", json={"perfil": "48gb"})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "excede"
    assert c["modelos"]["agente"]["nombre"] == "gemma4:12b-it-qat"  # no cambió nada
    assert cli.post("/api/modelos", json={"perfil": "48gb", "forzar": True}).status_code == 200
    assert c["modelos"]["agente"]["nombre"] == "qwen3.6:35b-a3b-q4_K_M"

    r = cli.post("/api/modelos", json={"perfil": "manual", "modelos": {"agente": "kimi-k3:cloud"}})
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "nube"
    cli2, _ = armar(["bge-m3"])
    r = cli2.post("/api/modelos", json={"perfil": "24gb"})
    assert r.status_code == 409 and "ollama pull gemma4:12b-it-qat" in r.json()["detail"]["mensaje"]
    estado["ocupado"] = True
    r = cli.post("/api/modelos", json={"perfil": "24gb"})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "turno_en_curso"


def test_api_embeddings_pide_reinicio(api):
    c, _, armar, _ = api
    cli, _ = armar(TODOS + ["qwen3-embedding:0.6b"])
    r = cli.post("/api/modelos", json={"perfil": "24gb", "modelos": {"embeddings": "qwen3-embedding:0.6b"}}).json()
    assert r["pendiente_reinicio"] and "make ingest" in r["pendiente_reinicio"][0]
    assert c["modelos"]["embeddings"]["nombre"] == "bge-m3"  # el servidor MCP sigue con el índice actual


def test_vision_lee_el_modelo_vigente(cfg):
    from orchestrator.vision import crear_vision

    class OllamaMin:
        async def chat(self, rol, mensajes, *, imagenes=None, formato=None, opciones=None):
            return ""

    v = crear_vision(cfg, OllamaMin())
    cfg["modelos"]["vision"]["nombre"] = "gemma4:12b-it-qat"
    assert v.modelo == "gemma4:12b-it-qat"


def test_ui_tiene_selector_de_modelos():
    html = (config.RAIZ / "web" / "index.html").read_text(encoding="utf-8")
    js = (config.RAIZ / "web" / "js" / "modelos.js").read_text(encoding="utf-8")
    for id_ in ("estado-ram", "aviso-ram", "dialogo-modelos", "dm-perfil", "dm-roles", "dm-alerta", "dm-aplicar"):
        assert f'id="{id_}"' in html and f"'{id_}'" in js, id_
