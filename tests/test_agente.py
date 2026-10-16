"""Loop del agente con Ollama simulado y el servidor MCP real (en memoria): herramientas, aprobación humana,
auditoría, enrutamiento multi-modelo, memoria, timeouts y ganchos."""

import asyncio
import logging
import sqlite3

import pytest

from orchestrator.ganchos import Audio, Extraccion, Ganchos, Transcripcion
from orchestrator.mcp_host import HostMCP
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador, TurnoEnCurso
from tests.ollama_simulado import OllamaFalso, llamar

AGENTE = "qwen3.6:35b-a3b-q4_K_M"
RAPIDO = "qwen3:4b-instruct-2507-q4_K_M"


@pytest.fixture
async def armar(servidor, cfg, db):
    creados: list[Orquestador] = []

    async def _armar(falso: OllamaFalso, ganchos: Ganchos | None = None, modo: str = "auto", **ajustes):
        for clave, valor in ajustes.items():
            cfg["orquestador"]["timeouts_s"][clave] = valor
        host = HostMCP(servidor, modo=modo)
        orq = Orquestador(cfg, Ollama(cfg, transport=falso.transport), host, ganchos, db=db)
        await host.conectar()
        creados.append(orq)
        return orq

    yield _armar
    for o in creados:
        await o.cerrar()


async def turno(orq: Orquestador, texto: str, sesion_id: str = "prueba", aprobar: bool | None = None, **kw):
    """Corre un turno completo y devuelve sus eventos. Si `aprobar` no es None, responde la aprobación."""
    s = orq.sesion(sesion_id)
    cola = s.bus.suscribir()
    orq.iniciar_turno(s, texto, **kw)
    eventos = []
    while True:
        e = await asyncio.wait_for(cola.get(), 10)
        eventos.append(e)
        if e["tipo"] == "aprobacion.solicitada" and aprobar is not None:
            assert orq.resolver_aprobacion(e["datos"]["aprobacion_id"], aprobar, "desde la prueba")
        if e["tipo"] == "sesion.acumulado":
            break
    await s.tarea
    s.bus.desuscribir(cola)
    return eventos


def de_tipo(eventos, tipo):
    return [e["datos"] for e in eventos if e["tipo"] == tipo]


CARTERA = [
    {"llamadas": [llamar("buscar_cliente", texto="Lucía Cojtí")]},
    {"llamadas": [llamar("resumen_cliente", cliente_id="CLI-0042")]},
    {"contenido": "La clienta Lucía Fernanda Cojtí Barrios tiene Q48,350.75 y US$12,800.00 disponibles."},
]


async def test_turno_con_herramientas_eventos_y_metricas(armar, db):
    falso = OllamaFalso(list(CARTERA))
    orq = await armar(falso)
    ev = await turno(orq, "¿Cómo está la cartera de Lucía Cojtí?")
    tipos = [e["tipo"] for e in ev]
    assert tipos[0] == "turno.inicio" and tipos[-2:] == ["turno.fin", "sesion.acumulado"]
    assert all(e["turno_id"] == "t-0001" for e in ev[:-1]) and ev[-1]["turno_id"] is None
    assert [e["seq"] for e in ev] == sorted(e["seq"] for e in ev)

    fines = de_tipo(ev, "herramienta.fin")
    assert [f["nombre"] for f in fines] == ["buscar_cliente", "resumen_cliente"]
    assert all(f["ok"] for f in fines)
    assert fines[1]["resultado"]["saldo_disponible_por_moneda"] == {"GTQ": 48350.75, "USD": 12800.0}

    final = de_tipo(ev, "respuesta.final")[0]
    assert "Q48,350.75" in final["texto"] and final["resumen_hablado"] == falso.resumen
    assert "".join(d["texto"] for d in de_tipo(ev, "llm.delta")) == final["texto"]

    fin = de_tipo(ev, "turno.fin")[0]
    m = fin["metricas"]
    assert fin["ok"] and fin["iteraciones"] == 3
    assert m["herramientas"] == ["buscar_cliente", "resumen_cliente"]
    assert m["tokens_entrada"] >= 3 * 900 and m["tokens_por_s"] == 40.0  # 60 tokens / 1.5 s
    assert m["ttft_ms"] is not None and set(m["latencias_ms"]) >= {"llm", "herramientas", "total"}
    assert set(m["modelos"]) == {AGENTE, RAPIDO}

    # Chips: el agente razona, el rápido clasifica y resume.
    usos = {(u["rol"], u["tarea"]) for u in de_tipo(ev, "modelo.uso")}
    assert ("agente", "Razonar y usar herramientas") in usos and ("rapido", "Resumen para voz") in usos
    assert de_tipo(ev, "intencion")[0]["intencion"] == "cartera"

    # Auditoría en el servidor MCP con usuario y modelo enviados por el orquestador.
    filas = sqlite3.connect(db).execute("SELECT herramienta, modelo, usuario FROM auditoria ORDER BY id").fetchall()
    assert filas == [("buscar_cliente", AGENTE, "ejecutivo.demo"), ("resumen_cliente", AGENTE, "ejecutivo.demo")]

    # Formato de Ollama: herramientas MCP como funciones, resultado devuelto con role=tool.
    peticion = falso.peticiones_de(AGENTE)[1]
    assert {t["function"]["name"] for t in peticion["tools"]} >= {"buscar_cliente", "crear_ticket"}
    assert peticion["keep_alive"] == -1 and peticion["think"] is False
    assert peticion["messages"][0]["role"] == "system" and "usted" in peticion["messages"][0]["content"]
    assert peticion["messages"][-1]["role"] == "tool" and "CLI-0042" in peticion["messages"][-1]["content"]


async def test_herramientas_mcp_a_formato_ollama(armar):
    orq = await armar(OllamaFalso())
    tools = {t["function"]["name"]: t["function"] for t in orq.host.para_ollama()}
    assert len(tools) == 10
    assert "aprobacion" not in tools["crear_ticket"]["parameters"]["properties"]
    assert set(tools["crear_ticket"]["parameters"]["required"]) >= {"tipo", "prioridad", "titulo", "detalle"}
    escritura = {h.nombre for h in orq.host.herramientas.values() if h.escritura}
    assert escritura == {"crear_ticket", "actualizar_ticket"}


async def test_memoria_por_sesion(armar):
    falso = OllamaFalso([{"contenido": "Hola."}, {"contenido": "Sigo aquí."}, {"contenido": "Otra sesión."}])
    orq = await armar(falso)
    await turno(orq, "Hola")
    await turno(orq, "¿Me recuerda?")
    await turno(orq, "Soy otra persona", sesion_id="otra")
    mensajes = falso.peticiones_de(AGENTE)[1]["messages"]
    assert [m["content"] for m in mensajes if m["role"] == "user"] == ["Hola", "¿Me recuerda?"]
    otra = falso.peticiones_de(AGENTE)[2]["messages"]
    assert [m["content"] for m in otra if m["role"] == "user"] == ["Soy otra persona"]


TICKET = [
    {"llamadas": [llamar("crear_ticket", tipo="fraude", prioridad="alta", titulo="ticket",
                         detalle="Compras en Panamá de madrugada y ráfaga ACH", cliente_id="CLI-0042")]},
    lambda msgs: {"contenido": "Listo, ticket creado." if '"creado": true' in msgs[-1]["content"]
                  else "El ticket no se creó porque usted lo rechazó."},
]


@pytest.mark.parametrize("modo", ["auto", "legacy"])
async def test_escritura_aprobada_crea_ticket(armar, db, modo):
    falso = OllamaFalso(list(TICKET))
    orq = await armar(falso, modo=modo)
    antes = sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    ev = await turno(orq, "Abra un ticket para fraude", aprobar=True)

    inicio = de_tipo(ev, "herramienta.inicio")[0]
    assert inicio["escritura"] is True
    assert inicio["argumentos"]["titulo"] == falso.titulo  # el modelo rápido tituló el ticket
    sol = de_tipo(ev, "aprobacion.solicitada")[0]
    assert sol["llamada_id"] == inicio["llamada_id"] and sol["herramienta"] == "crear_ticket"
    assert "Centinela quiere crear este ticket" in sol["mensaje"] and "CLI-0042" in sol["mensaje"]
    res = de_tipo(ev, "aprobacion.resuelta")[0]
    assert res["aprobada"] is True and res["origen"] == "persona"
    # La tarjeta de aprobación aparece antes del resultado.
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("aprobacion.solicitada") < tipos.index("aprobacion.resuelta") < tipos.index("herramienta.fin")

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == antes + 1
    titulo, creado_por = con.execute("SELECT titulo, creado_por FROM tickets ORDER BY id DESC").fetchone()
    assert titulo == falso.titulo and creado_por == "centinela/ejecutivo.demo"
    estado = con.execute("SELECT estado FROM auditoria WHERE herramienta='crear_ticket'").fetchone()[0]
    assert estado == "ok"
    assert de_tipo(ev, "respuesta.final")[0]["texto"] == "Listo, ticket creado."
    assert orq.sesion("prueba").acumulado["aprobaciones"] == {"aprobadas": 1, "rechazadas": 0}


async def test_escritura_rechazada_no_escribe_y_el_modelo_lo_sabe(armar, db):
    orq = await armar(OllamaFalso(list(TICKET)))
    antes = sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    ev = await turno(orq, "Abra un ticket para fraude", aprobar=False)
    assert de_tipo(ev, "aprobacion.resuelta")[0]["aprobada"] is False
    assert de_tipo(ev, "herramienta.fin")[0]["resultado"]["creado"] is False
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == antes
    estado = sqlite3.connect(db).execute("SELECT estado FROM auditoria WHERE herramienta='crear_ticket'").fetchone()
    assert estado[0] == "rechazado"
    assert "rechazó" in de_tipo(ev, "respuesta.final")[0]["texto"]


async def test_aprobacion_sin_respuesta_vence_y_rechaza(armar, db):
    orq = await armar(OllamaFalso(list(TICKET)), aprobacion=0.2)
    antes = sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    ev = await turno(orq, "Abra un ticket")
    res = de_tipo(ev, "aprobacion.resuelta")[0]
    assert res["aprobada"] is False and res["origen"] == "tiempo_agotado"
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == antes
    assert orq.aprobaciones == {}


async def test_maximo_de_iteraciones(armar, cfg):
    cfg["orquestador"]["max_iteraciones"] = 2
    pasos = [{"llamadas": [llamar("buscar_cliente", texto="x")]} for _ in range(5)]
    orq = await armar(OllamaFalso(pasos))
    ev = await turno(orq, "Busque sin parar")
    err = de_tipo(ev, "error")[0]
    assert err["codigo"] == "max_iteraciones" and err["mensaje"]
    assert de_tipo(ev, "turno.fin")[0]["ok"] is False and de_tipo(ev, "turno.fin")[0]["iteraciones"] == 2


async def test_herramienta_inexistente_se_le_informa_al_modelo(armar):
    falso = OllamaFalso([{"llamadas": [llamar("borrar_todo")]}, {"contenido": "Disculpe, no puedo."}])
    orq = await armar(falso)
    ev = await turno(orq, "Borre todo")
    assert de_tipo(ev, "herramienta.inicio") == []
    assert "no existe" in falso.peticiones_de(AGENTE)[1]["messages"][-1]["content"]
    assert de_tipo(ev, "turno.fin")[0]["ok"] is True


async def test_ollama_apagado_da_mensaje_amable(armar):
    falso = OllamaFalso()
    falso.caido = True
    orq = await armar(falso)
    ev = await turno(orq, "Hola")
    err = de_tipo(ev, "error")[0]
    assert err["codigo"] == "ollama_no_disponible" and "Ollama" in err["mensaje"]
    assert de_tipo(ev, "turno.fin")[0]["ok"] is False


async def test_timeout_del_llm(armar):
    falso = OllamaFalso([{"contenido": "tarde"}])
    falso.demora_agente_s = 1.0
    orq = await armar(falso, llm=0.2)
    ev = await turno(orq, "Hola")
    assert de_tipo(ev, "error")[0]["codigo"] == "tiempo_agotado"
    assert de_tipo(ev, "turno.fin")[0]["ok"] is False


async def test_un_turno_a_la_vez_y_cancelar(armar):
    falso = OllamaFalso([{"contenido": "lento"}])
    falso.demora_agente_s = 5
    orq = await armar(falso)
    s = orq.sesion("prueba")
    cola = s.bus.suscribir()
    orq.iniciar_turno(s, "Primera")
    with pytest.raises(TurnoEnCurso):
        orq.iniciar_turno(s, "Segunda")
    await asyncio.sleep(0.05)
    assert orq.cancelar(s)
    await asyncio.gather(s.tarea, return_exceptions=True)
    eventos = []
    while not cola.empty():
        eventos.append(cola.get_nowait())
    assert "turno_en_curso" in [e["datos"].get("codigo") for e in eventos]
    fin = [e["datos"] for e in eventos if e["tipo"] == "turno.fin"][0]
    assert fin["cancelado"] is True and fin["ok"] is False


async def test_logs_enmascaran_cuentas(armar, caplog):
    from orchestrator.registro import FiltroEnmascarado

    falso = OllamaFalso([{"llamadas": [llamar("movimientos", cuenta_id="AT-00042-01")]}, {"contenido": "Listo."}])
    orq = await armar(falso)
    filtro = FiltroEnmascarado()
    caplog.handler.addFilter(filtro)
    try:
        with caplog.at_level(logging.INFO, logger="centinela"):
            await turno(orq, "Movimientos de la cuenta AT-00042-01")
    finally:
        caplog.handler.removeFilter(filtro)
    assert "AT-•••42-01" in caplog.text and "AT-00042-01" not in caplog.text


class VisionFalsa:
    disponible = True

    async def extraer(self, imagen, nombre):
        return Extraccion("boleta_deposito", {"cuenta": "AT-00042-01", "titular": "Pedro Pérez", "monto": 1500.0},
                          "qwen3-vl:4b-instruct", ["firma ilegible"])


class VozFalsa:
    disponible = True

    async def transcribir(self, audio, formato):
        return Transcripcion("¿Cómo está la cartera de Lucía Cojtí?", "whisper-large-v3-turbo")

    async def sintetizar(self, texto):
        return Audio(b"RIFF" + texto.encode(), "wav", "kokoro", 800)


async def test_documento_pasa_por_vision_y_el_agente_lo_cruza(armar):
    falso = OllamaFalso([{"llamadas": [llamar("resumen_cliente", cliente_id="CLI-0042")]},
                         {"contenido": "La cuenta existe pero el titular no coincide."}])
    orq = await armar(falso, Ganchos(VisionFalsa(), Ganchos.vacios().transcriptor, Ganchos.vacios().sintetizador))
    doc_id = orq.documentos.guardar("boleta.png", b"\x89PNG...")
    ev = await turno(orq, "", entrada="documento", documento_id=doc_id)
    ex = de_tipo(ev, "documento.extraido")[0]
    assert ex["campos"]["titular"] == "Pedro Pérez" and ex["advertencias"] == ["firma ilegible"]
    usuario = falso.peticiones_de(AGENTE)[0]["messages"][-1]["content"]
    assert "Pedro Pérez" in usuario and "Valide este documento" in usuario and "firma ilegible" in usuario
    assert de_tipo(ev, "intencion")[0]["intencion"] == "documento"
    assert orq.sesion("prueba").acumulado["documentos_analizados"] == 1


async def test_documento_sin_vision_instalada(armar):
    orq = await armar(OllamaFalso())
    doc_id = orq.documentos.guardar("boleta.png", b"x")
    ev = await turno(orq, "", entrada="documento", documento_id=doc_id)
    assert de_tipo(ev, "error")[0]["codigo"] == "vision_no_disponible"


async def test_voz_entrada_y_salida(armar):
    voz = VozFalsa()
    falso = OllamaFalso([{"contenido": "Tiene **Q48,350.75** disponibles."}],
                        resumen="Tiene unos cuarenta y ocho mil quetzales. Todo en orden.")
    orq = await armar(falso, Ganchos(Ganchos.vacios().vision, voz, voz))
    ev = await turno(orq, "", entrada="voz", audio=(b"audio", "webm"))
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("transcripcion") < tipos.index("turno.inicio")
    assert de_tipo(ev, "turno.inicio")[0]["texto"] == "¿Cómo está la cartera de Lucía Cojtí?"
    audios = de_tipo(ev, "voz.audio")
    assert [a["texto"] for a in audios] == ["Tiene unos cuarenta y ocho mil quetzales.", "Todo en orden."]
    assert orq.audios.obtener(audios[0]["url"].rsplit("/", 1)[-1]).startswith(b"RIFF")
    assert {"whisper-large-v3-turbo", "kokoro"} <= set(de_tipo(ev, "turno.fin")[0]["metricas"]["modelos"])


async def test_voz_no_disponible(armar):
    orq = await armar(OllamaFalso())
    ev = await turno(orq, "", entrada="voz", audio=(b"audio", "webm"))
    assert de_tipo(ev, "error")[0]["codigo"] == "voz_no_disponible"
    assert de_tipo(ev, "turno.fin")[0]["ok"] is False


def test_memoria_no_deja_herramientas_huerfanas():
    from orchestrator.memoria import Memoria

    m = Memoria(max_mensajes=4)
    for i in range(3):
        m.agregar({"role": "user", "content": f"p{i}"})
        m.agregar({"role": "assistant", "content": "", "tool_calls": [{}]})
        m.agregar({"role": "tool", "content": "r"})
    assert m.mensajes[0] == {"role": "user", "content": "p2"} and len(m.mensajes) == 3
    for _ in range(5):  # un turno largo no se corta
        m.agregar({"role": "tool", "content": "r"})
    assert m.mensajes[0]["content"] == "p2"


def test_oraciones():
    from orchestrator.agente import oraciones

    assert oraciones("**Hola**, señor. ¿Cómo está?  Bien… gracias") == ["Hola, señor.", "¿Cómo está?", "Bien…",
                                                                        "gracias"]


class LocutorFalso:
    """Imita orchestrator.voz_local.locutor.Locutor de la Fase 4."""

    def __init__(self):
        self.texto, self.cola, self.cancelado = "", asyncio.Queue(), False

    def alimentar(self, delta):
        self.texto += delta
        *listas, self.texto = self.texto.split(". ")
        for o in listas:
            self.cola.put_nowait(o + ".")

    def terminar(self):
        if self.texto.strip():
            self.cola.put_nowait(self.texto.strip())
        self.cola.put_nowait(None)

    def cancelar(self):
        self.cancelado = True
        self.cola.put_nowait(None)

    async def fragmentos(self):
        from types import SimpleNamespace

        i = 0
        while (o := await self.cola.get()) is not None:
            yield SimpleNamespace(indice=i, texto=o, wav=b"RIFF" + o.encode(), motor="kokoro", duracion_ms=500,
                                  latencia_ms=120)
            i += 1


class VozConLocutor(VozFalsa):
    def locutor(self):
        self.ultimo_locutor = LocutorFalso()
        return self.ultimo_locutor


async def test_voz_habla_mientras_el_agente_escribe(armar, cfg):
    cfg["voz"] = {"modo_habla": "primeras_oraciones"}
    voz = VozConLocutor()
    falso = OllamaFalso([{"contenido": "Tiene dos cuentas activas. Su riesgo es bajo."}])
    falso.pausa_entre_trozos_s = 0.01
    orq = await armar(falso, Ganchos(Ganchos.vacios().vision, voz, voz))
    ev = await turno(orq, "¿Cómo está la cartera?")
    assert [a["texto"] for a in de_tipo(ev, "voz.audio")] == ["Tiene dos cuentas activas.", "Su riesgo es bajo."]
    # El primer audio sale antes de la respuesta final y no se gasta una llamada en el resumen.
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("voz.audio") < tipos.index("respuesta.final")
    assert de_tipo(ev, "respuesta.final")[0]["resumen_hablado"] is None
    assert falso.peticiones_de(RAPIDO) and all("format" in p for p in falso.peticiones_de(RAPIDO))
    assert de_tipo(ev, "turno.fin")[0]["metricas"]["latencias_ms"]["tts"] == 240


async def test_vision_estilo_fase_3_usa_el_cliente_del_orquestador(armar):
    """La Fase 3 llama ollama.chat("vision", ..., imagenes=[bytes], formato=...): sale el chip y el base64."""
    import json

    class VisionConOllama:
        def __init__(self, ollama):
            self.ollama = ollama

        async def extraer(self, imagen, nombre):
            r = await self.ollama.chat("vision", [{"role": "user", "content": "Extraiga los campos"}],
                                       imagenes=[imagen], formato={"type": "object", "properties": {"titulo": {}}},
                                       opciones={"temperature": 0})
            return Extraccion("boleta_deposito", json.loads(r.contenido), self.ollama.modelo("vision"))

    falso = OllamaFalso([{"contenido": "Revisado."}], titulo="AT-00042-01")
    orq = await armar(falso)
    orq.ganchos.vision = VisionConOllama(orq.ollama)
    doc_id = orq.documentos.guardar("boleta.png", b"\x89PNG")
    ev = await turno(orq, "", entrada="documento", documento_id=doc_id)
    assert ("vision", "qwen3-vl:4b-instruct") in {(u["rol"], u["modelo"]) for u in de_tipo(ev, "modelo.uso")}
    peticion = falso.peticiones_de("qwen3-vl:4b-instruct")[0]
    assert peticion["messages"][0]["images"] == ["iVBORw=="] and peticion["options"]["temperature"] == 0
    assert de_tipo(ev, "documento.extraido")[0]["modelo"] == "qwen3-vl:4b-instruct"


async def test_documento_ilegible_da_el_mensaje_de_la_vision(armar):
    class ErrorExtraccion(RuntimeError):
        pass

    class VisionQueFalla:
        async def extraer(self, imagen, nombre):
            raise ErrorExtraccion("La imagen está borrosa: tome otra foto con más luz.")

    orq = await armar(OllamaFalso())
    orq.ganchos.vision = VisionQueFalla()
    ev = await turno(orq, "", entrada="documento", documento_id=orq.documentos.guardar("x.png", b"x"))
    err = de_tipo(ev, "error")[0]
    assert err["codigo"] == "documento_ilegible" and "borrosa" in err["mensaje"]
