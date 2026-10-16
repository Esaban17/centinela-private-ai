"""Validación cruzada de un documento extraído contra la base del banco.

Cada verificación es explicable: dice qué se comparó, el valor del documento, el de la base y el resultado.
Estados: `ok`, `discrepancia` (no se debe procesar), `advertencia` (revisar) y `no_verificable`.

Se usa desde la herramienta MCP `validar_documento`, así que solo lee (conexión en modo solo lectura).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from documentos.modelos import NOMBRES, BoletaDeposito, Factura, SolicitudCredito, validar_campos
from mcp_server.db import fecha_referencia, sin_acentos

TOLERANCIA_SALDO = 0.10  # manual de validación documental, saldo declarado vs. real
POLITICA = "manual_validacion_documental.md"


def _tokens(nombre: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", sin_acentos(nombre)) if t]


def _distancia(a: str, b: str) -> int:
    """Levenshtein simple (los nombres son cortos)."""
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def comparar_nombres(documento: str, base: str) -> tuple[str, str]:
    """Devuelve (`igual` | `parecido` | `distinto`, explicación).

    Ignora tildes, mayúsculas y espacios (la lectura óptica suele perder tildes). `parecido` = cada palabra
    difiere a lo sumo en una letra (probable error de lectura). Cualquier otra diferencia es `distinto`.
    """
    a, b = _tokens(documento), _tokens(base)
    if a == b:
        return "igual", "coincide con el titular registrado"
    if len(a) == len(b) and all(_distancia(x, y) <= 1 for x, y in zip(a, b)):
        return "parecido", "difiere en una letra por palabra (posible error de lectura); confirme a la vista"
    distintas = [f"«{x}» ≠ «{y}»" for x, y in zip(a, b) if _distancia(x, y) > 1]
    parecido = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    detalle = "no coincide con el titular registrado"
    if distintas:
        detalle += f" ({', '.join(distintas)})"
    if parecido >= 0.6:
        detalle += "; el nombre es parecido, lo que puede indicar suplantación"
    return "distinto", detalle


class _Validador:
    def __init__(self, con: sqlite3.Connection, tipo: str):
        self.con = con
        self.tipo = tipo
        self.verificaciones: list[dict[str, Any]] = []
        self.cliente: dict[str, Any] | None = None
        self.hoy: date = fecha_referencia(con).date()

    def agregar(self, verificacion: str, estado: str, detalle: str, documento: Any = None, base: Any = None) -> None:
        self.verificaciones.append({"verificacion": verificacion, "estado": estado, "detalle": detalle,
                                    "documento": documento, "base": base})

    def fila(self, sql: str, args: tuple) -> dict[str, Any] | None:
        f = self.con.execute(sql, args).fetchone()
        return dict(f) if f is not None else None

    # ── piezas comunes ──

    def cuenta(self, cuenta: str | None, etiqueta: str, tipos_esperados: set[str]) -> dict[str, Any] | None:
        if not cuenta:
            self.agregar("cuenta_existe", "no_verificable", f"El documento no trae {etiqueta} legible.")
            return None
        c = self.fila("SELECT c.*, cl.nombre AS titular, cl.departamento, cl.segmento, cl.riesgo_kyc "
                      "FROM cuentas c JOIN clientes cl ON cl.id = c.cliente_id WHERE c.id = ?", (cuenta,))
        if c is None:
            self.agregar("cuenta_existe", "discrepancia", f"La {etiqueta} {cuenta} no existe en la base.", cuenta)
            return None
        self.agregar("cuenta_existe", "ok", f"La {etiqueta} {cuenta} existe ({c['tipo']}, {c['moneda']}).",
                     cuenta, c["id"])
        if c["estado"] != "activa":
            self.agregar("cuenta_activa", "discrepancia", f"La cuenta está {c['estado']}.", None, c["estado"])
        if c["tipo"] not in tipos_esperados:
            self.agregar("tipo_cuenta", "advertencia",
                         f"Es una cuenta de tipo {c['tipo']}; para este documento se espera "
                         f"{' o '.join(sorted(tipos_esperados))}.", None, c["tipo"])
        self.cliente = {"id": c["cliente_id"], "nombre": c["titular"], "segmento": c["segmento"],
                        "departamento": c["departamento"], "riesgo_kyc": c["riesgo_kyc"]}
        return c

    def titular(self, nombre: str | None, cuenta: dict[str, Any] | None, etiqueta: str = "titular") -> None:
        if cuenta is None:
            self.agregar("titular_coincide", "no_verificable", "Sin cuenta válida no se puede verificar el titular.",
                         nombre)
            return
        if not nombre:
            self.agregar("titular_coincide", "no_verificable", f"El documento no trae {etiqueta} legible.")
            return
        resultado, detalle = comparar_nombres(nombre, cuenta["titular"])
        estado = {"igual": "ok", "parecido": "advertencia", "distinto": "discrepancia"}[resultado]
        self.agregar("titular_coincide", estado, f"El {etiqueta} del documento {detalle}.", nombre, cuenta["titular"])

    def moneda(self, moneda: str, cuenta: dict[str, Any] | None) -> None:
        if cuenta is not None and moneda != cuenta["moneda"]:
            self.agregar("moneda_coincide", "discrepancia",
                         f"El documento está en {moneda} y la cuenta es en {cuenta['moneda']}.", moneda, cuenta["moneda"])

    def movimiento(self, cuenta_id: str, tipo_trx: str, monto: float, fecha: date | None,
                   comercio: str | None = None) -> None:
        filas = [dict(f) for f in self.con.execute(
            "SELECT id, fecha_hora, monto, comercio FROM transacciones WHERE cuenta_id = ? AND tipo = ? "
            "AND ABS(monto - ?) < 0.005 ORDER BY fecha_hora DESC", (cuenta_id, tipo_trx, monto))]
        en_fecha = [f for f in filas if fecha is None or
                    abs((datetime.fromisoformat(f["fecha_hora"]).date() - fecha).days) <= 1]
        if comercio and en_fecha:
            mismos = [f for f in en_fecha if f["comercio"] and
                      SequenceMatcher(None, sin_acentos(comercio), sin_acentos(f["comercio"])).ratio() >= 0.6]
            if not mismos:
                self.agregar("movimiento_registrado", "advertencia",
                             f"Hay un movimiento por ese monto y fecha, pero en otro comercio ({en_fecha[0]['comercio']}).",
                             comercio, en_fecha[0]["comercio"])
                return
            en_fecha = mismos
        el, un, ninguno, registrado = {"deposito": ("el depósito", "un depósito", "ningún depósito", "registrado"),
                                       "compra": ("la compra", "una compra", "ninguna compra", "registrada")}[tipo_trx]
        if en_fecha:
            f = en_fecha[0]
            self.agregar("movimiento_registrado", "ok",
                         f"El monto cuadra con {el} #{f['id']} del {f['fecha_hora']}.",
                         monto, {"transaccion_id": f["id"], "fecha_hora": f["fecha_hora"], "monto": f["monto"]})
        elif filas:
            f = filas[0]
            self.agregar("movimiento_registrado", "advertencia",
                         f"Hay {un} por ese monto, pero con otra fecha ({f['fecha_hora']}).",
                         {"monto": monto, "fecha": fecha.isoformat() if fecha else None},
                         {"transaccion_id": f["id"], "fecha_hora": f["fecha_hora"]})
        elif fecha is not None and fecha >= self.hoy:
            self.agregar("movimiento_registrado", "advertencia",
                         f"Todavía no hay {un} {registrado} por ese monto (el documento es de hoy).", monto)
        else:
            self.agregar("movimiento_registrado", "discrepancia",
                         f"No existe {ninguno} por ese monto en la cuenta.", monto)

    # ── por tipo ──

    def boleta(self, d: BoletaDeposito) -> None:
        c = self.cuenta(d.cuenta, "cuenta", {"monetaria", "ahorro"})
        self.titular(d.titular, c)
        self.moneda(d.moneda, c)
        partes = [m for m in (d.monto_efectivo, d.monto_cheques) if m is not None]
        if partes and abs(sum(partes) - d.monto_total) > 0.005:
            self.agregar("suma_montos", "discrepancia",
                         f"Efectivo + cheques ({sum(partes):,.2f}) no suma el total ({d.monto_total:,.2f}).",
                         sum(partes), d.monto_total)
        if c is not None:
            self.movimiento(c["id"], "deposito", d.monto_total, d.fecha)

    def solicitud(self, d: SolicitudCredito) -> None:
        c = self.cuenta(d.cuenta_desembolso, "cuenta de desembolso", {"monetaria", "ahorro"})
        self.titular(d.solicitante, c, "solicitante")
        self.moneda(d.moneda, c)
        if c is None:
            return
        if d.cliente_id and d.cliente_id != c["cliente_id"]:
            self.agregar("cliente_coincide", "discrepancia",
                         f"El código de cliente {d.cliente_id} no es el dueño de la cuenta ({c['cliente_id']}).",
                         d.cliente_id, c["cliente_id"])
        if d.departamento and sin_acentos(d.departamento) != sin_acentos(c["departamento"]):
            self.agregar("departamento_coincide", "advertencia",
                         f"Departamento declarado {d.departamento}; registrado {c['departamento']}.",
                         d.departamento, c["departamento"])
        if d.saldo_promedio_declarado is not None:
            real = self.con.execute(
                "SELECT COALESCE(SUM(saldo),0) FROM cuentas WHERE cliente_id=? AND moneda=? "
                "AND tipo IN ('monetaria','ahorro')", (c["cliente_id"], d.moneda)).fetchone()[0]
            real = round(real, 2)
            if real <= 0:
                self.agregar("saldo_declarado", "discrepancia", "Declara saldo, pero no tiene cuentas con saldo en "
                             f"{d.moneda}.", d.saldo_promedio_declarado, real)
            else:
                dif = (d.saldo_promedio_declarado - real) / real
                estado = "ok" if abs(dif) <= TOLERANCIA_SALDO else "discrepancia"
                self.agregar("saldo_declarado", estado,
                             f"Saldo declarado {d.saldo_promedio_declarado:,.2f} vs. saldo actual en cuentas "
                             f"monetarias y de ahorro {real:,.2f} ({dif:+.0%}; tolerancia ±{TOLERANCIA_SALDO:.0%}).",
                             d.saldo_promedio_declarado, real)
        if c["riesgo_kyc"] == "alto":
            self.agregar("riesgo_kyc", "advertencia", "Cliente con riesgo KYC alto: requiere debida diligencia "
                         "ampliada.", None, "alto")
        pendientes = [dict(f) for f in self.con.execute(
            "SELECT id, titulo FROM tickets WHERE cliente_id=? AND estado IN ('abierto','en_progreso') ORDER BY id",
            (c["cliente_id"],))]
        if pendientes:
            self.agregar("tramites_pendientes", "advertencia",
                         "Tiene tickets abiertos: " + "; ".join(f"#{t['id']} {t['titulo']}" for t in pendientes) + ".",
                         None, pendientes)

    def factura(self, d: Factura) -> None:
        c = None
        if d.cuenta_tarjeta:
            c = self.cuenta(d.cuenta_tarjeta, "cuenta de la tarjeta", {"tarjeta"})
        else:
            # Sin cuenta impresa: se busca la compra por monto, fecha y comercio.
            cand = [dict(f) for f in self.con.execute(
                "SELECT t.cuenta_id, t.fecha_hora, t.comercio FROM transacciones t WHERE t.tipo='compra' "
                "AND ABS(t.monto - ?) < 0.005", (d.total,))]
            cand = [f for f in cand if d.fecha is None or
                    abs((datetime.fromisoformat(f["fecha_hora"]).date() - d.fecha).days) <= 1]
            if len(cand) == 1:
                self.agregar("cuenta_identificada", "ok", f"La compra se identificó en la cuenta {cand[0]['cuenta_id']}.",
                             None, cand[0]["cuenta_id"])
                c = self.cuenta(cand[0]["cuenta_id"], "cuenta de la tarjeta", {"tarjeta"})
            else:
                self.agregar("cuenta_identificada", "no_verificable",
                             f"La factura no trae cuenta y hay {len(cand)} compras posibles por ese monto y fecha.")
        self.titular(d.cliente_nombre, c, "cliente facturado")
        self.moneda(d.moneda, c)
        if c is not None:
            self.movimiento(c["id"], "compra", d.total, d.fecha, d.comercio)


def _resumen(tipo: str, verificaciones: list[dict[str, Any]], veredicto: str) -> str:
    if veredicto == "coincide":
        return f"La {NOMBRES[tipo]} coincide con la base en todas las verificaciones."
    malas = [v["detalle"] for v in verificaciones if v["estado"] == "discrepancia"]
    dudas = [v["detalle"] for v in verificaciones if v["estado"] == "advertencia"]
    buenas = [v["detalle"] for v in verificaciones if v["estado"] == "ok"]
    partes = []
    if malas:
        partes.append(f"{len(malas)} discrepancia(s): " + " ".join(malas))
    if dudas:
        partes.append("Revisar: " + " ".join(dudas))
    if buenas and malas:
        partes.append("Sí coincide: " + " ".join(buenas))
    return " ".join(partes)


def validar(con: sqlite3.Connection, tipo: str, campos: dict[str, Any]) -> dict[str, Any]:
    """Valida los campos (dict ya extraído) contra la base. Lanza `ValueError` si los campos no son válidos."""
    doc = validar_campos(tipo, campos)
    v = _Validador(con, tipo)
    {"boleta_deposito": v.boleta, "solicitud_credito": v.solicitud, "factura": v.factura}[tipo](doc)  # type: ignore[operator]
    estados = [x["estado"] for x in v.verificaciones]
    n_disc, n_adv = estados.count("discrepancia"), estados.count("advertencia")
    veredicto = "con_discrepancias" if n_disc else ("revisar" if n_adv or "no_verificable" in estados else "coincide")
    return {
        "tipo_documento": tipo,
        "documento": NOMBRES[tipo],
        "veredicto": veredicto,
        "discrepancias": n_disc,
        "advertencias": n_adv,
        "cliente": v.cliente,
        "campos": doc.model_dump(mode="json"),
        "verificaciones": v.verificaciones,
        "resumen": _resumen(tipo, v.verificaciones, veredicto),
        "politica_aplicable": POLITICA if n_disc else None,
        "aviso": "Validación automática: una persona debe revisar el documento antes de procesarlo.",
    }


__all__ = ["validar", "comparar_nombres", "TOLERANCIA_SALDO"]
