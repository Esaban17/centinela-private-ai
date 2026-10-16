"""Visión documental: esquemas, generador de documentos demo, extracción (Ollama simulado) y validación cruzada."""

import io
import json
import sqlite3

import pytest
from mcp.client.client import Client
from PIL import Image

from documentos.extraccion import ErrorExtraccion, extraer, llamador_ollama
from documentos.modelos import normalizar_monto, validar_campos
from documentos.validacion import comparar_nombres, validar
from mcp_server.db import conectar_lectura
from orchestrator.vision import crear_vision
from scripts.gen_documentos import NOMBRE_FALSO_0107, generar


@pytest.fixture(scope="session")
def demo(db_plantilla, tmp_path_factory):
    """Documentos generados una vez desde la base plantilla (cada `db` de prueba es copia de ella)."""
    from comun import config

    carpeta = tmp_path_factory.mktemp("documentos_demo")
    return carpeta, generar(db_plantilla, carpeta, config.cargar())


def png(ancho=40, alto=30) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (ancho, alto), "white").save(b, format="PNG")
    return b.getvalue()


def programar(ollama_falso, campos_por_tipo, tipo_clasificado=None):
    """El chat simulado responde según el esquema pedido: clasificación o extracción."""
    def responder(cuerpo):
        esquema = cuerpo.get("format") or {}
        if esquema.get("title") == "Clasificacion":
            return json.dumps({"tipo_documento": tipo_clasificado or "otro"})
        return json.dumps(campos_por_tipo[esquema["title"]])
    ollama_falso.por_defecto = responder


# ───────────── Esquemas y normalización ─────────────

@pytest.mark.parametrize("texto,valor", [("Q 12,500.00", 12500.0), ("12500", 12500.0), ("US$ 1,250.5", 1250.5),
                                          ("Q12.500,00", 12500.0), ("3,964.34", 3964.34), (480000, 480000.0)])
def test_normaliza_montos(texto, valor):
    assert normalizar_monto(texto) == valor


def test_normaliza_campos_que_lee_el_modelo():
    b = validar_campos("boleta_deposito", {"cuenta": "at 00107 01", "titular": " Ana ", "monto_total": "Q 1,000.00",
                                           "fecha": "15/10/2026", "moneda": "Q", "numero_boleta": "—"})
    assert (b.cuenta, b.titular, b.monto_total, b.fecha.isoformat(), b.moneda, b.numero_boleta) == \
        ("AT-00107-01", "Ana", 1000.0, "2026-10-15", "GTQ", None)
    with pytest.raises(ValueError):
        validar_campos("boleta_deposito", {"cuenta": "AT-00107-01"})  # falta titular y monto
    with pytest.raises(ValueError):
        validar_campos("pasaporte", {})


@pytest.mark.parametrize("doc,base,esperado", [
    ("CARLOS ENRIQUE XICARA MORALES", "Carlos Enrique Xicará Morales", "igual"),
    ("Carlos Enrique Xicora Morales", "Carlos Enrique Xicará Morales", "parecido"),
    (NOMBRE_FALSO_0107, "Carlos Enrique Xicará Morales", "distinto"),
    ("Lucía Cojtí", "Lucía Fernanda Cojtí Barrios", "distinto"),
])
def test_comparar_nombres(doc, base, esperado):
    assert comparar_nombres(doc, base)[0] == esperado


# ───────────── Documentos demo y validación cruzada ─────────────

def test_genera_documentos_marcados_y_manifiesto(demo):
    carpeta, m = demo
    assert set(m["documentos"]) == {"boleta_deposito_ok.png", "boleta_deposito_discrepancia.png",
                                    "solicitud_credito_discrepancia.png", "factura_comercio_ok.png"}
    for nombre in m["documentos"]:
        img = Image.open(carpeta / nombre)
        assert img.width >= 1000
    assert json.loads((carpeta / "manifiesto.json").read_text(encoding="utf-8"))["aviso"] == "DEMO / DATOS FICTICIOS"
    assert m["documentos"]["boleta_deposito_discrepancia.png"]["campos"]["cuenta"].startswith("AT-00107-")


def test_veredictos_de_los_documentos_demo(db, demo):
    con = conectar_lectura(db)
    for nombre, info in demo[1]["documentos"].items():
        r = validar(con, info["tipo_documento"], info["campos"])
        assert r["veredicto"] == info["veredicto_esperado"], (nombre, r["resumen"])
        malas = {v["verificacion"] for v in r["verificaciones"] if v["estado"] == "discrepancia"}
        assert malas == set(info.get("discrepancias_esperadas", [])), (nombre, r["verificaciones"])


def test_boleta_con_discrepancia_dice_que_la_cuenta_existe_pero_el_titular_no(db, demo):
    info = demo[1]["documentos"]["boleta_deposito_discrepancia.png"]
    r = validar(conectar_lectura(db), "boleta_deposito", info["campos"])
    estados = {v["verificacion"]: v for v in r["verificaciones"]}
    assert estados["cuenta_existe"]["estado"] == "ok"
    assert estados["movimiento_registrado"]["estado"] == "ok"
    assert estados["titular_coincide"]["estado"] == "discrepancia"
    assert estados["titular_coincide"]["base"] == "Carlos Enrique Xicará Morales"
    assert r["cliente"]["id"] == "CLI-0107" and r["politica_aplicable"] == "manual_validacion_documental.md"


def test_solicitud_detecta_saldo_inflado(db, demo):
    info = demo[1]["documentos"]["solicitud_credito_discrepancia.png"]
    r = validar(conectar_lectura(db), "solicitud_credito", info["campos"])
    saldo = next(v for v in r["verificaciones"] if v["verificacion"] == "saldo_declarado")
    assert saldo["estado"] == "discrepancia" and saldo["base"] == 277400.0
    assert any(v["verificacion"] == "tramites_pendientes" for v in r["verificaciones"])  # ticket KYC sembrado


def test_cuenta_inexistente_y_monto_sin_movimiento(db, demo):
    con = conectar_lectura(db)
    campos = dict(demo[1]["documentos"]["boleta_deposito_ok.png"]["campos"])
    r = validar(con, "boleta_deposito", {**campos, "cuenta": "AT-99999-01"})
    assert r["veredicto"] == "con_discrepancias"
    assert {v["verificacion"]: v["estado"] for v in r["verificaciones"]}["titular_coincide"] == "no_verificable"
    r = validar(con, "boleta_deposito", {**campos, "monto_total": 123456.78, "monto_efectivo": 123456.78})
    mov = next(v for v in r["verificaciones"] if v["verificacion"] == "movimiento_registrado")
    assert mov["estado"] == "discrepancia"
    r = validar(con, "boleta_deposito", {**campos, "monto_efectivo": 1.0})
    assert any(v["verificacion"] == "suma_montos" for v in r["verificaciones"])


def test_factura_sin_cuenta_se_identifica_por_la_compra(db, demo):
    campos = dict(demo[1]["documentos"]["factura_comercio_ok.png"]["campos"])
    campos["cuenta_tarjeta"] = None
    r = validar(conectar_lectura(db), "factura", campos)
    assert r["veredicto"] == "coincide" and r["cliente"]["id"] == "CLI-0042"


# ───────────── Extracción con el modelo de visión (simulado) ─────────────

async def test_extraccion_clasifica_y_valida(ollama_falso, demo):
    carpeta, m = demo
    info = m["documentos"]["boleta_deposito_discrepancia.png"]
    programar(ollama_falso, {"BoletaDeposito": info["campos"]}, "boleta_deposito")
    llamar = llamador_ollama(ollama_falso.cliente, "qwen3-vl:4b-instruct")
    ext = await extraer(llamar, (carpeta / "boleta_deposito_discrepancia.png").read_bytes(), "subida.png",
                        modelo="qwen3-vl:4b-instruct")
    assert ext.tipo_documento == "boleta_deposito" and ext.campos["titular"] == NOMBRE_FALSO_0107
    assert ext.campos["cuenta"] == info["campos"]["cuenta"] and ext.advertencias == []
    chats = ollama_falso.chats()
    assert len(chats) == 2 and all(c["model"] == "qwen3-vl:4b-instruct" for c in chats)
    assert chats[1]["format"]["title"] == "BoletaDeposito" and chats[1]["options"]["temperature"] == 0
    assert chats[1]["messages"][1]["images"]  # la imagen viaja en base64
    # La imagen se reduce antes de enviarla (1400 px de ancho → máx. 1600: queda igual; se comprueba el formato)
    assert Image.open(io.BytesIO(__import__("base64").b64decode(chats[1]["messages"][1]["images"][0]))).format == "PNG"


async def test_reintenta_si_el_json_es_invalido(ollama_falso):
    ollama_falso.respuestas = ['{"titular": "Ana"', json.dumps({"cuenta": "AT-00042-01", "titular": "Ana",
                                                                 "monto_total": "Q 10.00"})]
    ext = await extraer(llamador_ollama(ollama_falso.cliente, "vl"), png(), "x.png", tipo="boleta_deposito")
    assert ext.intentos == 2 and ext.campos["monto_total"] == 10.0
    assert any("segundo" in a for a in ext.advertencias)
    assert "no es válido" in ollama_falso.chats()[1]["messages"][-1]["content"]


async def test_falla_con_mensaje_claro(ollama_falso):
    ollama_falso.respuestas = ["no sé", "tampoco"]
    with pytest.raises(ErrorExtraccion, match="2 intentos"):
        await extraer(llamador_ollama(ollama_falso.cliente, "vl"), png(), "x.png", tipo="factura")
    with pytest.raises(ErrorExtraccion, match="PDF"):
        await extraer(llamador_ollama(ollama_falso.cliente, "vl"), b"%PDF-1.7 ...", "x.pdf", tipo="factura")
    with pytest.raises(ErrorExtraccion, match="imagen válida"):
        await extraer(llamador_ollama(ollama_falso.cliente, "vl"), b"hola", "x.png", tipo="factura")


async def test_tipo_desconocido_usa_el_nombre_del_archivo(ollama_falso, demo):
    info = demo[1]["documentos"]["factura_comercio_ok.png"]
    programar(ollama_falso, {"Factura": info["campos"]}, "otro")
    ext = await extraer(llamador_ollama(ollama_falso.cliente, "vl"), png(), "factura_123.jpg")
    assert ext.tipo_documento == "factura" and "nombre del archivo" in ext.advertencias[0]
    programar(ollama_falso, {}, "otro")
    with pytest.raises(ErrorExtraccion, match="No reconocí"):
        await extraer(llamador_ollama(ollama_falso.cliente, "vl"), png(), "escaneo.png")


async def test_imagen_grande_se_reduce(ollama_falso):
    ollama_falso.respuestas = [json.dumps({"cuenta": "AT-00042-01", "titular": "Ana", "monto_total": 1})]
    await extraer(llamador_ollama(ollama_falso.cliente, "vl"), png(4000, 3000), "b.png", tipo="boleta_deposito",
                  max_lado=1600)
    import base64

    enviada = Image.open(io.BytesIO(base64.b64decode(ollama_falso.chats()[0]["messages"][1]["images"][0])))
    assert max(enviada.size) == 1600


# ───────────── Gancho del orquestador ─────────────

class ClienteOrquestadorFalso:
    """Imita `orchestrator/ollama.py`: chat(rol, mensajes, imagenes=..., formato=...) asíncrono."""

    def __init__(self, respuestas):
        self.respuestas, self.llamadas = list(respuestas), []

    async def chat(self, rol, mensajes, imagenes=None, formato=None, opciones=None):
        self.llamadas.append({"rol": rol, "imagenes": imagenes, "formato": formato})
        return {"message": {"content": self.respuestas.pop(0)}}


async def test_gancho_de_vision_con_cliente_del_orquestador(cfg, demo):
    carpeta, m = demo
    info = m["documentos"]["solicitud_credito_discrepancia.png"]
    cli = ClienteOrquestadorFalso([json.dumps({"tipo_documento": "solicitud_credito"}), json.dumps(info["campos"])])
    vision = crear_vision(cfg, cli)
    ext = await vision.extraer((carpeta / "solicitud_credito_discrepancia.png").read_bytes(),
                               "solicitud_credito_discrepancia.png")
    assert ext.tipo_documento == "solicitud_credito" and ext.modelo == cfg["modelos"]["vision"]["nombre"]
    assert ext.campos["solicitante"] == NOMBRE_FALSO_0107
    assert [c["rol"] for c in cli.llamadas] == ["vision", "vision"]
    assert isinstance(cli.llamadas[0]["imagenes"][0], bytes) and cli.llamadas[1]["formato"]["title"] == "SolicitudCredito"


async def test_gancho_sin_cliente_usa_ollama_http(cfg, ollama_falso, demo):
    info = demo[1]["documentos"]["factura_comercio_ok.png"]
    programar(ollama_falso, {"Factura": info["campos"]}, "factura")
    ext = await crear_vision(cfg, ollama_falso.cliente).extraer(png(), "f.png")
    assert ext.campos["total"] == info["campos"]["total"]


# ───────────── Herramienta MCP validar_documento ─────────────

async def test_herramienta_validar_documento(servidor, db, demo):
    info = demo[1]["documentos"]["boleta_deposito_discrepancia.png"]
    async with Client(servidor) as c:
        r = await c.call_tool("validar_documento", {"tipo_documento": "boleta_deposito", "campos": info["campos"]},
                              meta={"usuario": "estuardo", "modelo": "agente"})
        malo = await c.call_tool("validar_documento", {"tipo_documento": "factura", "campos": {"total": "x"}})
    assert r.structured_content["ok"] and r.structured_content["veredicto"] == "con_discrepancias"
    assert malo.structured_content["ok"] is False
    filas = sqlite3.connect(db).execute(
        "SELECT estado, parametros FROM auditoria WHERE herramienta='validar_documento' ORDER BY id").fetchall()
    assert [f[0] for f in filas] == ["ok", "rechazado"]
    assert "AT-•••07-" in filas[0][1]  # cuentas enmascaradas en auditoría
