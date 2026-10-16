"""Ollama simulado con `httpx.MockTransport`: las pruebas corren sin modelos ni red.

- `/api/embed`: vectores deterministas de bolsa de palabras (con hashing y raíces de 6 letras), así textos que
  comparten palabras quedan cerca, igual que con un modelo real pero sin descargar nada.
- `/api/chat`: responde con lo que la prueba programe en `respuestas` (lista FIFO de textos) o, si está vacía,
  con `por_defecto(cuerpo)`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable

import httpx

from comun.ollama_http import ClienteOllama
from mcp_server.db import sin_acentos

DIMS = 256


def vector_falso(texto: str) -> list[float]:
    v = [0.0] * DIMS
    for p in re.split(r"[^a-z0-9ñ]+", sin_acentos(texto)):
        if len(p) <= 2:
            continue
        h = int(hashlib.md5(p[:6].encode()).hexdigest(), 16)
        v[h % DIMS] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class OllamaFalso:
    def __init__(self) -> None:
        self.caido = False
        self.peticiones: list[tuple[str, dict[str, Any]]] = []
        self.respuestas: list[str] = []
        self.por_defecto: Callable[[dict[str, Any]], str] = lambda cuerpo: "{}"
        self.cliente = ClienteOllama("http://127.0.0.1:11434", transport=httpx.MockTransport(self._manejar))

    def _manejar(self, req: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(req.content or b"{}")
        self.peticiones.append((req.url.path, cuerpo))
        if self.caido:
            raise httpx.ConnectError("Ollama apagado (simulado)", request=req)
        if req.url.path == "/api/embed":
            entradas = cuerpo["input"] if isinstance(cuerpo["input"], list) else [cuerpo["input"]]
            return httpx.Response(200, json={"model": cuerpo["model"], "embeddings": [vector_falso(t) for t in entradas]})
        if req.url.path == "/api/chat":
            texto = self.respuestas.pop(0) if self.respuestas else self.por_defecto(cuerpo)
            return httpx.Response(200, json={"model": cuerpo["model"], "done": True, "eval_count": 42,
                                             "message": {"role": "assistant", "content": texto}})
        return httpx.Response(404, json={"error": "ruta no simulada"})

    def chats(self) -> list[dict[str, Any]]:
        return [c for ruta, c in self.peticiones if ruta == "/api/chat"]
