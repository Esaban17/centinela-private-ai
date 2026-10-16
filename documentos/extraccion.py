"""Imagen de un documento → modelo de visión local → campos JSON validados con Pydantic.

La función central es `extraer(llamar, imagen, nombre)`. `llamar` es cualquier función asíncrona que envía
mensajes + imágenes al modelo de visión y devuelve el texto de la respuesta; así la usan igual:

- el orquestador (`orchestrator/vision.py`), con su propio cliente de Ollama que registra el «chip» y los tokens;
- la línea de comandos y las pruebas (`llamador_ollama`, con `comun.ollama_http.ClienteOllama`).

Flujo: (1) si no se indica el tipo, el modelo de visión clasifica el documento; (2) extrae los campos con el
JSON Schema del tipo como salida estructurada; (3) Pydantic valida y normaliza; si falla, se reintenta una vez
mostrándole al modelo el error.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from documentos.modelos import ESQUEMAS, NOMBRES, Clasificacion, esquema_json, validar_campos

# (mensajes, imágenes PNG en bytes, JSON Schema o None) → texto de la respuesta del modelo
LlamarVision = Callable[[list[dict[str, Any]], list[bytes], dict[str, Any] | None], Awaitable[str]]

SISTEMA = (
    "Usted es un lector de documentos del Banco Atitlán (banco ficticio, datos de demostración). "
    "Transcribe EXACTAMENTE lo que está escrito en la imagen: no corrija nombres, montos ni números de cuenta, "
    "aunque parezcan errores, porque luego se validan contra la base. Si un campo no aparece o no se lee, use "
    "null. Los montos van como números sin símbolo de moneda ni comas de miles. Las fechas en formato AAAA-MM-DD. "
    "Responda solo con el JSON pedido."
)

PISTAS = {
    "boleta_deposito": ("boleta", "deposito", "depósito"),
    "solicitud_credito": ("solicitud", "credito", "crédito"),
    "factura": ("factura",),
}


class ErrorExtraccion(RuntimeError):
    """El documento no se pudo leer o el modelo no devolvió campos válidos."""


@dataclass
class Extraccion:
    """Resultado de la visión. Coincide con el gancho `Extraccion` del contrato de eventos (§5)."""

    tipo_documento: str
    campos: dict[str, Any]
    modelo: str
    advertencias: list[str] = field(default_factory=list)
    duracion_ms: int = 0
    intentos: int = 1

    def a_dict(self) -> dict[str, Any]:
        return {"tipo_documento": self.tipo_documento, "campos": self.campos, "modelo": self.modelo,
                "advertencias": self.advertencias, "duracion_ms": self.duracion_ms, "intentos": self.intentos}


def preparar_imagen(datos: bytes, max_lado: int = 1600) -> bytes:
    """Convierte a PNG RGB y reduce el lado mayor a `max_lado` px (menos tokens de imagen, misma legibilidad)."""
    from PIL import Image, UnidentifiedImageError

    if datos[:4] == b"%PDF":
        raise ErrorExtraccion("Los PDF no se leen directamente: exporte la página como PNG o JPG.")
    try:
        img = Image.open(io.BytesIO(datos))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ErrorExtraccion(f"El archivo no es una imagen válida ({e}).") from e
    img = img.convert("RGB")
    if max(img.size) > max_lado:
        img.thumbnail((max_lado, max_lado))
    salida = io.BytesIO()
    img.save(salida, format="PNG", optimize=True)
    return salida.getvalue()


def tipo_por_nombre(nombre: str) -> str | None:
    n = (nombre or "").lower()
    for tipo, palabras in PISTAS.items():
        if any(p in n for p in palabras):
            return tipo
    return None


def _json(texto: str) -> dict[str, Any]:
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{"):]
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin < inicio:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(texto[inicio:fin + 1])


def _instruccion(tipo: str) -> str:
    campos = ", ".join(ESQUEMAS[tipo].model_fields)
    return (f"La imagen es una {NOMBRES[tipo]}. Extraiga estos campos: {campos}. "
            "Copie los nombres de personas exactamente como están escritos, con sus tildes.")


async def clasificar(llamar: LlamarVision, imagen: bytes) -> str:
    mensajes = [
        {"role": "system", "content": SISTEMA},
        {"role": "user", "content": "¿Qué tipo de documento es? boleta_deposito (boleta de depósito), "
                                    "solicitud_credito (solicitud de crédito), factura (factura de un comercio) "
                                    "u otro."},
    ]
    texto = await llamar(mensajes, [imagen], Clasificacion.model_json_schema())
    try:
        return Clasificacion.model_validate(_json(texto)).tipo_documento
    except (ValueError, ValidationError):
        return "otro"


async def extraer(llamar: LlamarVision, imagen: bytes, nombre: str = "", *, tipo: str | None = None,
                  modelo: str = "", max_lado: int = 1600, clasificar_con_modelo: bool = True) -> Extraccion:
    """Extrae y valida los campos de un documento. Lanza `ErrorExtraccion` si no lo logra."""
    t0 = time.perf_counter()
    png = preparar_imagen(imagen, max_lado)
    advertencias: list[str] = []
    pista = tipo_por_nombre(nombre)

    if tipo is None:
        tipo = await clasificar(llamar, png) if clasificar_con_modelo else "otro"
        if tipo == "otro":
            if pista is None:
                raise ErrorExtraccion("No reconocí el tipo de documento. Se aceptan boletas de depósito, "
                                      "solicitudes de crédito y facturas.")
            if clasificar_con_modelo:
                advertencias.append(f"El modelo no reconoció el tipo; se usó el nombre del archivo ({NOMBRES[pista]}).")
            tipo = pista
        elif pista and pista != tipo:
            advertencias.append(f"El nombre del archivo sugiere {NOMBRES[pista]}, pero el modelo lo leyó como "
                                f"{NOMBRES[tipo]}.")
    elif tipo not in ESQUEMAS:
        raise ErrorExtraccion(f"Tipo de documento desconocido: «{tipo}».")

    mensajes: list[dict[str, Any]] = [
        {"role": "system", "content": SISTEMA},
        {"role": "user", "content": _instruccion(tipo)},
    ]
    esquema = esquema_json(tipo)
    error = ""
    for intento in (1, 2):
        texto = await llamar(mensajes, [png], esquema)
        try:
            doc = validar_campos(tipo, _json(texto))
        except (ValueError, ValidationError) as e:
            error = str(e)[:600]
            mensajes = [*mensajes, {"role": "assistant", "content": texto[:2000]},
                        {"role": "user", "content": f"El JSON no es válido: {error}. Corríjalo y responda solo "
                                                    "con el JSON."}]
            continue
        campos = doc.model_dump(mode="json")
        faltan = [c for c, info in ESQUEMAS[tipo].model_fields.items() if campos.get(c) is None and info.is_required()]
        faltan += [c for c in ("fecha",) if c in campos and campos[c] is None]
        if faltan:
            advertencias.append("No se leyeron: " + ", ".join(faltan) + ".")
        if intento == 2:
            advertencias.append("El primer intento devolvió un JSON inválido; se corrigió en el segundo.")
        return Extraccion(tipo, campos, modelo, advertencias, int((time.perf_counter() - t0) * 1000), intento)
    raise ErrorExtraccion(f"El modelo de visión no devolvió campos válidos tras 2 intentos: {error}")


def llamador_ollama(cliente: Any, modelo: str, opciones: dict[str, Any] | None = None) -> LlamarVision:
    """Adapta `comun.ollama_http.ClienteOllama` (síncrono) a `LlamarVision`."""
    import base64

    async def llamar(mensajes: list[dict[str, Any]], imagenes: list[bytes], formato: dict[str, Any] | None) -> str:
        msgs = [dict(m) for m in mensajes]
        primer_usuario = next(i for i, m in enumerate(msgs) if m["role"] == "user")
        msgs[primer_usuario]["images"] = [base64.b64encode(i).decode() for i in imagenes]
        r = await asyncio.to_thread(cliente.chat, modelo, msgs, formato=formato,
                                    opciones={"temperature": 0, **(opciones or {})})
        return r.get("message", {}).get("content", "")

    return llamar
