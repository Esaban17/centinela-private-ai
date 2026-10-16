"""Servidor MCP «banco-atitlan» (SDK oficial `mcp`, MCPServer, transporte stdio).

Se puede probar solo, sin el orquestador:

    npx @modelcontextprotocol/inspector .venv/bin/python -m mcp_server

Cualquier cliente MCP puede consumirlo. Cada llamada a herramienta queda registrada
en la tabla `auditoria`. Las herramientas de escritura piden confirmación humana al
cliente mediante *elicitation* y se rechazan si no hay aprobación.

DEMO / DATOS FICTICIOS.
"""


import json
import logging
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from comun import config
from comun.mascaras import enmascarar
from mcp_server import anomalias, rag, sql_seguro
from mcp_server.db import (
    con_limite_de_tiempo,
    conectar_escritura,
    conectar_lectura,
    fecha_referencia,
    sin_acentos,
)

# Los logs van a stderr: en stdio, stdout es el canal del protocolo MCP.
log = logging.getLogger("banco-atitlan")

LECTURA = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ESCRITURA = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

TipoTicket = Literal["fraude", "servicio", "infraestructura"]
Prioridad = Literal["baja", "media", "alta", "critica"]
EstadoTicket = Literal["abierto", "en_progreso", "resuelto", "cerrado"]
Severidad = Literal["info", "advertencia", "critica"]
TipoMovimiento = Literal["deposito", "retiro", "compra", "transferencia_enviada", "transferencia_recibida",
                         "pago_servicio", "pago_prestamo"]

INSTRUCCIONES = """Servidor del Banco Atitlán (banco FICTICIO, datos de demostración).
Expone clientes, cuentas, transacciones, tickets, alertas del NOC y políticas internas.
Use buscar_cliente antes de resumen_cliente si solo conoce el nombre. Las herramientas
crear_ticket y actualizar_ticket requieren aprobación humana. Si un dato no existe, dígalo;
no lo invente. Las fechas usan la zona America/Guatemala."""


class Aprobacion(BaseModel):
    """Formulario que el cliente MCP muestra a la persona antes de una escritura."""

    aprobar: bool = Field(description="¿Aprueba esta operación?")
    comentario: str = Field(default="", description="Comentario opcional para la auditoría")


def _filas(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(f) for f in cursor.fetchall()]


def _meta(ctx: Context | None, clave: str) -> str | None:
    """Lee un campo de `_meta` de la petición (el orquestador envía usuario y modelo)."""
    if ctx is None:
        return None
    try:
        meta = ctx.request_context.meta
    except Exception:  # fuera de una petición
        return None
    if meta is None:
        return None
    if isinstance(meta, dict):
        valor = meta.get(clave)
    else:
        valor = getattr(meta, clave, None)
        if valor is None and getattr(meta, "model_extra", None):
            valor = meta.model_extra.get(clave)
    return str(valor) if valor is not None else None


def _resumir(resultado: Any) -> str:
    if isinstance(resultado, dict):
        for clave in ("resumen", "mensaje"):
            if clave in resultado:
                return str(resultado[clave])[:300]
        partes = [f"{k}={v}" for k, v in resultado.items() if isinstance(v, (int, float, str, bool))][:6]
        return "; ".join(partes)[:300] or "ok"
    return str(resultado)[:300]


class Banco:
    """Estado del servidor: rutas, conexiones y configuración."""

    def __init__(self, db: Path, cfg: dict[str, Any]):
        self.db = db
        self.cfg = cfg
        self.max_filas = int(cfg["mcp"]["max_filas"])
        self.lectura = conectar_lectura(db)
        self.escritura = conectar_escritura(db)
        self.usuario_por_defecto = os.environ.get("CENTINELA_USUARIO", "ejecutivo.demo")

    def ahora(self) -> datetime:
        return fecha_referencia(self.lectura)

    def auditar(self, ctx: Context | None, herramienta: str, parametros: dict[str, Any], resumen: str,
                estado: str) -> None:
        usuario = _meta(ctx, "usuario") or self.usuario_por_defecto
        modelo = _meta(ctx, "modelo") or "sin modelo (llamada directa)"
        self.escritura.execute(
            "INSERT INTO auditoria (fecha,usuario,herramienta,parametros,resultado_resumen,modelo,estado) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(" ", "seconds"), usuario, herramienta,
             json.dumps(enmascarar(parametros), ensure_ascii=False), enmascarar(resumen)[:500], modelo, estado))
        self.escritura.commit()
        log.info("auditoría · %s · %s · %s · %s", herramienta, estado, modelo, enmascarar(resumen)[:120])

    @asynccontextmanager
    async def registro(self, ctx: Context | None, herramienta: str, parametros: dict[str, Any]):
        """Registra la llamada en auditoría al terminar, con su resultado o su error."""
        nota: dict[str, Any] = {"estado": "ok", "resumen": ""}
        try:
            yield nota
        except Exception as e:
            self.auditar(ctx, herramienta, parametros, f"{type(e).__name__}: {e}", "error")
            raise
        else:
            self.auditar(ctx, herramienta, parametros, nota["resumen"] or "ok", nota["estado"])

    def pedir_aprobacion(self, ctx: Context, mensaje: str) -> Elicit[Aprobacion] | Aprobacion:
        """Devuelve la solicitud de aprobación humana (elicitation) o una decisión inmediata.

        Funciona con ambos estilos del protocolo: en versiones ≥ 2026-07-28 el SDK la envía como
        InputRequiredResult y el cliente reintenta con la respuesta; en versiones anteriores la
        envía como `elicitation/create` a mitad de la llamada.
        """
        if not self.cfg["mcp"].get("escritura_requiere_confirmacion", True):
            return Aprobacion(aprobar=True, comentario="confirmación desactivada en config.yaml")
        caps = ctx.client_capabilities
        if caps is None or caps.elicitation is None:
            return Aprobacion(aprobar=False,
                              comentario="el cliente MCP no soporta confirmación humana (elicitation)")
        return Elicit(mensaje, Aprobacion)


def _decision(resultado: ElicitationResult[Aprobacion]) -> tuple[bool, str]:
    if isinstance(resultado, AcceptedElicitation):
        if resultado.data.aprobar:
            return True, resultado.data.comentario
        return False, resultado.data.comentario or "rechazado por la persona"
    return False, f"rechazado por la persona ({resultado.action})"


def crear_servidor(db: Path | None = None, cfg: dict[str, Any] | None = None) -> MCPServer:
    cfg = cfg or config.cargar()
    banco = Banco(db or config.ruta("db"), cfg)
    an = cfg.get("anomalias", {})
    mcp = MCPServer(
        name=cfg["mcp"].get("servidor", "banco-atitlan"),
        title="Banco Atitlán (ficticio)",
        version="0.1.0",
        instructions=INSTRUCCIONES,
    )
    mcp.banco = banco  # type: ignore[attr-defined]  # acceso para pruebas

    # ───────────── Lectura ─────────────

    @mcp.tool(annotations=LECTURA)
    async def buscar_cliente(texto: str, ctx: Context) -> dict[str, Any]:
        """Busca clientes por nombre parcial (sin importar tildes ni mayúsculas) o por id (ej. CLI-0042 o 42).

        Devuelve hasta 10 coincidencias con id, nombre, segmento, departamento y ejecutivo."""
        async with banco.registro(ctx, "buscar_cliente", {"texto": texto}) as reg:
            t = (texto or "").strip()
            filas: list[dict[str, Any]] = []
            if t.upper().startswith("CLI-") or t.isdigit():
                cid = t.upper() if t.upper().startswith("CLI-") else f"CLI-{int(t):04d}"
                filas = _filas(banco.lectura.execute(
                    "SELECT id,nombre,segmento,departamento,riesgo_kyc,ejecutivo FROM clientes WHERE id = ?", (cid,)))
            if not filas:
                palabras = [p for p in sin_acentos(t).split() if p]
                if palabras:
                    cond = " AND ".join("sin_acentos(nombre) LIKE ?" for _ in palabras)
                    filas = _filas(banco.lectura.execute(
                        f"SELECT id,nombre,segmento,departamento,riesgo_kyc,ejecutivo FROM clientes "
                        f"WHERE {cond} ORDER BY nombre LIMIT 10", [f"%{p}%" for p in palabras]))
            reg["resumen"] = f"{len(filas)} cliente(s) para «{t}»"
            return {"coincidencias": len(filas), "clientes": filas,
                    "mensaje": None if filas else f"No se encontró ningún cliente para «{t}»."}

    @mcp.tool(annotations=LECTURA)
    async def resumen_cliente(cliente_id: str, ctx: Context) -> dict[str, Any]:
        """Ficha del cliente: datos generales, productos con saldos y estado, riesgo KYC, ejecutivo,
        tickets abiertos y actividad de los últimos 30 días."""
        async with banco.registro(ctx, "resumen_cliente", {"cliente_id": cliente_id}) as reg:
            cid = cliente_id.strip().upper()
            cli = banco.lectura.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
            if cli is None:
                reg["resumen"] = f"cliente {cid} no existe"
                return {"encontrado": False, "mensaje": f"No existe el cliente {cid}."}
            cuentas = _filas(banco.lectura.execute(
                "SELECT id,tipo,moneda,saldo,estado FROM cuentas WHERE cliente_id = ? ORDER BY id", (cid,)))
            totales: dict[str, float] = {}
            for c in cuentas:
                if c["tipo"] in ("monetaria", "ahorro"):
                    totales[c["moneda"]] = round(totales.get(c["moneda"], 0) + c["saldo"], 2)
            desde = (banco.ahora() - timedelta(days=30)).isoformat(" ")
            act = banco.lectura.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(t.monto),0) total FROM transacciones t "
                "JOIN cuentas c ON c.id=t.cuenta_id WHERE c.cliente_id=? AND t.fecha_hora>=?", (cid, desde)).fetchone()
            tickets = _filas(banco.lectura.execute(
                "SELECT id,tipo,prioridad,estado,titulo,fecha FROM tickets WHERE cliente_id=? "
                "AND estado IN ('abierto','en_progreso') ORDER BY fecha DESC", (cid,)))
            reg["resumen"] = f"{cli['nombre']}: {len(cuentas)} productos, {len(tickets)} tickets abiertos"
            return {"encontrado": True, "cliente": dict(cli), "productos": cuentas,
                    "saldo_disponible_por_moneda": totales,
                    "actividad_30_dias": {"operaciones": act["n"], "monto_total": round(act["total"], 2)},
                    "tickets_abiertos": tickets}

    @mcp.tool(annotations=LECTURA)
    async def movimientos(cuenta_id: str, ctx: Context, desde: str | None = None, hasta: str | None = None,
                          tipo: TipoMovimiento | None = None) -> dict[str, Any]:
        """Movimientos de una cuenta entre dos fechas (AAAA-MM-DD, inclusive). Por defecto, últimos 30 días.

        Devuelve como máximo el límite de filas configurado, del más reciente al más antiguo."""
        params = {"cuenta_id": cuenta_id, "desde": desde, "hasta": hasta, "tipo": tipo}
        async with banco.registro(ctx, "movimientos", params) as reg:
            ahora = banco.ahora()
            d = desde or (ahora - timedelta(days=30)).date().isoformat()
            h = hasta or ahora.date().isoformat()
            cta = banco.lectura.execute("SELECT id,tipo,moneda FROM cuentas WHERE id=?", (cuenta_id,)).fetchone()
            if cta is None:
                reg["resumen"] = "cuenta inexistente"
                return {"encontrada": False, "mensaje": f"No existe la cuenta {cuenta_id}."}
            where = "cuenta_id=? AND fecha_hora>=? AND fecha_hora<=?"
            args: list[Any] = [cuenta_id, f"{d} 00:00:00", f"{h} 23:59:59"]
            if tipo:
                where += " AND tipo=?"
                args.append(tipo)
            total = banco.lectura.execute(f"SELECT COUNT(*) FROM transacciones WHERE {where}", args).fetchone()[0]
            filas = _filas(banco.lectura.execute(
                f"SELECT id,fecha_hora,tipo,monto,canal,comercio,ubicacion,descripcion FROM transacciones "
                f"WHERE {where} ORDER BY fecha_hora DESC LIMIT ?", [*args, banco.max_filas]))
            reg["resumen"] = f"{len(filas)} de {total} movimientos"
            return {"encontrada": True, "cuenta": dict(cta), "desde": d, "hasta": h, "total": total,
                    "devueltos": len(filas), "truncado": total > len(filas), "movimientos": filas}

    @mcp.tool(annotations=LECTURA)
    async def detectar_anomalias(cliente_id: str, ctx: Context, dias: int = 30) -> dict[str, Any]:
        """Revisa las operaciones salientes del cliente en los últimos `dias` y señala anomalías con reglas
        explicables: monto muy superior a su promedio (z-score), horario de madrugada inusual, ubicación
        nunca vista y ráfagas de operaciones. Cada hallazgo incluye sus motivos."""
        async with banco.registro(ctx, "detectar_anomalias", {"cliente_id": cliente_id, "dias": dias}) as reg:
            cid = cliente_id.strip().upper()
            if banco.lectura.execute("SELECT 1 FROM clientes WHERE id=?", (cid,)).fetchone() is None:
                reg["resumen"] = "cliente inexistente"
                return {"encontrado": False, "mensaje": f"No existe el cliente {cid}."}
            res = anomalias.detectar(
                banco.lectura, cid, banco.ahora(), max(1, min(int(dias), 365)),
                dias_base=int(an.get("dias_base", 120)), umbral_z=float(an.get("umbral_z", 3.0)),
                horario=(int(an.get("horario_inicio", 0)), int(an.get("horario_fin", 5))),
                rafaga_minutos=int(an.get("rafaga_minutos", 30)),
                rafaga_min=int(an.get("rafaga_min_transacciones", 4)),
                tipo_cambio=float(cfg.get("costos", {}).get("tipo_cambio_gtq", 7.7)))
            reg["resumen"] = f"{res['total_hallazgos']} hallazgos, {len(res['rafagas'])} ráfagas"
            return res

    @mcp.tool(annotations=LECTURA)
    async def consulta_sql(sql: str, ctx: Context) -> dict[str, Any]:
        """Ejecuta una consulta SQL de SOLO LECTURA (un único SELECT, dialecto SQLite) sobre la base del banco.

        Se fuerza un LIMIT. Consulte el recurso esquema://banco o la vista v_esquema para conocer tablas y
        columnas. Cualquier intento de escritura se rechaza."""
        async with banco.registro(ctx, "consulta_sql", {"sql": sql}) as reg:
            try:
                seguro = sql_seguro.validar(sql, banco.max_filas)
            except sql_seguro.SQLNoPermitido as e:
                reg["estado"], reg["resumen"] = "rechazado", str(e)
                return {"ok": False, "mensaje": f"Consulta rechazada: {e}"}
            banco.lectura.set_authorizer(sql_seguro.autorizador)
            quitar = con_limite_de_tiempo(banco.lectura, int(cfg["mcp"].get("timeout_sql_ms", 2000)))
            try:
                cur = banco.lectura.execute(seguro)
                columnas = [d[0] for d in cur.description or []]
                filas = [list(f) for f in cur.fetchall()]
            except sqlite3.Error as e:
                reg["estado"], reg["resumen"] = "error", f"error de SQLite: {e}"
                return {"ok": False, "mensaje": f"Error al ejecutar la consulta: {e}"}
            finally:
                quitar()
                banco.lectura.set_authorizer(None)
            reg["resumen"] = f"{len(filas)} filas"
            return {"ok": True, "columnas": columnas, "filas": filas, "filas_devueltas": len(filas),
                    "limite": banco.max_filas, "posiblemente_truncado": len(filas) >= banco.max_filas}

    @mcp.tool(annotations=LECTURA)
    async def buscar_politicas(consulta: str, ctx: Context, k: int = 4) -> dict[str, Any]:
        """Busca en las políticas y manuales internos del banco (RAG local). Devuelve fragmentos con el
        documento y la sección de origen para citarlos."""
        async with banco.registro(ctx, "buscar_politicas", {"consulta": consulta, "k": k}) as reg:
            res = rag.buscar(cfg, consulta, max(1, min(int(k), 8)))
            reg["resumen"] = f"{len(res.get('fragmentos', []))} fragmentos"
            return res

    @mcp.tool(annotations=LECTURA)
    async def alertas_infraestructura(ctx: Context, severidad: Severidad | None = None,
                                      horas: int = 24) -> dict[str, Any]:
        """Alertas del NOC (vCenter, hosts ESXi, NSX Edge, clústeres VKS, almacenamiento) de las últimas
        `horas`, de la más reciente a la más antigua. Filtre por severidad: info, advertencia o critica."""
        async with banco.registro(ctx, "alertas_infraestructura", {"severidad": severidad, "horas": horas}) as reg:
            ahora = banco.ahora()
            desde = (ahora - timedelta(hours=max(1, min(int(horas), 24 * 30)))).isoformat(" ")
            where, args = "fecha >= ? AND fecha <= ?", [desde, ahora.isoformat(" ")]
            if severidad:
                where += " AND severidad = ?"
                args.append(severidad)
            filas = _filas(banco.lectura.execute(
                f"SELECT * FROM alertas_infra WHERE {where} ORDER BY fecha DESC LIMIT ?", [*args, banco.max_filas]))
            abiertas = [f for f in filas if f["estado"] != "resuelta"]
            reg["resumen"] = f"{len(filas)} alertas, {len(abiertas)} sin resolver"
            return {"desde": desde, "total": len(filas), "sin_resolver": len(abiertas), "alertas": filas}

    # ───────────── Escritura (con aprobación humana) ─────────────
    # La aprobación se resuelve ANTES de ejecutar el cuerpo de la herramienta (Resolve + Elicit).

    def aprobacion_crear_ticket(ctx: Context, tipo: str, prioridad: str, titulo: str, detalle: str,
                                cliente_id: str | None = None) -> Elicit[Aprobacion] | Aprobacion:
        cid = cliente_id.strip().upper() if cliente_id else None
        if cid and banco.lectura.execute("SELECT 1 FROM clientes WHERE id=?", (cid,)).fetchone() is None:
            return Aprobacion(aprobar=False, comentario=f"no existe el cliente {cid}")
        return banco.pedir_aprobacion(ctx, (
            f"Centinela quiere crear este ticket:\n• Tipo: {tipo}\n• Prioridad: {prioridad}\n"
            f"• Título: {titulo}\n• Cliente: {cid or '—'}\n• Detalle: {detalle}"))

    def aprobacion_actualizar_ticket(ctx: Context, id: int, estado: str, nota: str) -> Elicit[Aprobacion] | Aprobacion:
        actual = banco.lectura.execute("SELECT titulo, estado FROM tickets WHERE id=?", (id,)).fetchone()
        if actual is None:
            return Aprobacion(aprobar=False, comentario=f"no existe el ticket #{id}")
        return banco.pedir_aprobacion(ctx, (
            f"Centinela quiere actualizar el ticket #{id} «{actual['titulo']}»:\n"
            f"• Estado: {actual['estado']} → {estado}\n• Nota: {nota}"))

    @mcp.tool(annotations=ESCRITURA)
    async def crear_ticket(
        tipo: TipoTicket, prioridad: Prioridad, titulo: str, detalle: str, ctx: Context,
        aprobacion: Annotated[ElicitationResult[Aprobacion], Resolve(aprobacion_crear_ticket)],
        cliente_id: str | None = None,
    ) -> dict[str, Any]:
        """Crea un ticket (fraude, servicio o infraestructura). REQUIERE APROBACIÓN HUMANA: la persona verá
        el detalle y podrá aprobar o rechazar. Si se rechaza, no se crea nada."""
        params = {"tipo": tipo, "prioridad": prioridad, "titulo": titulo, "detalle": detalle,
                  "cliente_id": cliente_id}
        async with banco.registro(ctx, "crear_ticket", params) as reg:
            cid = cliente_id.strip().upper() if cliente_id else None
            aprobado, comentario = _decision(aprobacion)
            if not aprobado:
                reg["estado"], reg["resumen"] = "rechazado", f"no aprobado: {comentario}"
                return {"creado": False, "mensaje": f"El ticket no se creó: {comentario}."}
            usuario = _meta(ctx, "usuario") or banco.usuario_por_defecto
            cur = banco.escritura.execute(
                "INSERT INTO tickets (tipo,prioridad,estado,titulo,detalle,cliente_id,creado_por,fecha) "
                "VALUES (?,?,'abierto',?,?,?,?,?)",
                (tipo, prioridad, titulo, detalle, cid, f"centinela/{usuario}", banco.ahora().isoformat(" ")))
            banco.escritura.commit()
            reg["resumen"] = f"ticket #{cur.lastrowid} creado ({tipo}, {prioridad})"
            ticket = dict(banco.lectura.execute("SELECT * FROM tickets WHERE id=?", (cur.lastrowid,)).fetchone())
            return {"creado": True, "ticket": ticket, "aprobacion": comentario or "aprobado"}

    @mcp.tool(annotations=ESCRITURA)
    async def actualizar_ticket(
        id: int, estado: EstadoTicket, nota: str, ctx: Context,
        aprobacion: Annotated[ElicitationResult[Aprobacion], Resolve(aprobacion_actualizar_ticket)],
    ) -> dict[str, Any]:
        """Cambia el estado de un ticket y agrega una nota. REQUIERE APROBACIÓN HUMANA."""
        async with banco.registro(ctx, "actualizar_ticket", {"id": id, "estado": estado, "nota": nota}) as reg:
            actual = banco.lectura.execute("SELECT * FROM tickets WHERE id=?", (id,)).fetchone()
            if actual is None:
                reg["estado"], reg["resumen"] = "rechazado", "ticket inexistente"
                return {"actualizado": False, "mensaje": f"No existe el ticket #{id}."}
            aprobado, comentario = _decision(aprobacion)
            if not aprobado:
                reg["estado"], reg["resumen"] = "rechazado", f"no aprobado: {comentario}"
                return {"actualizado": False, "mensaje": f"El ticket no se actualizó: {comentario}."}
            usuario = _meta(ctx, "usuario") or banco.usuario_por_defecto
            banco.escritura.execute("UPDATE tickets SET estado=? WHERE id=?", (estado, id))
            banco.escritura.execute(
                "INSERT INTO tickets_notas (ticket_id,fecha,autor,estado,nota) VALUES (?,?,?,?,?)",
                (id, banco.ahora().isoformat(" "), f"centinela/{usuario}", estado, nota))
            banco.escritura.commit()
            reg["resumen"] = f"ticket #{id}: {actual['estado']} → {estado}"
            return {"actualizado": True,
                    "ticket": dict(banco.lectura.execute("SELECT * FROM tickets WHERE id=?", (id,)).fetchone())}

    # ───────────── Recursos ─────────────

    @mcp.resource("esquema://banco", name="esquema_banco", title="Esquema de la base del Banco Atitlán",
                  mime_type="text/markdown")
    def esquema() -> str:
        """Tablas, columnas y significado de cada columna."""
        lineas = ["# Esquema — Banco Atitlán (DEMO / DATOS FICTICIOS)", ""]
        tabla_actual = None
        for f in banco.lectura.execute("SELECT tabla, columna, descripcion FROM v_esquema"):
            if f["tabla"] != tabla_actual:
                tabla_actual = f["tabla"]
                lineas += ["", f"## {tabla_actual}", "", "| columna | descripción |", "|---|---|"]
            lineas.append(f"| {f['columna']} | {f['descripcion']} |")
        lineas += ["", f"Fecha de referencia de la demo («ahora»): {banco.ahora().isoformat(' ')}"]
        return "\n".join(lineas)

    @mcp.resource("politicas://indice", name="indice_politicas", title="Índice de políticas internas",
                  mime_type="text/markdown")
    def indice_politicas() -> str:
        """Documentos de políticas y manuales disponibles para buscar_politicas."""
        return rag.indice(cfg)

    # ───────────── Prompts del guion ─────────────

    @mcp.prompt(title="Revisión de posible fraude")
    def caso_fraude(nombre_cliente: str) -> str:
        """Guía para revisar movimientos sospechosos de un cliente."""
        return (f"Busque al cliente «{nombre_cliente}», revise sus anomalías de los últimos 30 días, explique "
                "cada motivo y, si corresponde, proponga un ticket de fraude citando la política de bloqueo "
                "preventivo de tarjetas.")

    @mcp.prompt(title="Revisión del NOC")
    def revision_noc() -> str:
        """Guía para priorizar alertas críticas de infraestructura."""
        return ("Liste las alertas críticas sin resolver de las últimas 24 horas, consulte el runbook del NOC "
                "para cada componente afectado y proponga pasos y prioridad de tickets.")

    return mcp


def main() -> None:
    logging.basicConfig(level=os.environ.get("CENTINELA_LOG", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    crear_servidor().run("stdio")


if __name__ == "__main__":
    main()
