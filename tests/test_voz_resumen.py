"""Resumen hablado: campo del agente, modelo rápido o recorte."""

import asyncio

from orchestrator.voz_local.opciones import OpcionesVoz
from orchestrator.voz_local.resumen import extraer_campo_resumen, recortar_para_voz, resumen_hablado

RESPUESTA = ("La cliente Lucía Cojtí tiene 6 movimientos inusuales este mes. El mayor fue de Q18,500.00 a las "
             "02:14 a. m. desde Zacapa, muy por encima de su promedio. Además hubo una ráfaga de 4 "
             "transferencias en 20 minutos.\n\n| Fecha | Monto |\n|---|---|\n| 2026-11-18 | Q18,500.00 |")


def test_recorte_por_palabras():
    r = recortar_para_voz(RESPUESTA, 25)
    assert r.startswith("La cliente Lucía Cojtí tiene 6 movimientos") and "|" not in r
    assert len(r.split()) <= 25


def test_recorte_primera_oracion_larga():
    r = recortar_para_voz("uno " * 100 + ".", 10)
    assert len(r.split()) == 10 and r.endswith(".")


async def test_prefiere_el_campo_del_modelo():
    r = await resumen_hablado(RESPUESTA, OpcionesVoz(), resumen_del_modelo="Hay 6 movimientos raros.")
    assert r.origen == "campo" and r.texto == "Hay 6 movimientos raros."


async def test_usa_el_modelo_rapido():
    prompts = []

    async def generar(prompt):
        prompts.append(prompt)
        return "Encontré **6 movimientos** inusuales; el mayor, de Q18,500.00."

    r = await resumen_hablado(RESPUESTA, OpcionesVoz(max_palabras_resumen=30), generar=generar)
    assert r.origen == "modelo_rapido"
    assert r.texto == "Encontré 6 movimientos inusuales; el mayor, de 18500 quetzales."
    assert "30 palabras" in prompts[0] and RESPUESTA in prompts[0]


async def test_si_el_modelo_falla_o_tarda_recorta():
    async def lento(prompt):
        await asyncio.sleep(1)
        return "tarde"

    async def roto(prompt):
        raise ConnectionError("ollama caído")

    for generar in (lento, roto):
        r = await resumen_hablado(RESPUESTA, OpcionesVoz(), generar=generar, timeout_s=0.05)
        assert r.origen == "recorte" and r.texto.startswith("La cliente")


def test_extraer_campo_json():
    assert extraer_campo_resumen('{"respuesta": "x", "resumen_hablado": "Todo en orden."}') == "Todo en orden."
    assert extraer_campo_resumen('```json\n{"resumen_hablado": "Sí."}\n```') == "Sí."
    assert extraer_campo_resumen("Texto normal.") is None
    assert extraer_campo_resumen('{"resumen_hablado": ""}') is None
