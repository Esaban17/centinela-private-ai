"""Fase 6: el guion corrido de punta a punta (warmup, ensayo, medir, check-offline) con Ollama simulado y el
servidor MCP real en memoria; evaluación de cada paso, auditoría y criterios de aceptación."""

import pytest

from comun import config
from orchestrator.ganchos import Extraccion
from orchestrator.mcp_host import HostMCP
from orchestrator.ollama import Ollama
from scripts import ensayo
from scripts.guion import (ResultadoPaso, auditoria_desde, correr_guion, crear_orquestador, evaluar, percentil,
                           revisar_auditoria, sin_tildes, ultimo_id_auditoria)
from tests.ollama_simulado import OllamaFalso, llamar

AGENTE = "qwen3.6:35b-a3b-q4_K_M"


class VisionFalsa:
    disponible = True

    def __init__(self, campos):
        self.campos = campos

    async def extraer(self, imagen, nombre):
        return Extraccion("boleta_deposito", dict(self.campos), "qwen3-vl:4b-instruct")


@pytest.fixture(scope="module")
def manifiesto(db_plantilla, tmp_path_factory):
    from scripts.gen_documentos import generar

    return generar(db_plantilla, tmp_path_factory.mktemp("docs"), config.cargar())


@pytest.fixture
async def orquestador(servidor, cfg, db):
    creados = []

    async def _crear(falso, vision=None):
        orq = crear_orquestador(cfg, db, vision=False, ollama=Ollama(cfg, transport=falso.transport),
                                host=HostMCP(servidor))
        if vision is not None:
            orq.ganchos.vision = vision
        await orq.host.conectar()
        creados.append(orq)
        return orq

    yield _crear
    for o in creados:
        await o.cerrar()


def final(texto):
    return {"contenido": texto}


async def test_guion_completo_exitoso_y_auditado(orquestador, cfg, db, manifiesto):
    from scripts.ingest import construir

    construir(config.ruta("politicas"), config.ruta("vectores", cfg), cfg, con_embeddings=False)  # solo léxica
    campos = manifiesto["documentos"]["boleta_deposito_discrepancia.png"]["campos"]
    falso = OllamaFalso([
        {"llamadas": [llamar("buscar_cliente", texto="Lucía Cojtí")]},
        {"llamadas": [llamar("resumen_cliente", cliente_id="CLI-0042")]},
        final("La clienta tiene tres productos."),
        {"llamadas": [llamar("crear_ticket", tipo="fraude", prioridad="alta", titulo="t", detalle="Anomalías",
                             cliente_id="CLI-0042")]},
        final("Ticket creado."),
        {"llamadas": [llamar("buscar_politicas", consulta="bloqueo preventivo de tarjetas")]},
        final("La política de prevención de fraude, sección de bloqueo, lo permite."),
        {"llamadas": [llamar("validar_documento", tipo_documento="boleta_deposito", campos=campos)]},
        final("La cuenta existe, pero el TITULAR no coincide."),
    ])
    orq = await orquestador(falso, VisionFalsa(campos))
    doc_id = orq.documentos.guardar("boleta.png", b"png simulado")
    pasos = [
        {"id": "cartera", "pregunta": "¿Cómo está la cartera de Lucía?",
         "herramientas_esperadas": ["buscar_cliente", "resumen_cliente"]},
        {"id": "ticket", "pregunta": "Abra un ticket", "herramientas_esperadas": ["crear_ticket"]},
        {"id": "politica", "pregunta": "¿Qué dice la política?", "herramientas_esperadas": ["buscar_politicas"]},
        {"id": "documento", "pregunta": "Valide esta boleta", "documento_id": doc_id,
         "herramientas_esperadas": ["validar_documento"], "respuesta_contiene": ["titular"]},
    ]
    desde = ultimo_id_auditoria(db)
    vistos = []
    res = await correr_guion(orq, pasos, al_terminar_paso=vistos.append)

    assert [r.id for r in res] == [r.id for r in vistos] == ["cartera", "ticket", "politica", "documento"]
    assert all(r.ok for r in res), [r.problemas for r in res]
    assert res[2].citas > 0 and res[3].herramientas == ["validar_documento"]
    assert res[0].total_ms > 0 and res[0].tokens_entrada > 0 and AGENTE in res[0].modelos
    filas = auditoria_desde(db, desde)
    assert [f["herramienta"] for f in filas] == ["buscar_cliente", "resumen_cliente", "crear_ticket",
                                                 "buscar_politicas", "validar_documento"]
    assert filas[2]["estado"] == "ok"  # el ticket se aprobó (como en el escenario)
    assert revisar_auditoria(res, filas) == []


async def test_paso_falla_si_no_usa_la_herramienta_o_no_dice_lo_esperado(orquestador):
    falso = OllamaFalso([final("Todo está tranquilo."),
                         {"llamadas": [llamar("alertas_infraestructura", severidad="critica")]},
                         final("Hay dos alertas críticas en almacenamiento.")])
    orq = await orquestador(falso)
    res = await correr_guion(orq, [
        {"id": "fraude", "pregunta": "¿Movimientos raros?", "herramientas_esperadas": ["detectar_anomalias"]},
        {"id": "noc", "pregunta": "¿Alertas?", "herramientas_esperadas": ["alertas_infraestructura"],
         "respuesta_contiene": ["VKS", "NSX"]}])
    assert res[0].problemas == ["no usó detectar_anomalias"]
    assert res[1].problemas == ["la respuesta no menciona VKS / NSX"]
    assert not any(r.ok for r in res)


async def test_error_del_turno_es_un_problema_con_su_codigo(orquestador):
    falso = OllamaFalso()
    falso.caido = True
    orq = await orquestador(falso)
    (r,) = await correr_guion(orq, [{"id": "cartera", "pregunta": "Hola", "herramientas_esperadas": []}])
    assert not r.ok and r.problemas[0].startswith("error ollama_no_disponible")


def test_evaluar_y_utilidades():
    eventos = [{"tipo": "herramienta.fin", "datos": {"nombre": "buscar_politicas", "ok": True}},
               {"tipo": "respuesta.final", "datos": {"texto": "Según la política…"}},
               {"tipo": "turno.fin", "datos": {"ok": True, "metricas": {"herramientas": ["buscar_politicas"]}}}]
    assert evaluar({"herramientas_esperadas": ["buscar_politicas"]}, eventos) == ["sin citas de política"]
    assert evaluar({}, []) == ["el turno no terminó"]
    assert sin_tildes("Política ÑANDÚ") == "politica nandu"
    assert percentil([], .5) == 0 and percentil([5], .95) == 5
    assert percentil(list(range(1, 11)), .5) == 5 and percentil(list(range(1, 11)), .95) == 10
    assert percentil([1, None, 3], .95) == 3
    r = [ResultadoPaso("a", True, herramientas=["buscar_cliente"])]
    assert revisar_auditoria(r, []) == ["1 llamadas a herramientas y 0 filas en auditoría"]
    assert revisar_auditoria(r, [{"herramienta": "buscar_cliente", "modelo": "sin modelo (llamada directa)"}]) == [
        "hay filas de auditoría sin el modelo que pidió la herramienta"]


def _resumen(tasa=1.0, audio=None):
    return [{"id": "cartera", "tasa": tasa, "primer_audio_p50_ms": audio, "primer_audio_p95_ms": audio}]


def test_criterios_de_aceptacion():
    cfg = {"ensayo": {"exito_minimo": 0.9, "primer_audio_max_ms": 5000}}
    ok = ensayo.criterios(cfg, _resumen(), 0, 0, [], voz=False)
    assert [e for e, *_ in ok] == ["ok", "ok", "ok"]
    malo = ensayo.criterios(cfg, _resumen(0.8), 2, 1, ["2 llamadas a herramientas y 1 filas en auditoría"], False)
    assert [e for e, *_ in malo] == ["falla"] * 4
    assert "cartera 80%" in malo[0][2]
    voz = ensayo.criterios(cfg, _resumen(audio=6200), 0, 0, [], voz=True)
    assert voz[-1][0] == "aviso" and "6.2 s" in voz[-1][2]
    assert ensayo.criterios(cfg, _resumen(audio=3100), 0, 0, [], voz=True)[-1][0] == "ok"


def test_resumen_por_pregunta():
    filas = [ResultadoPaso("noc", True, total_ms=1000, ttft_ms=300, primer_audio_ms=2000, stt_ms=400),
             ResultadoPaso("noc", False, ["no usó alertas_infraestructura"], total_ms=3000, ttft_ms=500,
                           primer_audio_ms=4000, stt_ms=600)]
    (r,) = ensayo.resumir([{"id": "noc"}], filas)
    assert (r["exitos"], r["corridas"], r["tasa"]) == (1, 2, 0.5)
    assert r["total_p95_ms"] == 3000 and r["primer_audio_p95_ms"] == 4000 and r["stt_p50_ms"] == 400
    assert r["problemas"] == [("no usó alertas_infraestructura", 1)]


def test_guion_de_config_cubre_los_seis_actos():
    """Las preguntas del guion de config.yaml usan herramientas que existen y el acto de visión trae su documento."""
    guion = config.cargar()["guion"]
    assert [p["id"] for p in guion] == ["cartera", "fraude", "ticket", "politica", "documento", "noc"]
    herramientas = {"buscar_cliente", "resumen_cliente", "movimientos", "detectar_anomalias", "consulta_sql",
                    "buscar_politicas", "alertas_infraestructura", "crear_ticket", "actualizar_ticket",
                    "validar_documento"}
    assert all(set(p["herramientas_esperadas"]) <= herramientas for p in guion)
    doc = next(p for p in guion if p["id"] == "documento")
    assert doc["documento_id"] == "demo-boleta_deposito_discrepancia.png"


def test_documento_del_guion_lo_genera_make_documentos(manifiesto):
    doc = next(p for p in config.cargar()["guion"] if p["id"] == "documento")
    assert doc["documento_id"].removeprefix("demo-") in manifiesto["documentos"]
