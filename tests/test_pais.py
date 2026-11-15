"""Modo PAIS: cliente OpenAI-compatible (chat con herramientas en SSE y JSON, embeddings), token de Keycloak,
agente de PAIS, varios servidores MCP, guardia de salida con destinos del datacenter y config.pais.yaml."""

import asyncio
import copy
import json

import pytest

from comun import config, salida
from comun import pais as cpais
from orchestrator.mcp_host import HostMCP, HostMCPMultiple
from orchestrator.ollama import observador_actual
from orchestrator.orquestador import Orquestador
from orchestrator.pais import AgentePAIS, PaisLLM, PaisNoDisponible, a_openai, formato_openai
from tests.pais_simulado import PaisFalso, llamada


@pytest.fixture
def cfg_pais(cfg, monkeypatch):
    monkeypatch.setenv("PAIS_TOKEN", "estatico")
    c = copy.deepcopy(cfg)
    c["proveedor"] = "pais"
    c["pais"] = {"url": "https://pais.lab", "verificar_tls": False, "stream": True,
                 "agente": {"id": "ag-1", "nombre": "agente-banco"}}
    for rol in ("agente", "rapido"):
        c["modelos"][rol] = {"nombre": "llama-3.1-8b"}
    c["modelos"]["embeddings"] = {"nombre": "bge-m3"}
    return c


# ───────────── traducción de mensajes ─────────────

def test_a_openai_convierte_herramientas_e_ids():
    mensajes = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "buscar_cliente",
                                                                         "arguments": {"texto": "Ana"}}}]},
        {"role": "tool", "tool_name": "buscar_cliente", "content": "CLI-1"},
    ]
    o = a_openai(mensajes)
    tc = o[2]["tool_calls"][0]
    assert tc["type"] == "function" and json.loads(tc["function"]["arguments"]) == {"texto": "Ana"}
    assert o[3] == {"role": "tool", "tool_call_id": tc["id"], "content": "CLI-1"}
    assert "tool_name" not in o[3]


def test_a_openai_respeta_ids_y_adjunta_imagenes():
    o = a_openai([{"role": "assistant", "content": None, "tool_calls": [{"id": "x1", "function": {
        "name": "a", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "x1", "content": "ok"},
        {"role": "user", "content": "lea", "images": ["QUJD"]}])
    assert o[0]["tool_calls"][0]["id"] == "x1" and o[1]["tool_call_id"] == "x1"
    assert o[2]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,QUJD")


def test_formato_openai():
    assert formato_openai("json") == {"type": "json_object"}
    assert formato_openai({"type": "object"})["json_schema"]["schema"] == {"type": "object"}


# ───────────── chat ─────────────

@pytest.mark.parametrize("stream", [True, False])
async def test_chat_con_herramientas(cfg_pais, stream):
    falso = PaisFalso([{"llamadas": [llamada("perfil_crediticio", filtro="CLI-00119")]}], stream=stream)
    llm = PaisLLM(cfg_pais, transport=falso.transport)
    trozos = []
    r = await llm.chat("agente", [{"role": "user", "content": "¿califica?"}],
                       herramientas=[{"type": "function", "function": {"name": "perfil_crediticio"}}],
                       al_recibir=trozos.append)
    await llm.cerrar()
    assert [(c.nombre, c.argumentos, c.id) for c in r.llamadas] == [("perfil_crediticio", {"filtro": "CLI-00119"},
                                                                     "call_0")]
    enviado = falso.chats()[0]
    assert enviado["model"] == "llama-3.1-8b" and enviado["tools"][0]["function"]["name"] == "perfil_crediticio"
    assert falso.peticiones[0]["auth"] == "Bearer estatico"
    assert r.tokens_entrada in (100, 120)


async def test_chat_streaming_de_texto(cfg_pais):
    falso = PaisFalso([{"contenido": "El dictamen es SUSPENDIDO según NR-005."}])
    llm = PaisLLM(cfg_pais, transport=falso.transport)
    trozos = []
    r = await llm.chat("agente", [{"role": "user", "content": "x"}], al_recibir=trozos.append)
    await llm.cerrar()
    assert r.contenido == "El dictamen es SUSPENDIDO según NR-005." and len(trozos) > 1
    assert r.ttft_ms is not None and r.tokens_salida == 12


async def test_chat_formato_json_va_en_response_format_y_en_el_sistema(cfg_pais):
    falso = PaisFalso([{"contenido": '{"intencion": "fraude"}'}])
    llm = PaisLLM(cfg_pais, transport=falso.transport)
    r = await llm.chat("rapido", [{"role": "system", "content": "Clasifique."}, {"role": "user", "content": "x"}],
                       formato={"type": "object"})
    await llm.cerrar()
    enviado = falso.chats()[0]
    assert enviado["response_format"]["type"] == "json_schema"
    assert enviado["messages"][0]["role"] == "system" and "JSON" in enviado["messages"][0]["content"]
    assert len([m for m in enviado["messages"] if m["role"] == "system"]) == 1
    assert json.loads(r.contenido) == {"intencion": "fraude"}


async def test_token_de_keycloak_se_cachea_y_se_renueva_con_401(cfg_pais, monkeypatch):
    monkeypatch.delenv("PAIS_TOKEN")
    monkeypatch.setenv("PAIS_KC_USUARIO", "app-centinela")
    monkeypatch.setenv("PAIS_KC_CLAVE", "secreta")
    cfg_pais["pais"]["keycloak"] = {"url_token": "https://kc.lab/token", "client_id": "pais"}
    falso = PaisFalso([{"contenido": "uno"}, {"contenido": "dos"}], rechazar_primero=True)
    token = cpais.TokenPAIS(cfg_pais, transport=falso.transport_kc)
    llm = PaisLLM(cfg_pais, transport=falso.transport, token=token)
    r1 = await llm.chat("agente", [{"role": "user", "content": "a"}])
    r2 = await llm.chat("agente", [{"role": "user", "content": "b"}])
    await llm.cerrar()
    assert (r1.contenido, r2.contenido) == ("uno", "dos")
    assert falso.tokens_emitidos == 2  # el primero lo rechazó PAIS (401); el segundo se reusa
    kc = [p for p in falso.peticiones if p["ruta"] == "keycloak"][0]["cuerpo"]
    assert "grant_type=password" in kc and "username=app-centinela" in kc


async def test_sin_credenciales_da_error_claro(cfg_pais, monkeypatch):
    monkeypatch.delenv("PAIS_TOKEN")
    llm = PaisLLM(cfg_pais, transport=PaisFalso().transport)
    with pytest.raises(PaisNoDisponible, match="PAIS_KC_USUARIO"):
        await llm.chat("agente", [{"role": "user", "content": "a"}])
    await llm.cerrar()


async def test_version_lista_modelos(cfg_pais):
    llm = PaisLLM(cfg_pais, transport=PaisFalso().transport)
    assert await llm.version() == "PAIS · llama-3.1-8b, bge-m3"
    await llm.cerrar()


def test_embeddings_en_orden(cfg_pais):
    c = cpais.ClientePAIS(cfg_pais, transport=PaisFalso().transport)
    assert c.embeddings("bge-m3", ["a", "bbb"]) == [[1.0, 1.0, 0.0], [3.0, 1.0, 0.0]]
    c.cerrar()


def test_cliente_embeddings_segun_proveedor(cfg, cfg_pais):
    from comun.ollama_http import ClienteOllama

    assert isinstance(cpais.cliente_embeddings(cfg), ClienteOllama)
    assert isinstance(cpais.cliente_embeddings(cfg_pais), cpais.ClientePAIS)


# ───────────── agente de PAIS ─────────────

async def test_agente_pais_manda_solo_la_ultima_pregunta(cfg_pais):
    falso = PaisFalso()
    ag = AgentePAIS(cfg_pais, transport=falso.transport)
    res = await ag.preguntar([{"role": "system", "content": ""}, {"role": "user", "content": "uno"},
                              {"role": "assistant", "content": "r"}, {"role": "user", "content": "¿CLI-00119?"}], "s1")
    await ag.cerrar()
    p = falso.peticiones[-1]
    assert p["ruta"].endswith("/agents/ag-1/chat/completions")
    assert p["cuerpo"]["messages"] == [{"role": "user", "content": "¿CLI-00119?"}] and "model" not in p["cuerpo"]
    assert res["texto"] == "DICTAMEN: SUSPENDIDO" and ag.sesiones["s1"] == "pais-s1"


# ───────────── turno completo ─────────────

async def _turno(orq, texto, sesion_id="p"):
    s = orq.sesion(sesion_id)
    cola = s.bus.suscribir()
    orq.iniciar_turno(s, texto)
    eventos = []
    while True:
        e = await asyncio.wait_for(cola.get(), 10)
        eventos.append(e)
        if e["tipo"] == "sesion.acumulado":
            break
    await s.tarea
    return eventos


async def test_turno_agente_propio_con_modelos_de_pais(cfg_pais, servidor, db):
    """El loop de Centinela con PAIS: herramienta MCP real (en memoria) y resultado de vuelta en formato OpenAI."""
    cfg_pais["orquestador"]["enrutamiento"] = {"clasificar_intencion": False, "resumen_hablado": False}
    falso = PaisFalso([{"llamadas": [llamada("buscar_cliente", texto="Lucía Cojtí")]},
                       {"contenido": "Encontré a la clienta CLI-0042."}])
    host = HostMCP(servidor)
    orq = Orquestador(cfg_pais, PaisLLM(cfg_pais, transport=falso.transport), host, db=db,
                      agente_pais=AgentePAIS(cfg_pais, transport=falso.transport))
    await host.conectar()
    try:
        eventos = await _turno(orq, "Busque a Lucía Cojtí")
    finally:
        await orq.cerrar()
    final = [e["datos"] for e in eventos if e["tipo"] == "respuesta.final"]
    assert final and final[0]["texto"] == "Encontré a la clienta CLI-0042."
    assert [e["datos"]["nombre"] for e in eventos if e["tipo"] == "herramienta.fin"] == ["buscar_cliente"]
    segundo = falso.chats()[1]["messages"]
    herramienta = [m for m in segundo if m["role"] == "tool"][0]
    assert herramienta["tool_call_id"] == "call_0" and "CLI-0042" in herramienta["content"]


async def test_turno_modo_pais_delega_al_agente(cfg_pais, servidor, db):
    cfg_pais["orquestador"]["enrutamiento"] = {"resumen_hablado": False}  # el clasificador ya se omite en modo PAIS
    falso = PaisFalso(agente_texto="DICTAMEN: SUSPENDIDO. La solicitud queda en revisión.")
    host = HostMCP(servidor)
    orq = Orquestador(cfg_pais, PaisLLM(cfg_pais, transport=falso.transport), host, db=db,
                      agente_pais=AgentePAIS(cfg_pais, transport=falso.transport))
    await host.conectar()
    assert orq.modos == ["propio", "pais"] and orq.datos_sesion_lista()["proveedor"] == "pais"
    assert orq.cambiar_modo(orq.sesion("p"), "pais") and not orq.cambiar_modo(orq.sesion("p"), "otro")
    try:
        eventos = await _turno(orq, "¿El cliente CLI-00119 califica para un préstamo personal de Q50,000?")
    finally:
        await orq.cerrar()
    tipos = [e["tipo"] for e in eventos]
    assert "herramienta.inicio" not in tipos and not falso.chats()
    uso = [e["datos"] for e in eventos if e["tipo"] == "modelo.uso"][0]
    assert uso["modelo"] == "agente-banco (PAIS)"
    fin = [e["datos"] for e in eventos if e["tipo"] == "turno.fin"][0]
    assert fin["ok"] and fin["metricas"]["tokens_entrada"] == 900


async def test_pais_caido_da_mensaje_amable(cfg_pais, servidor, db):
    import httpx

    def caido(_req):
        raise httpx.ConnectError("sin ruta")

    host = HostMCP(servidor)
    orq = Orquestador(cfg_pais, PaisLLM(cfg_pais, transport=httpx.MockTransport(caido)), host, db=db)
    await host.conectar()
    try:
        eventos = await _turno(orq, "hola")
    finally:
        await orq.cerrar()
    err = [e["datos"] for e in eventos if e["tipo"] == "error"][0]
    assert err["codigo"] == "pais_no_disponible" and "PAIS" in err["mensaje"]


# ───────────── varios servidores MCP ─────────────

async def test_host_multiple_filtra_y_enruta(servidor, db, cfg):
    from mcp_server.server import crear_servidor

    otro = crear_servidor(db, cfg)
    a = HostMCP(servidor, permitidas=["buscar_cliente"], nombre="datos", solo_lectura=True)
    b = HostMCP(otro, permitidas=["buscar_politicas", "buscar_cliente"], nombre="politicas")
    multi = HostMCPMultiple([a, b])
    await multi.conectar()
    try:
        assert sorted(multi.herramientas) == ["buscar_cliente", "buscar_politicas"]
        assert multi.herramientas["buscar_cliente"].escritura is False
        assert len(multi.para_ollama()) == 2 and multi.conectado
        r = await multi.llamar("buscar_cliente", {"texto": "Lucía Cojtí"}, {"usuario": "t"}, 10)
        assert r.ok and "CLI-0042" in r.texto
        assert [e["nombre"] for e in multi.estado()] == ["datos", "politicas"]
    finally:
        await multi.cerrar()


def test_crear_host_desde_config(cfg, monkeypatch):
    from orchestrator.mcp_host import ServidorHTTP, crear_host

    monkeypatch.setenv("CLAVE_MCP", "abc")
    c = copy.deepcopy(cfg)
    c["mcp"]["servidores"] = [
        {"nombre": "demo-banco", "transporte": "http", "url": "http://10.2.15.51:8101/mcp",
         "api_key_env": "CLAVE_MCP", "herramientas": ["perfil_crediticio"], "solo_lectura": True},
        {"nombre": "politicas", "transporte": "stdio", "herramientas": ["buscar_politicas"]}]
    h = crear_host(c)
    assert isinstance(h, HostMCPMultiple)
    http = h.hosts[0].objetivo
    assert isinstance(http, ServidorHTTP) and http.encabezados == {"Authorization": "Apikey abc"}
    assert h.hosts[0].permitidas == ["perfil_crediticio"] and h.hosts[1].permitidas == ["buscar_politicas"]


# ───────────── guardia de salida y configuración ─────────────

def test_guardia_permite_el_datacenter_y_bloquea_internet(cfg_pais):
    salida.limpiar_permitidos()
    try:
        cfg_pais["soberania"] = {"destinos_permitidos": ["10.2.15.0/24", "10.2.20.0/24"]}
        cfg_pais["mcp"]["servidores"] = [{"url": "http://10.2.15.51:8101/mcp"}]
        salida.instalar(cfg_pais)
        assert salida.es_permitido("10.2.20.10") and salida.es_permitido("pais.lab")
        assert salida.es_permitido("127.0.0.1") and not salida.es_permitido("8.8.8.8")
        assert not salida.es_permitido("api.openai.com")
        with pytest.raises(salida.ConexionBloqueada):
            salida._hook("socket.getaddrinfo", ("huggingface.co", 443))
        salida._hook("socket.getaddrinfo", ("pais.lab", 443))  # no lanza
    finally:
        salida.desactivar()
        salida.limpiar_permitidos()
        salida.limpiar()


def test_config_pais_se_aplica_con_la_variable(monkeypatch):
    monkeypatch.setenv("CENTINELA_PROVEEDOR", "pais")
    config.cargar.cache_clear()
    try:
        c = config.cargar()
        assert c["proveedor"] == "pais" and c["modelos"]["agente"]["nombre"] == "llama-3.1-8b"
        assert c["modelos"]["embeddings"]["nombre"] == "bge-m3" and c["vision"]["habilitada"] is False
        assert c["guion"][0]["id"] == "credito_fraude" and c["rutas"]["politicas"] == "data/politicas_pais"
        assert [s["nombre"] for s in c["mcp"]["servidores"]] == ["demo-banco", "politicas"]
        assert "resuelto" not in (c.get("seleccion_modelos") or {})
        assert "pais.vcflab.local" in cpais.destinos(c) and "10.2.15.51" in cpais.destinos(c)
    finally:
        monkeypatch.delenv("CENTINELA_PROVEEDOR")
        config.cargar.cache_clear()
    assert config.cargar()["proveedor"] == "ollama"


def test_politicas_pais_se_indexan(tmp_path, cfg_pais):
    from comun.config import RAIZ
    from scripts.ingest import construir

    r = construir(RAIZ / "data" / "politicas_pais", tmp_path / "v.db", cfg_pais,
                  cpais.ClientePAIS(cfg_pais, transport=PaisFalso().transport))
    assert (r["documentos"], r["embeddings"], r["dimensiones"]) == ("4", "1", "3")
    import sqlite3

    con = sqlite3.connect(tmp_path / "v.db")
    codigos = {c for (c,) in con.execute("SELECT DISTINCT codigo FROM fragmentos")}
    con.close()
    assert {"POL-CR-001", "POL-CR-002", "NR-005", "GE-010"} <= codigos


def test_observador_no_queda_puesto():
    assert observador_actual.get() is None


def test_recordatorio_tras_herramientas():
    from orchestrator.pais import a_openai
    msgs = [{"role": "user", "content": "¿CLI-00119 califica?"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {
                "name": "evaluar_solicitud_credito", "arguments": {"cliente": "CLI-00119"}}}]},
            {"role": "tool", "tool_name": "evaluar_solicitud_credito", "tool_call_id": "c1",
             "content": "DICTAMEN: SUSPENDIDO"}]
    sin = a_openai(msgs)
    con = a_openai(msgs, recordatorio=True)
    assert sin[-1]["role"] == "tool"
    assert con[-1]["role"] == "user" and "evaluar_solicitud_credito" in con[-1]["content"]
    # Si la conversación no termina en herramienta, no se agrega nada.
    assert a_openai(msgs[:1], recordatorio=True) == a_openai(msgs[:1])


def test_respuesta_meta():
    from orchestrator.agente import respuesta_meta
    assert respuesta_meta("La respuesta anterior es un dictamen de la herramienta evaluar_solicitud_credito.")
    assert respuesta_meta("La respuesta es el dictamen devuelto por la función evaluar_solicitud_credito.")
    assert not respuesta_meta("DICTAMEN: SUSPENDIDO. El cliente tiene una alerta de fraude abierta (NR-005).")
    assert not respuesta_meta("El cliente califica por Q200,000 a 11.50 %.")
    assert not respuesta_meta("")


def test_memoria_solo_turno_en_curso():
    from orchestrator.memoria import Memoria
    m = Memoria(40)
    for msg in [{"role": "user", "content": "¿CLI-00142 califica?"},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "evaluar"}}]},
                {"role": "tool", "content": "DICTAMEN: APROBADO"},
                {"role": "assistant", "content": "DICTAMEN: APROBADO CON CONDICIONES"},
                {"role": "user", "content": "¿Qué dice la política de fraude?"}]:
        m.agregar(msg)
    assert [x["role"] for x in m.para_llm("S", 0)] == ["system", "user"]
    assert [x["role"] for x in m.para_llm("S", 1)] == ["system", "user", "assistant", "user"]
    assert [x["role"] for x in m.para_llm("S", 5)] == ["system", "user", "assistant", "user"]
    assert len(m.para_llm("S")) == 6


def test_texto_herramienta_politicas():
    import json
    from orchestrator.pais import a_openai, texto_herramienta
    res = json.dumps({"disponible": True, "modo": "hibrida", "motor_vectorial": "sqlite-vec", "fragmentos": [
        {"documento": "Normas de riesgo", "codigo": "NR-005", "seccion": "1", "cita": "Normas de riesgo, sección 1",
         "fragmento": "Con alerta de fraude abierta la solicitud se suspende.", "puntaje": 1.0}]})
    t = texto_herramienta(res)
    assert "NR-005" in t and "se suspende" in t and "motor" not in t
    assert texto_herramienta("DICTAMEN: SUSPENDIDO") == "DICTAMEN: SUSPENDIDO"
    msgs = [{"role": "user", "content": "¿Qué dice la política?"},
            {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "buscar_politicas", "arguments": {}}}]},
            {"role": "tool", "tool_name": "buscar_politicas", "tool_call_id": "c", "content": res}]
    sal = a_openai(msgs, recordatorio=True)
    assert "¿Qué dice la política?" in sal[-1]["content"] and "NR-005" in sal[-2]["content"]


def test_sensibles_demo_banco():
    from orchestrator.soberania.sensibles import Inventario
    inv = Inventario()
    inv.herramienta("evaluar_solicitud_credito", {"cliente": "CLI-00119", "monto": 50000},
                    "DICTAMEN: SUSPENDIDO\nCliente: Andrea Barrios Reyes (CLI-00119), segmento Personal.\n"
                    "Solicitud: préstamo Personal por Q50,000.00.")
    inv.herramienta("alertas_fraude_abiertas", {},
                    "Resultado: 2 fila(s).\n\n| id | fecha | nivel | motivo | estado | cliente | monto | canal |\n"
                    "|---|---|---|---|---|---|---|---|\n"
                    "| 7 | 2026-11-01 | Alto | Ráfaga | Abierta | Andrea Barrios Reyes | 4500.00 | Web |\n"
                    "| 9 | 2026-11-02 | Medio | País | En revisión | Kevin Recinos Reyes | 120.50 | POS |")
    r = {c["id"]: c["cantidad"] for n in inv.resumen()["niveles"] for c in n["categorias"]}
    assert r["clientes"] == 1
    assert r["nombres"] == 2          # el mismo nombre en el dictamen y en la alerta cuenta una vez
    assert r["casos"] == 2
    assert r["montos"] >= 3
