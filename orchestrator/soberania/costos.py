"""Costo equivalente estimado frente a un modelo de API por token.

Los precios están en `config.yaml` (sección `costos`; fuente en docs/DECISIONES.md §7). No hay precios
quemados en el código: si no están configurados, se informa así y la UI no inventa cifras.
"""

from __future__ import annotations


def _num(valor) -> float | None:
    try:
        return None if valor is None or valor == "" else float(valor)
    except (TypeError, ValueError):
        return None


def costo_equivalente(tokens_entrada: int, tokens_salida: int, cfg_costos: dict | None) -> dict:
    cfg = cfg_costos or {}
    p_in = _num(cfg.get("precio_entrada_millon"))
    p_out = _num(cfg.get("precio_salida_millon"))
    tc = _num(cfg.get("tipo_cambio_gtq"))
    moneda = cfg.get("moneda") or "USD"

    res: dict = {
        "rotulo": "Estimación referencial",
        "referencia": cfg.get("referencia") or "",
        "moneda": moneda,
        "configurado": p_in is not None and p_out is not None,
        "costo_local_gtq": 0.0,   # costo marginal local: la inferencia corre en hardware propio
        "monto": None,
        "monto_gtq": None,
    }
    if not res["configurado"]:
        res["nota"] = "Configure costos.precio_entrada_millon y precio_salida_millon en config.yaml."
        return res

    monto = tokens_entrada / 1_000_000 * p_in + tokens_salida / 1_000_000 * p_out
    res["monto"] = round(monto, 6)
    if moneda.upper() == "GTQ":
        res["monto_gtq"] = round(monto, 4)
    elif tc is not None:
        res["monto_gtq"] = round(monto * tc, 4)
    return res
