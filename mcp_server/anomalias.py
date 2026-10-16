"""Detección de anomalías transaccionales con reglas explicables.

No es un modelo de ML: son cuatro reglas que un analista de fraude puede auditar,
y cada hallazgo devuelve el motivo en lenguaje natural.

1. Monto:      z-score del monto contra la línea base del cliente (operaciones salientes).
2. Horario:    operación en madrugada cuando el cliente casi nunca opera a esa hora.
3. Ubicación:  ciudad nunca vista en la línea base del cliente.
4. Frecuencia: ráfaga de N o más operaciones salientes en una ventana corta.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta
from typing import Any

SALIENTES = ("compra", "retiro", "transferencia_enviada", "pago_servicio")


def _en_gtq(monto: float, moneda: str, tipo_cambio: float) -> float:
    return monto * tipo_cambio if moneda == "USD" else monto


def detectar(
    con: sqlite3.Connection,
    cliente_id: str,
    ahora: datetime,
    dias: int = 30,
    *,
    dias_base: int = 120,
    umbral_z: float = 3.0,
    horario: tuple[int, int] = (0, 5),
    rafaga_minutos: int = 30,
    rafaga_min: int = 4,
    tipo_cambio: float = 7.7,
) -> dict[str, Any]:
    desde = ahora - timedelta(days=dias)
    base_desde = desde - timedelta(days=dias_base)
    marcadores = ",".join("?" * len(SALIENTES))
    filas = con.execute(
        f"""SELECT t.id, t.cuenta_id, t.fecha_hora, t.tipo, t.monto, t.canal, t.comercio, t.ubicacion,
                   c.moneda, c.tipo AS tipo_cuenta
            FROM transacciones t JOIN cuentas c ON c.id = t.cuenta_id
            WHERE c.cliente_id = ? AND t.tipo IN ({marcadores}) AND t.fecha_hora >= ? AND t.fecha_hora <= ?
            ORDER BY t.fecha_hora""",
        (cliente_id, *SALIENTES, base_desde.isoformat(" "), ahora.isoformat(" ")),
    ).fetchall()

    base = [f for f in filas if f["fecha_hora"] < desde.isoformat(" ")]
    ventana = [f for f in filas if f["fecha_hora"] >= desde.isoformat(" ")]

    montos_base = [_en_gtq(f["monto"], f["moneda"], tipo_cambio) for f in base]
    media = statistics.fmean(montos_base) if montos_base else 0.0
    desv = statistics.pstdev(montos_base) if len(montos_base) > 1 else 0.0
    h_ini, h_fin = horario
    nocturnas_base = sum(1 for f in base if h_ini <= datetime.fromisoformat(f["fecha_hora"]).hour <= h_fin)
    pct_nocturno = nocturnas_base / len(base) if base else 0.0
    ciudades_base = {f["ubicacion"] for f in base}

    hallazgos: dict[int, dict[str, Any]] = {}

    def marcar(f: sqlite3.Row, regla: str, motivo: str, puntos: float) -> None:
        h = hallazgos.setdefault(f["id"], {
            "transaccion_id": f["id"], "cuenta_id": f["cuenta_id"], "fecha_hora": f["fecha_hora"],
            "tipo": f["tipo"], "monto": f["monto"], "moneda": f["moneda"], "canal": f["canal"],
            "comercio": f["comercio"], "ubicacion": f["ubicacion"], "reglas": [], "motivos": [], "puntaje": 0.0,
        })
        h["reglas"].append(regla)
        h["motivos"].append(motivo)
        h["puntaje"] = round(h["puntaje"] + puntos, 2)

    for f in ventana:
        monto = _en_gtq(f["monto"], f["moneda"], tipo_cambio)
        if desv > 0:
            z = (monto - media) / desv
            if z >= umbral_z:
                veces = monto / media if media else 0
                marcar(f, "monto", f"Monto Q{monto:,.2f} es {veces:.1f} veces su promedio (Q{media:,.2f}); "
                                   f"z = {z:.1f}", min(z / umbral_z, 4))
        hora = datetime.fromisoformat(f["fecha_hora"]).hour
        if h_ini <= hora <= h_fin and pct_nocturno < 0.05:
            marcar(f, "horario", f"Operación a las {f['fecha_hora'][11:16]}; solo el {pct_nocturno:.0%} de su "
                                 f"historial es de madrugada", 1)
        if ciudades_base and f["ubicacion"] not in ciudades_base:
            marcar(f, "ubicacion", f"Ubicación «{f['ubicacion']}» nunca vista en su historial "
                                   f"({', '.join(sorted(ciudades_base))})", 1.5)

    # Ráfagas: ventana deslizante sobre operaciones salientes del periodo.
    rafagas = []
    i = 0
    tiempos = [datetime.fromisoformat(f["fecha_hora"]) for f in ventana]
    while i < len(ventana):
        j = i
        while j + 1 < len(ventana) and tiempos[j + 1] - tiempos[i] <= timedelta(minutes=rafaga_minutos):
            j += 1
        grupo = ventana[i:j + 1]
        if len(grupo) >= rafaga_min:
            total = sum(_en_gtq(f["monto"], f["moneda"], tipo_cambio) for f in grupo)
            minutos = (tiempos[j] - tiempos[i]).total_seconds() / 60
            motivo = (f"Ráfaga de {len(grupo)} operaciones en {minutos:.0f} minutos por Q{total:,.2f} en total")
            rafagas.append({"desde": grupo[0]["fecha_hora"], "hasta": grupo[-1]["fecha_hora"],
                            "operaciones": len(grupo), "total_gtq": round(total, 2), "motivo": motivo,
                            "transacciones": [f["id"] for f in grupo]})
            for f in grupo:
                marcar(f, "frecuencia", motivo, 1)
            i = j + 1
        else:
            i += 1

    lista = sorted(hallazgos.values(), key=lambda h: (-h["puntaje"], h["fecha_hora"]))
    return {
        "cliente_id": cliente_id,
        "periodo": {"desde": desde.isoformat(" "), "hasta": ahora.isoformat(" "), "dias": dias},
        "linea_base": {
            "operaciones": len(base), "promedio_gtq": round(media, 2), "desviacion_gtq": round(desv, 2),
            "pct_madrugada": round(pct_nocturno, 3), "ciudades": sorted(ciudades_base),
        },
        "operaciones_revisadas": len(ventana),
        "total_hallazgos": len(lista),
        "hallazgos": lista,
        "rafagas": rafagas,
        "reglas": {"umbral_z": umbral_z, "horario_madrugada": f"{h_ini:02d}:00-{h_fin:02d}:59",
                   "rafaga": f">= {rafaga_min} operaciones en {rafaga_minutos} min"},
    }
