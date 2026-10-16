"""Simulador del orquestador para desarrollar y ensayar la UI sin modelos.

Habla el contrato de eventos v1, rev. 1.1 (docs/CONTRATO_EVENTOS.md) en las mismas rutas que el
orquestador real, así que `web/` funciona igual contra uno u otro.

Qué es real y qué es simulado:
  - REAL: las herramientas (servidor MCP banco-atitlan en memoria, contra data/banco.db),
    la aprobación humana (elicitation de MCP) y todo el Panel de Soberanía
    (/api/soberania: conexiones con psutil, red, /api/ps de Ollama, costo).
  - SIMULADO: los modelos. El "LLM" es un guion por palabras clave con textos de
    plantilla; los tokens y latencias son inventados. No hay STT ni TTS.

Uso:  make ui-sim   (requiere `make seed`)  →  http://127.0.0.1:<ui.puerto_simulador>
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import mcp_types as types
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.client.client import Client

from comun import config
from comun.config import RAIZ
from mcp_server.server import crear_servidor
from orchestrator.soberania import crear_router

CFG = config.cargar()
MODELOS = {rol: (CFG["modelos"][rol].get("nombre") or CFG["modelos"][rol].get("motor"))
           for rol in ("agente", "rapido", "vision", "embeddings", "stt", "tts")}

GUION = [
    {"acto": 1, "titulo": "Cartera", "texto": "Centinela, ¿cómo está la cartera de la clienta Lucía Cojtí?"},
    {"acto": 2, "titulo": "Fraude", "texto": "¿Ve movimientos raros en sus cuentas este mes?"},
    {"acto": 2, "titulo": "Ticket", "texto": "Abra un ticket para el equipo de fraude."},
    {"acto": 3, "titulo": "Política", "texto": "¿Qué dice nuestra política sobre bloqueo preventivo de tarjetas en este caso?"},
    {"acto": 4, "titulo": "Documento", "texto": "Valide esta boleta de depósito contra la base.", "documento_id": "demo-boleta"},
    {"acto": 5, "titulo": "NOC", "texto": "¿Hay alertas críticas en la infraestructura?"},
]


def sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


# ─────────────────────────── Sesiones y eventos ───────────────────────────

@dataclass
class Sesion:
    id: str
    seq: int = 0
    buffer: list[dict] = field(default_factory=list)
    clientes: set[WebSocket] = field(default_factory=set)
    turnos: int = 0
    turno: asyncio.Task | None = None
    acumulado: dict = field(default_factory=lambda: {
        "turnos": 0, "tokens_entrada": 0, "tokens_salida": 0, "herramientas_ejecutadas": 0,
        "documentos_analizados": 0, "aprobaciones": {"aprobadas": 0, "rechazadas": 0}, "modelos_usados": []})
    contexto: dict = field(default_factory=dict)   # "memoria": último cliente mencionado, etc.

    async def emitir(self, tipo: str, turno_id: str | None, datos: dict) -> None:
        self.seq += 1
        ev = {"v": 1, "seq": self.seq, "tipo": tipo, "sesion_id": self.id, "turno_id": turno_id,
              "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "datos": datos}
        self.buffer = (self.buffer + [ev])[-1000:]
        for ws in list(self.clientes):
            try:
                await ws.send_json(ev)
            except Exception:  # noqa: BLE001 (cliente desconectado)
                self.clientes.discard(ws)


SESIONES: dict[str, Sesion] = {}
APROBACIONES: dict[str, asyncio.Future] = {}
DOCUMENTOS: dict[str, dict] = {}
_ids = itertools.count(1)


def sesion(sid: str) -> Sesion:
    return SESIONES.setdefault(sid, Sesion(sid))


# ─────────────────────────── Turno simulado ───────────────────────────

class Turno:
    """Un turno: emite los eventos del contrato y lleva las métricas."""

    def __init__(self, s: Sesion, entrada: str, texto: str, documento_id: str | None, turno_id: str | None = None):
        s.turnos += 1
        self.s, self.texto, self.documento_id, self.entrada = s, texto, documento_id, entrada
        self.id = turno_id or f"t-{s.turnos:04d}"
        self.paso = 0
        self.t0 = time.perf_counter()
        self.lat = {k: 0 for k in ("stt", "vision", "intencion", "llm", "herramientas", "aprobacion", "resumen", "tts")}
        self.tok_in = self.tok_out = 0
        self.ttft: int | None = None
        self.modelos: list[str] = []
        self.herramientas: list[str] = []
        self.iteracion = 0

    async def ev(self, tipo: str, **datos) -> None:
        await self.s.emitir(tipo, self.id, datos)

    async def modelo(self, rol: str, tarea: str) -> int:
        self.paso += 1
        if MODELOS[rol] not in self.modelos:
            self.modelos.append(MODELOS[rol])
        await self.ev("modelo.uso", paso=self.paso, rol=rol, modelo=MODELOS[rol], tarea=tarea)
        return self.paso

    async def esperar(self, etapa: str, ms: int) -> None:
        ms = int(ms * random.uniform(0.85, 1.15))
        await asyncio.sleep(ms / 1000)
        self.lat[etapa] += ms

    async def llm(self, pedidas: list[str], texto: str = "") -> None:
        """Una vuelta del modelo agente: pide herramientas o escribe la respuesta en streaming."""
        self.iteracion += 1
        paso = await self.modelo("agente", "Razonar con herramientas" if pedidas else "Redactar respuesta")
        await self.ev("llm.inicio", paso=paso, rol="agente", modelo=MODELOS["agente"], iteracion=self.iteracion)
        t = time.perf_counter()
        tok_in = random.randint(1800, 3200) + 400 * self.iteracion
        await self.esperar("llm", 450)
        ttft = int((time.perf_counter() - t) * 1000)
        trozos = [p + " " for p in texto.split(" ")] if texto else []
        if trozos and self.ttft is None:
            self.ttft = int((time.perf_counter() - self.t0) * 1000)
        for i in range(0, len(trozos), 2):
            await self.ev("llm.delta", paso=paso, texto="".join(trozos[i:i + 2]))
            await self.esperar("llm", 45)
        tok_out = max(12, int(len(texto) / 3.6)) if texto else random.randint(25, 60)
        dur = int((time.perf_counter() - t) * 1000)
        self.tok_in += tok_in
        self.tok_out += tok_out
        await self.ev("llm.fin", paso=paso, rol="agente", modelo=MODELOS["agente"], iteracion=self.iteracion,
                      ttft_ms=ttft, duracion_ms=dur, tokens_entrada=tok_in, tokens_salida=tok_out,
                      tokens_por_s=round(random.uniform(34, 46), 1), herramientas_pedidas=pedidas)

    async def herramienta(self, nombre: str, args: dict, escritura: bool = False) -> dict:
        llamada_id = f"ll-{next(_ids)}"
        self.herramientas.append(nombre)
        await self.ev("herramienta.inicio", llamada_id=llamada_id, paso=self.paso, nombre=nombre,
                      argumentos=args, escritura=escritura)
        t = time.perf_counter()
        CONTEXTO_LLAMADA["turno"], CONTEXTO_LLAMADA["llamada_id"] = self, llamada_id
        r = await MCP.call_tool(nombre, args, meta={"usuario": "ejecutivo.demo", "modelo": MODELOS["agente"]})
        dur = int((time.perf_counter() - t) * 1000) - self._ms_aprobacion(llamada_id)
        await asyncio.sleep(0.15)
        self.lat["herramientas"] += max(dur, 1)
        res = r.structured_content or {}
        await self.ev("herramienta.fin", llamada_id=llamada_id, nombre=nombre, ok=not r.is_error,
                      duracion_ms=max(dur, 1), resumen=_resumen(nombre, res), resultado=res)
        return res

    def _ms_aprobacion(self, llamada_id: str) -> int:
        return getattr(self, "_aprob_ms", {}).pop(llamada_id, 0)

    async def final(self, texto: str, hablado: str | None, citas: list | None = None) -> None:
        await self.llm([], texto)
        await self.modelo("rapido", "Resumen para voz")
        await self.esperar("resumen", 280)
        await self.ev("respuesta.final", texto=texto, resumen_hablado=hablado, citas=citas or [])

    async def cerrar(self, ok: bool = True, cancelado: bool = False) -> None:
        self.lat["total"] = int((time.perf_counter() - self.t0) * 1000)
        await self.ev("turno.fin", ok=ok, cancelado=cancelado, iteraciones=self.iteracion, metricas={
            "latencias_ms": self.lat, "ttft_ms": self.ttft or 0, "tokens_entrada": self.tok_in,
            "tokens_salida": self.tok_out, "tokens_por_s": round(random.uniform(36, 44), 1),
            "modelos": self.modelos, "herramientas": self.herramientas})
        a = self.s.acumulado
        a["turnos"] += 1
        a["tokens_entrada"] += self.tok_in
        a["tokens_salida"] += self.tok_out
        a["herramientas_ejecutadas"] += len(self.herramientas)
        a["modelos_usados"] = list(dict.fromkeys(a["modelos_usados"] + self.modelos))
        await self.s.emitir("sesion.acumulado", None, a)


CONTEXTO_LLAMADA: dict[str, Any] = {}


def _resumen(nombre: str, r: dict) -> str:
    match nombre:
        case "buscar_cliente":
            return f"{r.get('coincidencias', 0)} cliente(s)"
        case "resumen_cliente":
            return f"{(r.get('cliente') or {}).get('nombre', '?')}: {len(r.get('productos', []))} productos"
        case "detectar_anomalias":
            return f"{r.get('total_hallazgos', 0)} hallazgos"
        case "alertas_infraestructura":
            return f"{r.get('total', 0)} alertas, {r.get('sin_resolver', 0)} sin resolver"
        case "buscar_politicas":
            return f"{len(r.get('fragmentos', []))} fragmentos"
        case "consulta_sql":
            return f"{r.get('filas_devueltas', 0)} filas" if r.get("ok") else "rechazada"
        case "crear_ticket" | "actualizar_ticket":
            t = r.get("ticket") or {}
            return f"ticket #{t.get('id')} {t.get('estado', '')}".strip() if t else (r.get("mensaje") or "no ejecutado")
    return "ok"


async def elicitacion(_ctx, params: types.ElicitRequestParams) -> types.ElicitResult:
    """Aprobación humana: el servidor MCP la pide y la UI la resuelve (tarjeta Aprobar / Rechazar)."""
    turno: Turno = CONTEXTO_LLAMADA["turno"]
    llamada_id = CONTEXTO_LLAMADA["llamada_id"]
    aid = f"ap-{next(_ids):04d}"
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    APROBACIONES[aid] = fut
    await turno.ev("aprobacion.solicitada", aprobacion_id=aid, llamada_id=llamada_id,
                   herramienta=turno.herramientas[-1], mensaje=params.message,
                   argumentos=turno.s.contexto.get("args_escritura", {}), expira_en_s=120)
    t = time.perf_counter()
    origen = "persona"
    try:
        aprobar, comentario = await asyncio.wait_for(fut, 120)
    except TimeoutError:
        aprobar, comentario, origen = False, "Tiempo agotado", "tiempo_agotado"
    finally:
        APROBACIONES.pop(aid, None)
    ms = int((time.perf_counter() - t) * 1000)
    turno.lat["aprobacion"] += ms
    turno._aprob_ms = {llamada_id: ms}
    clave = "aprobadas" if aprobar else "rechazadas"
    turno.s.acumulado["aprobaciones"][clave] += 1
    await turno.ev("aprobacion.resuelta", aprobacion_id=aid, llamada_id=llamada_id, aprobada=aprobar,
                   comentario=comentario, origen=origen)
    return types.ElicitResult(action="accept", content={"aprobar": aprobar, "comentario": comentario})


def q(monto: float, moneda: str = "GTQ") -> str:
    return f"{'Q' if moneda == 'GTQ' else 'US$'}{monto:,.2f}"


async def correr_turno(s: Sesion, entrada: str, texto: str, documento_id: str | None,
                       turno_id: str | None = None) -> None:
    t = Turno(s, entrada, texto, documento_id, turno_id)
    try:
        await t.ev("turno.inicio", entrada=entrada, texto=texto, documento_id=documento_id)
        if documento_id:
            await acto_documento(t)
        else:
            await t.modelo("rapido", "Clasificar intención")
            await t.esperar("intencion", 180)
            intencion = clasificar(texto)
            await t.ev("intencion", intencion=intencion, modelo=MODELOS["rapido"], duracion_ms=t.lat["intencion"])
            await ACTOS.get(intencion, acto_otro)(t)
        await t.cerrar()
    except asyncio.CancelledError:
        await t.ev("aviso", mensaje="Turno cancelado.", etapa=None)
        await t.cerrar(ok=False, cancelado=True)
    except Exception as e:  # noqa: BLE001
        await t.ev("error", codigo="interno", mensaje=f"Algo falló en el simulador: {e}", etapa=None, recuperable=True)
        await t.cerrar(ok=False)


def clasificar(texto: str) -> str:
    t = sin_tildes(texto)
    if "ticket" in t:
        return "tickets"
    if any(p in t for p in ("politica", "bloqueo", "manual", "norma")):
        return "politicas"
    if any(p in t for p in ("raro", "anomal", "fraude", "sospech")):
        return "fraude"
    if any(p in t for p in ("alerta", "infraestructura", "noc", "vks", "nsx")):
        return "noc"
    if any(p in t for p in ("cartera", "cliente", "clienta", "saldo")):
        return "cartera"
    return "otro"


async def cliente_actual(t: Turno) -> dict:
    """Busca el cliente mencionado o usa el de la conversación (memoria de sesión)."""
    nombre = None
    for clave in ("lucia", "cojti"):
        if clave in sin_tildes(t.texto):
            nombre = "Lucía Cojtí"
    if nombre or "cliente_id" not in t.s.contexto:
        await t.llm(["buscar_cliente"])
        r = await t.herramienta("buscar_cliente", {"texto": nombre or "Lucía Cojtí"})
        cli = (r.get("clientes") or [{}])[0]
        t.s.contexto.update(cliente_id=cli.get("id"), cliente_nombre=cli.get("nombre"))
    return {"id": t.s.contexto["cliente_id"], "nombre": t.s.contexto["cliente_nombre"]}


async def acto_cartera(t: Turno) -> None:
    cli = await cliente_actual(t)
    await t.llm(["resumen_cliente"])
    r = await t.herramienta("resumen_cliente", {"cliente_id": cli["id"]})
    c = r["cliente"]
    prods = "; ".join(f"{p['tipo']} {p['id']}: {q(p['saldo'], p['moneda'])}" for p in r["productos"])
    act = r["actividad_30_dias"]
    texto = (f"{c['nombre']} es clienta del segmento {c['segmento']} en {c['departamento']}, con riesgo KYC "
             f"{c['riesgo_kyc']} y ejecutiva {c['ejecutivo']}. Tiene {len(r['productos'])} productos: {prods}. "
             f"En los últimos 30 días registró {act['operaciones']} operaciones por {q(act['monto_total'])}. "
             + ("No tiene tickets abiertos." if not r["tickets_abiertos"]
                else f"Tiene {len(r['tickets_abiertos'])} ticket(s) abierto(s)."))
    await t.final(texto, f"{c['nombre']} tiene {len(r['productos'])} productos activos y riesgo KYC "
                         f"{c['riesgo_kyc']}. Su actividad del mes es de {q(act['monto_total'])}.")


async def acto_fraude(t: Turno) -> None:
    cli = await cliente_actual(t)
    await t.llm(["detectar_anomalias"])
    r = await t.herramienta("detectar_anomalias", {"cliente_id": cli["id"], "dias": 30})
    h = r.get("hallazgos", [])
    lineas = [f"- {x['fecha_hora'][:16]} · {q(x['monto'], x['moneda'])} · {x['comercio']} ({x['ubicacion']}): "
              f"{'; '.join(x['motivos'][:2])}." for x in h[:4]]
    base = r.get("linea_base", {})
    texto = (f"Sí. Encontré {r.get('total_hallazgos', 0)} operaciones anómalas de {cli['nombre']} este mes, "
             f"frente a un promedio de {q(base.get('promedio_gtq', 0))} por operación:\n" + "\n".join(lineas) +
             "\nRecomiendo abrir un ticket de fraude y evaluar el bloqueo preventivo de la tarjeta.")
    t.s.contexto["hallazgos"] = h
    await t.final(texto, f"Sí, hay {r.get('total_hallazgos', 0)} operaciones anómalas: compras de madrugada "
                         "en Panamá y una ráfaga de transferencias. Recomiendo abrir un ticket de fraude.")


async def acto_ticket(t: Turno) -> None:
    cli = await cliente_actual(t)
    await t.modelo("rapido", "Titular el ticket")
    await t.esperar("resumen", 220)
    args = {"tipo": "fraude", "prioridad": "alta", "cliente_id": cli["id"],
            "titulo": f"Posible fraude con tarjeta y transferencias — {cli['nombre']}",
            "detalle": "Compras de madrugada en Ciudad de Panamá y ráfaga de 4 transferencias ACH detectadas "
                       "por reglas explicables. Revisar y evaluar bloqueo preventivo."}
    t.s.contexto["args_escritura"] = args
    await t.llm(["crear_ticket"])
    r = await t.herramienta("crear_ticket", args, escritura=True)
    if r.get("creado"):
        tid = r["ticket"]["id"]
        texto = f"Listo. Creé el ticket #{tid} de prioridad alta para el equipo de fraude; quedó registrado en auditoría."
        hablado = "Listo, el ticket de fraude quedó creado y auditado."
    else:
        texto = "No creé el ticket porque la operación no fue aprobada. No se modificó nada en la base."
        hablado = "Entendido, no creé el ticket."
    await t.final(texto, hablado)


CITAS_EJEMPLO = [
    {"documento": "Política de prevención de fraude (ficticia)", "seccion": "4.2 Bloqueo preventivo de tarjetas",
     "fragmento": "Ante dos o más alertas de monto y ubicación inusual en 24 horas, el analista puede aplicar un "
                  "bloqueo preventivo temporal de la tarjeta y debe contactar al cliente en un plazo máximo de 2 horas.",
     "puntaje": 0.82},
    {"documento": "Manual de atención de reclamos (ficticio)", "seccion": "2.1 Contacto con el cliente",
     "fragmento": "El contacto se realiza por el canal registrado; nunca se solicitan claves ni códigos por teléfono.",
     "puntaje": 0.61},
]


async def acto_politicas(t: Turno) -> None:
    await t.modelo("embeddings", "Vectorizar la consulta")
    await t.llm(["buscar_politicas"])
    r = await t.herramienta("buscar_politicas", {"consulta": "bloqueo preventivo de tarjetas", "k": 4})
    citas = [{"documento": f.get("documento"), "seccion": f.get("seccion"), "fragmento": f.get("texto") or
              f.get("fragmento"), "puntaje": f.get("puntaje")} for f in r.get("fragmentos", [])] or CITAS_EJEMPLO
    if not r.get("fragmentos"):
        await t.ev("aviso", etapa="herramienta",
                   mensaje="Índice de políticas sin construir: el simulador muestra citas de ejemplo.")
    await t.ev("citas", citas=citas)
    c = citas[0]
    texto = (f"Según «{c['documento']}», sección {c['seccion']}: {c['fragmento']} En este caso hay más de dos "
             "alertas de monto y ubicación en menos de 24 horas, así que procede el bloqueo preventivo temporal.")
    await t.final(texto, "La política permite el bloqueo preventivo temporal y pide contactar a la clienta "
                         "en menos de dos horas.", citas)


async def acto_documento(t: Turno) -> None:
    doc = DOCUMENTOS.get(t.documento_id or "") or {}
    await t.modelo("vision", "Leer el documento")
    await t.esperar("vision", 1600)
    campos = doc.get("campos") or {"tipo": "desconocido"}
    t.s.acumulado["documentos_analizados"] += 1
    await t.ev("documento.extraido", documento_id=t.documento_id, nombre=doc.get("nombre", "documento"),
               tipo_documento=doc.get("tipo_documento", "desconocido"), campos=campos, modelo=MODELOS["vision"],
               duracion_ms=t.lat["vision"], advertencias=doc.get("advertencias", []))
    cuenta = campos.get("numero_cuenta")
    if not cuenta:
        await t.final("Extraje los campos que pude leer, pero el documento no trae un número de cuenta para "
                      "cruzarlo con la base.", "No encontré un número de cuenta en el documento.")
        return
    await t.llm(["consulta_sql"])
    r = await t.herramienta("consulta_sql", {"sql": (
        "SELECT c.id AS cuenta, c.estado, cl.id AS cliente_id, cl.nombre AS titular FROM cuentas c "
        f"JOIN clientes cl ON cl.id = c.cliente_id WHERE c.id = '{cuenta}'")})
    filas = r.get("filas") or []
    if not filas:
        texto = f"La cuenta {cuenta} no existe en la base. El documento no es válido para este banco."
        await t.final(texto, "La cuenta del documento no existe en la base.")
        return
    _, estado, _, titular = filas[0]
    nombre_doc = campos.get("titular", "")
    if sin_tildes(nombre_doc) == sin_tildes(titular):
        texto = f"La cuenta {cuenta} existe ({estado}) y el titular coincide: {titular}."
        hablado = "La cuenta existe y el titular coincide."
    else:
        texto = (f"Discrepancia: la cuenta {cuenta} existe y está {estado}, pero el titular registrado es "
                 f"{titular} y la boleta dice «{nombre_doc}». Recomiendo no acreditar el depósito hasta verificar "
                 "con el cliente.")
        hablado = "El número de cuenta existe, pero el titular no coincide con el de la boleta."
    await t.final(texto, hablado)


async def acto_noc(t: Turno) -> None:
    await t.llm(["alertas_infraestructura"])
    r = await t.herramienta("alertas_infraestructura", {"severidad": "critica", "horas": 24})
    al = r.get("alertas", [])
    lineas = [f"- {a['fecha'][11:16]} · {a['componente']}: {a['mensaje']}" for a in al]
    texto = (f"Hay {len(al)} alertas críticas sin resolver:\n" + "\n".join(lineas) +
             "\nPrioridad 1: recuperar el quórum de etcd en el clúster VKS (afecta banca en línea). "
             "Prioridad 2: revisar las sesiones BGP del Edge NSX tras el failover.")
    await t.final(texto, f"Sí, hay {len(al)} alertas críticas: el clúster VKS sin quórum de etcd y un failover "
                         "del Edge de NSX. Atendería primero el clúster.")


async def acto_otro(t: Turno) -> None:
    await t.final("Soy Centinela, el asistente de operaciones del Banco Atitlán (ficticio). Puedo consultar "
                  "clientes, detectar anomalías, buscar en políticas internas, validar documentos y revisar "
                  "alertas del NOC. ¿En qué le ayudo?", "¿En qué le ayudo?")


ACTOS = {"cartera": acto_cartera, "fraude": acto_fraude, "tickets": acto_ticket, "politicas": acto_politicas,
         "noc": acto_noc, "otro": acto_otro}


# ─────────────────────────── Documento de ejemplo ───────────────────────────

BOLETA_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
<rect width="900" height="520" fill="#fbfaf5"/><rect x="12" y="12" width="876" height="496" fill="none" stroke="#4a6b8a" stroke-width="3"/>
<text x="40" y="70" font-family="Helvetica, Arial" font-size="34" font-weight="700" fill="#24435f">Banco Atitlán (ficticio)</text>
<text x="40" y="105" font-family="Helvetica, Arial" font-size="22" fill="#24435f">BOLETA DE DEPÓSITO MONETARIO</text>
<g font-family="Courier New, monospace" font-size="24" fill="#1d1d1d">
<text x="40" y="180">No. de cuenta:  AT-00107-01</text>
<text x="40" y="230">Titular:       Carlos Enrique Xicará Mendoza</text>
<text x="40" y="280">Monto:         Q 15,000.00</text>
<text x="40" y="330">Fecha:         15/10/2026</text>
<text x="40" y="380">Agencia:       Antigua Guatemala</text>
<text x="40" y="430">Depositante:   Ana Lucía Pérez</text></g>
<g transform="rotate(-18 690 300)"><rect x="560" y="250" width="270" height="90" fill="none" stroke="#c0392b" stroke-width="6"/>
<text x="695" y="312" text-anchor="middle" font-family="Helvetica, Arial" font-size="56" font-weight="800" fill="#c0392b">DEMO</text></g>
<text x="40" y="490" font-family="Helvetica, Arial" font-size="16" fill="#7a7a7a">DOCUMENTO FICTICIO · DATOS FICTICIOS · SOLO PARA DEMOSTRACIÓN</text>
</svg>"""

DOCUMENTOS["demo-boleta"] = {
    "nombre": "boleta_deposito_demo.svg", "tipo_documento": "boleta_deposito", "contenido": BOLETA_SVG.encode(),
    "media": "image/svg+xml",
    "campos": {"numero_cuenta": "AT-00107-01", "titular": "Carlos Enrique Xicará Mendoza", "monto": 15000.00,
               "moneda": "GTQ", "fecha": "2026-10-15", "agencia": "Antigua Guatemala", "depositante": "Ana Lucía Pérez"},
    "advertencias": [],
}


# ─────────────────────────── App ───────────────────────────

MCP: Client


@asynccontextmanager
async def ciclo(_app: FastAPI):
    global MCP
    if not config.ruta("db").exists():
        raise SystemExit("Falta data/banco.db: ejecute `make seed` primero.")
    async with Client(crear_servidor(), elicitation_callback=elicitacion) as cliente:
        MCP = cliente
        yield


app = FastAPI(title="Centinela · simulador de UI (DEMO / DATOS FICTICIOS)", lifespan=ciclo)
app.include_router(crear_router(CFG))


def iniciar(s: Sesion, entrada: str, texto: str, documento_id: str | None, turno_id: str | None = None) -> str | None:
    if s.turno and not s.turno.done():
        return None
    tid = turno_id or f"t-{s.turnos + 1:04d}"
    s.turno = asyncio.create_task(correr_turno(s, entrada, texto, documento_id, tid))
    return tid


@app.websocket("/ws/{sid}")
async def ws(websocket: WebSocket, sid: str, desde_seq: int | None = None):
    await websocket.accept()
    s = sesion(sid)
    s.clientes.add(websocket)
    await websocket.send_json({"v": 1, "seq": 0, "tipo": "sesion.lista", "sesion_id": sid, "turno_id": None,
                               "ts": datetime.now(UTC).isoformat(), "datos": {
                                   "banco": CFG["demo"]["banco"], "marca_demo": CFG["demo"]["marca_demo"],
                                   "modelos": MODELOS, "herramientas": [], "voz_disponible": False,
                                   "vision_disponible": True, "version_contrato": 1, "simulador": True}})
    if desde_seq is not None:
        for ev in s.buffer:
            if ev["seq"] > desde_seq:
                await websocket.send_json(ev)
    try:
        while True:
            msg = await websocket.receive_json()
            tipo = msg.get("tipo")
            if tipo == "mensaje":
                if iniciar(s, "documento" if msg.get("documento_id") else "texto", msg.get("texto") or
                           "Valide este documento contra la base", msg.get("documento_id")) is None:
                    await s.emitir("error", None, {"codigo": "turno_en_curso", "etapa": None, "recuperable": True,
                                                   "mensaje": "Espere a que termine la respuesta en curso."})
            elif tipo == "aprobacion":
                resolver(msg.get("aprobacion_id"), bool(msg.get("aprobar")), msg.get("comentario") or "")
            elif tipo == "cancelar" and s.turno and not s.turno.done():
                s.turno.cancel()
            elif tipo == "preferencias":
                pass  # el simulador no sintetiza voz
            elif tipo == "ping":
                await websocket.send_json({"v": 1, "seq": 0, "tipo": "pong", "sesion_id": sid, "turno_id": None,
                                           "ts": datetime.now(UTC).isoformat(), "datos": {}})
    except WebSocketDisconnect:
        s.clientes.discard(websocket)


def resolver(aid: str | None, aprobar: bool, comentario: str) -> bool:
    fut = APROBACIONES.get(aid or "")
    if fut and not fut.done():
        fut.set_result((aprobar, comentario))
        return True
    return False


@app.post("/api/sesiones/{sid}/mensajes", status_code=202)
async def mensajes(sid: str, cuerpo: dict):
    tid = iniciar(sesion(sid), "documento" if cuerpo.get("documento_id") else "texto", cuerpo.get("texto") or "",
                  cuerpo.get("documento_id"))
    if tid is None:
        raise HTTPException(409, "turno_en_curso")
    return {"turno_id": tid}


_voz = itertools.cycle([g for g in GUION if "documento_id" not in g])


@app.post("/api/sesiones/{sid}/audio", status_code=202)
async def audio(sid: str, audio: UploadFile = File(...)):
    """Sin STT en el simulador: la "transcripción" es la siguiente pregunta del guion."""
    s = sesion(sid)
    if s.turno and not s.turno.done():
        raise HTTPException(409, "turno_en_curso")
    datos = await audio.read()
    texto = next(_voz)["texto"]
    tid = f"t-{s.turnos + 1:04d}"
    await s.emitir("transcripcion", tid, {"texto": texto, "modelo": MODELOS["stt"] + " (simulado)",
                                          "duracion_ms": 420, "idioma": "es", "bytes_audio": len(datos)})
    iniciar(s, "voz", texto, None, tid)
    return {"turno_id": tid}


@app.post("/api/sesiones/{sid}/preferencias")
async def preferencias(sid: str, cuerpo: dict):
    return {"ok": True}


@app.post("/api/aprobaciones/{aid}")
async def aprobaciones(aid: str, cuerpo: dict):
    if not resolver(aid, bool(cuerpo.get("aprobar")), cuerpo.get("comentario") or ""):
        raise HTTPException(404, "aprobación inexistente o ya resuelta")
    return {"ok": True}


@app.post("/api/sesiones/{sid}/cancelar")
async def cancelar(sid: str):
    s = sesion(sid)
    if s.turno and not s.turno.done():
        s.turno.cancel()
    return {"ok": True}


@app.get("/api/documentos")
async def documentos():
    return [{"documento_id": k, "nombre": v["nombre"]} for k, v in DOCUMENTOS.items()]


@app.post("/api/documentos")
async def subir(archivo: UploadFile = File(...)):
    did = f"doc-{next(_ids)}"
    DOCUMENTOS[did] = {"nombre": archivo.filename or did, "contenido": await archivo.read(),
                       "media": archivo.content_type or "application/octet-stream", "tipo_documento": "desconocido",
                       "campos": {"nota": "Simulador: sin modelo de visión, no se extraen campos reales."},
                       "advertencias": ["Extracción simulada"]}
    return {"documento_id": did, "nombre": DOCUMENTOS[did]["nombre"]}


@app.get("/api/documentos/{did}")
async def documento(did: str):
    d = DOCUMENTOS.get(did)
    if not d:
        raise HTTPException(404)
    return Response(d["contenido"], media_type=d["media"])


@app.get("/api/guion")
async def guion():
    return GUION


@app.get("/api/salud")
async def salud():
    return JSONResponse({"ollama": False, "mcp": True, "voz": False, "vision": True, "modelos": MODELOS,
                         "simulador": True})


app.mount("/", StaticFiles(directory=RAIZ / "web", html=True), name="web")


def main() -> None:
    ui = CFG.get("ui") or {}
    uvicorn.run(app, host=ui.get("host", "127.0.0.1"), port=int(ui.get("puerto_simulador", 8766)), log_level="info")


if __name__ == "__main__":
    main()
