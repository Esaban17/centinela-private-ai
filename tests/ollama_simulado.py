"""Ollama simulado para las pruebas: responde /api/chat en NDJSON como el real, con un guion de pasos."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

Paso = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]


def llamar(nombre: str, **args: Any) -> dict[str, Any]:
    return {"function": {"name": nombre, "arguments": args}}


class OllamaFalso:
    """`agente`: lista de pasos para el modelo agente, en orden. Cada paso es
    `{"contenido": "...", "llamadas": [llamar(...)]}` o una función que recibe los mensajes y lo devuelve."""

    def __init__(self, agente: list[Paso] | None = None, intencion: str = "cartera",
                 titulo: str = "Revisión de anomalías de Lucía Cojtí", resumen: str = "Todo en orden. Le aviso."):
        self.agente = list(agente or [])
        self.intencion, self.titulo, self.resumen = intencion, titulo, resumen
        self.peticiones: list[dict[str, Any]] = []
        self.caido = False
        self.demora_agente_s = 0.0
        self.pausa_entre_trozos_s = 0.0  # > 0 simula el streaming real (el texto llega de a poco)
        self.transport = httpx.MockTransport(self._manejar)

    def peticiones_de(self, modelo: str) -> list[dict[str, Any]]:
        return [p for p in self.peticiones if p["model"] == modelo]

    async def _manejar(self, request: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("conexión rechazada", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.30.0-falso"})
        cuerpo = json.loads(request.content)
        self.peticiones.append(cuerpo)
        fmt = cuerpo.get("format")
        if isinstance(fmt, dict):
            props = fmt.get("properties", {})
            datos = {"intencion": self.intencion} if "intencion" in props else {"titulo": self.titulo}
            return self._ndjson(json.dumps(datos, ensure_ascii=False), [], 40, 8)
        if "tools" not in cuerpo:  # resumen hablado (modelo rápido, texto libre)
            return self._ndjson(self.resumen, [], 120, 15)
        if self.demora_agente_s:
            await asyncio.sleep(self.demora_agente_s)
        if not self.agente:
            return self._ndjson("No tengo más pasos en el guion de prueba.", [], 100, 10)
        paso = self.agente.pop(0)
        if callable(paso):
            paso = paso(cuerpo["messages"])
        return self._ndjson(paso.get("contenido", ""), paso.get("llamadas", []), 900, 60)

    def _ndjson(self, texto: str, llamadas: list[dict[str, Any]], tokens_in: int, tokens_out: int) -> httpx.Response:
        lineas = []
        palabras = texto.split(" ") if texto else []
        for i, p in enumerate(palabras):
            lineas.append({"message": {"role": "assistant", "content": p + (" " if i < len(palabras) - 1 else "")},
                           "done": False})
        if llamadas:
            lineas.append({"message": {"role": "assistant", "content": "", "tool_calls": llamadas}, "done": False})
        lineas.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                       "prompt_eval_count": tokens_in, "eval_count": tokens_out, "eval_duration": 1_500_000_000})
        trozos = [(json.dumps(x, ensure_ascii=False) + "\n").encode() for x in lineas]
        cabeceras = {"content-type": "application/x-ndjson"}
        if not self.pausa_entre_trozos_s:
            return httpx.Response(200, content=b"".join(trozos), headers=cabeceras)

        async def flujo():
            for t in trozos:
                await asyncio.sleep(self.pausa_entre_trozos_s)
                yield t

        return httpx.Response(200, content=flujo(), headers=cabeceras)
