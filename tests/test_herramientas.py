"""Herramientas del servidor MCP probadas a través de un cliente MCP real (transporte en memoria)."""

import json
import sqlite3

import mcp_types as types
import pytest
from mcp.client.client import Client

META = {"usuario": "estuardo", "modelo": "modelo-de-prueba"}
MODOS = ["auto", "legacy"]  # protocolo moderno (2026-07-28) y clásico (initialize)


async def llamar(servidor, herramienta, args, **kw):
    async with Client(servidor, **kw) as c:
        r = await c.call_tool(herramienta, args, meta=META)
    assert not r.is_error, r.content
    return r.structured_content


def aprobador(decision: bool, registro: list | None = None):
    async def cb(_ctx, params):
        if registro is not None:
            registro.append(params.message)
        return types.ElicitResult(action="accept", content={"aprobar": decision, "comentario": "prueba"})
    return cb


async def test_lista_de_herramientas_y_anotaciones(servidor):
    async with Client(servidor) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"buscar_cliente", "resumen_cliente", "movimientos", "detectar_anomalias", "consulta_sql",
                          "buscar_politicas", "alertas_infraestructura", "crear_ticket", "actualizar_ticket"}
    assert tools["crear_ticket"].annotations.read_only_hint is False
    assert tools["consulta_sql"].annotations.read_only_hint is True
    # El parámetro de aprobación lo resuelve el servidor; el modelo no puede «autoaprobarse».
    assert "aprobacion" not in tools["crear_ticket"].input_schema["properties"]
    for t in tools.values():
        assert t.description and len(t.description) > 40


@pytest.mark.parametrize("texto", ["Lucía Cojtí", "lucia cojti", "COJTI BARRIOS", "CLI-0042", "42"])
async def test_buscar_cliente_tolera_tildes_e_ids(servidor, texto):
    r = await llamar(servidor, "buscar_cliente", {"texto": texto})
    assert r["clientes"][0]["id"] == "CLI-0042"


async def test_buscar_cliente_sin_resultados(servidor):
    r = await llamar(servidor, "buscar_cliente", {"texto": "Persona Inexistente Zzz"})
    assert r["coincidencias"] == 0 and "No se encontró" in r["mensaje"]


async def test_resumen_cliente(servidor):
    r = await llamar(servidor, "resumen_cliente", {"cliente_id": "cli-0042"})
    assert r["cliente"]["ejecutivo"] == "Andrea Solórzano"
    assert {p["id"] for p in r["productos"]} == {"AT-00042-01", "AT-00042-02", "AT-00042-03"}
    assert r["saldo_disponible_por_moneda"] == {"GTQ": 48350.75, "USD": 12800.0}


async def test_resumen_cliente_inexistente(servidor):
    r = await llamar(servidor, "resumen_cliente", {"cliente_id": "CLI-9999"})
    assert r["encontrado"] is False


async def test_movimientos_con_limite(servidor, cfg, db):
    from mcp_server.server import crear_servidor
    cfg["mcp"]["max_filas"] = 5
    srv = crear_servidor(db, cfg)
    r = await llamar(srv, "movimientos", {"cuenta_id": "AT-00042-03", "desde": "2026-01-01", "hasta": "2026-11-20"})
    assert r["devueltos"] == 5 and r["truncado"] is True and r["total"] > 5
    fechas = [m["fecha_hora"] for m in r["movimientos"]]
    assert fechas == sorted(fechas, reverse=True)


async def test_movimientos_filtra_tipo(servidor):
    r = await llamar(servidor, "movimientos", {"cuenta_id": "AT-00042-01", "tipo": "transferencia_enviada"})
    assert r["total"] == 4 and all(m["tipo"] == "transferencia_enviada" for m in r["movimientos"])


async def test_detectar_anomalias_explica_motivos(servidor):
    r = await llamar(servidor, "detectar_anomalias", {"cliente_id": "CLI-0042", "dias": 30})
    assert r["total_hallazgos"] == 6
    assert len(r["rafagas"]) == 1 and r["rafagas"][0]["operaciones"] == 4
    top = r["hallazgos"][0]
    assert top["monto"] == 18750.0 and set(top["reglas"]) == {"monto", "horario", "ubicacion"}
    assert all(h["motivos"] for h in r["hallazgos"])
    assert "Panamá" in " ".join(top["motivos"])


async def test_cliente_normal_sin_falsos_positivos_masivos(servidor):
    r = await llamar(servidor, "detectar_anomalias", {"cliente_id": "CLI-0007"})
    assert r["total_hallazgos"] <= 3


async def test_consulta_sql_lectura(servidor):
    r = await llamar(servidor, "consulta_sql", {"sql": "SELECT segmento, COUNT(*) n FROM clientes GROUP BY 1"})
    assert r["ok"] and r["columnas"] == ["segmento", "n"]
    assert sum(f[1] for f in r["filas"]) == 200


async def test_consulta_sql_rechaza_escritura_y_audita(servidor, db):
    r = await llamar(servidor, "consulta_sql", {"sql": "DELETE FROM clientes"})
    assert r["ok"] is False and "rechazada" in r["mensaje"]
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0] == 200
    estado = con.execute("SELECT estado FROM auditoria WHERE herramienta='consulta_sql'").fetchone()[0]
    assert estado == "rechazado"


async def test_alertas_criticas(servidor):
    r = await llamar(servidor, "alertas_infraestructura", {"severidad": "critica", "horas": 24})
    assert r["total"] == 2
    assert {a["componente"] for a in r["alertas"]} == {"clúster VKS", "NSX Edge"}


async def test_buscar_politicas_sin_indice_no_inventa(servidor):
    r = await llamar(servidor, "buscar_politicas", {"consulta": "bloqueo preventivo de tarjetas"})
    assert r["fragmentos"] == [] and r["disponible"] is False


@pytest.mark.parametrize("modo", MODOS)
async def test_crear_ticket_aprobado(servidor, db, modo):
    mensajes: list[str] = []
    r = await llamar(servidor, "crear_ticket",
                     {"tipo": "fraude", "prioridad": "alta", "titulo": "Anomalías CLI-0042",
                      "detalle": "Compras en Panamá y ráfaga ACH", "cliente_id": "CLI-0042"},
                     elicitation_callback=aprobador(True, mensajes), mode=modo)
    assert r["creado"] is True and r["ticket"]["estado"] == "abierto"
    assert "Centinela quiere crear este ticket" in mensajes[0]
    con = sqlite3.connect(db)
    assert con.execute("SELECT creado_por FROM tickets WHERE id=?", (r["ticket"]["id"],)).fetchone()[0] \
        == "centinela/estuardo"


@pytest.mark.parametrize("modo", MODOS)
async def test_crear_ticket_rechazado_no_escribe(servidor, db, modo):
    antes = sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    r = await llamar(servidor, "crear_ticket", {"tipo": "servicio", "prioridad": "baja", "titulo": "x", "detalle": "y"},
                     elicitation_callback=aprobador(False), mode=modo)
    assert r["creado"] is False
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == antes


@pytest.mark.parametrize("modo", MODOS)
async def test_crear_ticket_sin_soporte_de_confirmacion_se_rechaza(servidor, db, modo):
    r = await llamar(servidor, "crear_ticket", {"tipo": "servicio", "prioridad": "baja", "titulo": "x", "detalle": "y"},
                     mode=modo)
    assert r["creado"] is False and "elicitation" in r["mensaje"]


async def test_crear_ticket_cliente_inexistente_no_pregunta(servidor):
    mensajes: list[str] = []
    r = await llamar(servidor, "crear_ticket",
                     {"tipo": "fraude", "prioridad": "alta", "titulo": "x", "detalle": "y", "cliente_id": "CLI-9999"},
                     elicitation_callback=aprobador(True, mensajes))
    assert r["creado"] is False and mensajes == []


async def test_actualizar_ticket(servidor, db):
    r = await llamar(servidor, "actualizar_ticket", {"id": 1, "estado": "en_progreso", "nota": "Revisando"},
                     elicitation_callback=aprobador(True))
    assert r["actualizado"] and r["ticket"]["estado"] == "en_progreso"
    nota = sqlite3.connect(db).execute("SELECT nota FROM tickets_notas WHERE ticket_id=1").fetchone()[0]
    assert nota == "Revisando"


async def test_toda_llamada_queda_en_auditoria_con_modelo_y_cuentas_enmascaradas(servidor, db):
    await llamar(servidor, "buscar_cliente", {"texto": "Cojtí"})
    await llamar(servidor, "movimientos", {"cuenta_id": "AT-00042-01"})
    await llamar(servidor, "resumen_cliente", {"cliente_id": "CLI-0042"})
    con = sqlite3.connect(db)
    filas = con.execute("SELECT herramienta, parametros, modelo, usuario FROM auditoria ORDER BY id").fetchall()
    assert [f[0] for f in filas] == ["buscar_cliente", "movimientos", "resumen_cliente"]
    assert all(f[2] == "modelo-de-prueba" and f[3] == "estuardo" for f in filas)
    params = json.loads(filas[1][1])
    assert params["cuenta_id"] == "AT-•••42-01"
    assert "AT-00042-01" not in " ".join(f[1] for f in filas)


async def test_recursos_y_prompts(servidor):
    async with Client(servidor) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert {"esquema://banco", "politicas://indice"} <= uris
        esquema = (await c.read_resource("esquema://banco")).contents[0].text
        assert "## transacciones" in esquema and "DATOS FICTICIOS" in esquema
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert {"caso_fraude", "revision_noc"} <= prompts
