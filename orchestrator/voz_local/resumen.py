"""Respuesta hablada más corta que la escrita.

Tres fuentes, en orden de preferencia:

1. `resumen_del_modelo`: el campo `resumen_hablado` si el agente ya lo devolvió.
2. `generar`: una función async que llama al modelo rápido con `PROMPT_RESUMEN_HABLADO` (el orquestador la pasa
   con su propio cliente de Ollama, así el chip y los tokens se registran solos).
3. Recorte: las primeras oraciones de la respuesta hasta `max_palabras_resumen`. Nunca falla.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .opciones import OpcionesVoz
from .texto import contar_palabras, normalizar_para_voz, partir_en_oraciones, tiene_contenido

log = logging.getLogger("centinela.voz.resumen")

PROMPT_RESUMEN_HABLADO = """Convierta la respuesta de abajo en un resumen para LEER EN VOZ ALTA a un ejecutivo del banco.
Reglas:
- Español de Guatemala, trato de usted, tono profesional y cálido.
- Máximo {max_palabras} palabras, en 1 a 3 oraciones completas.
- Sin listas, tablas, Markdown, emojis ni enlaces.
- Diga los montos redondeados y con su moneda (por ejemplo «unos 12 mil quetzales»).
- Nunca diga números de cuenta completos ni datos personales que no sean el nombre.
- No agregue nada que no esté en la respuesta. Si la respuesta dice que falta un dato, dígalo.
Responda solo con el resumen.

Respuesta:
{respuesta}"""

INSTRUCCION_CAMPO_RESUMEN = (
    "Además de la respuesta escrita, incluya un campo `resumen_hablado`: 1 a 3 oraciones (máximo {max_palabras} "
    "palabras) para leer en voz alta, sin listas, sin Markdown y sin números de cuenta completos.")


@dataclass
class ResumenHablado:
    texto: str
    origen: str                # campo | modelo_rapido | recorte
    latencia_s: float


def recortar_para_voz(texto: str, max_palabras: int) -> str:
    """Primeras oraciones hasta `max_palabras` (la primera siempre entra, recortada si hace falta)."""
    elegidas: list[str] = []
    total = 0
    for oracion in partir_en_oraciones(texto, min_caracteres=0):
        n = contar_palabras(oracion)
        if elegidas and total + n > max_palabras:
            break
        if not elegidas and n > max_palabras:
            palabras = oracion.split()[:max_palabras]
            return " ".join(palabras).rstrip(",;:") + "."
        elegidas.append(oracion)
        total += n
    return " ".join(elegidas)


def extraer_campo_resumen(texto: str) -> str | None:
    """Si la respuesta del agente es JSON con `resumen_hablado`, lo devuelve."""
    t = (texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    if not t.startswith("{"):
        return None
    try:
        valor = json.loads(t).get("resumen_hablado")
    except (ValueError, AttributeError):
        return None
    return valor if isinstance(valor, str) and valor.strip() else None


async def resumen_hablado(respuesta: str, op: OpcionesVoz, *, resumen_del_modelo: str | None = None,
                          generar: Callable[[str], Awaitable[str]] | None = None,
                          timeout_s: float = 6.0) -> ResumenHablado:
    t0 = time.perf_counter()
    maxp = op.max_palabras_resumen
    if resumen_del_modelo and tiene_contenido(resumen_del_modelo):
        return ResumenHablado(recortar_para_voz(resumen_del_modelo, maxp * 2), "campo", time.perf_counter() - t0)
    if generar is not None:
        try:
            prompt = PROMPT_RESUMEN_HABLADO.format(max_palabras=maxp, respuesta=respuesta)
            texto = await asyncio.wait_for(generar(prompt), timeout_s)
            texto = normalizar_para_voz(texto)
            if tiene_contenido(texto):
                # Margen: el modelo rápido a veces se pasa un poco; el doble es el tope duro.
                return ResumenHablado(recortar_para_voz(texto, maxp * 2), "modelo_rapido", time.perf_counter() - t0)
        except Exception as e:  # el resumen nunca debe tumbar el turno
            log.warning("Resumen hablado con el modelo rápido falló (%s); uso recorte.", e)
    return ResumenHablado(recortar_para_voz(respuesta, maxp), "recorte", time.perf_counter() - t0)
