"""PAIS simulado para las pruebas: API OpenAI-compatible (chat en SSE o JSON, embeddings, modelos), API de agentes
y Keycloak, con httpx.MockTransport. Guarda cada petición para revisar qué mandó Centinela."""

from __future__ import annotations

import json
from typing import Any

import httpx

RUTA = "/api/v1/compatibility/openai/v1"


def llamada(nombre: str, **args: Any) -> dict[str, Any]:
    return {"nombre": nombre, "args": args}


class PaisFalso:
    def __init__(self, guion: list[dict[str, Any]] | None = None, *, stream: bool = True, token: str = "tk-1",
                 rechazar_primero: bool = False, agente_texto: str = "DICTAMEN: SUSPENDIDO"):
        self.guion = list(guion or [])
        self.stream = stream
        self.token = token
        self.rechazar_primero = rechazar_primero
        self.agente_texto = agente_texto
        self.peticiones: list[dict[str, Any]] = []
        self.tokens_emitidos = 0

    # ── Keycloak (síncrono) ──
    def _keycloak(self, req: httpx.Request) -> httpx.Response:
        self.tokens_emitidos += 1
        self.peticiones.append({"ruta": "keycloak", "cuerpo": req.content.decode()})
        return httpx.Response(200, json={"access_token": f"kc-{self.tokens_emitidos}", "expires_in": 300})

    @property
    def transport_kc(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._keycloak)

    # ── PAIS ──
    def _autorizado(self, req: httpx.Request) -> bool:
        auth = req.headers.get("authorization", "")
        if self.rechazar_primero:
            self.rechazar_primero = False
            return False
        return auth.startswith("Bearer ")

    def _manejar(self, req: httpx.Request) -> httpx.Response:
        ruta = req.url.path
        cuerpo = json.loads(req.content) if req.content else {}
        self.peticiones.append({"ruta": ruta, "cuerpo": cuerpo, "auth": req.headers.get("authorization")})
        if not self._autorizado(req):
            return httpx.Response(401, json={"error": "token inválido"})
        if ruta == f"{RUTA}/models":
            return httpx.Response(200, json={"data": [{"id": "llama-3.1-8b"}, {"id": "bge-m3"}]})
        if ruta == f"{RUTA}/embeddings":
            datos = [{"index": i, "embedding": [float(len(t)), 1.0, 0.0]} for i, t in enumerate(cuerpo["input"])]
            return httpx.Response(200, json={"data": list(reversed(datos))})
        if ruta.startswith(f"{RUTA}/agents/"):
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                      "content": self.agente_texto}}],
                                             "usage": {"prompt_tokens": 900, "completion_tokens": 40},
                                             "session_id": "pais-s1"})
        if ruta == f"{RUTA}/chat/completions":
            paso = self.guion.pop(0) if self.guion else {"contenido": "Listo."}
            return self._sse(paso) if (cuerpo.get("stream") and self.stream) else self._json(paso)
        return httpx.Response(404, json={"error": ruta})

    def _json(self, paso: dict[str, Any]) -> httpx.Response:
        msg: dict[str, Any] = {"role": "assistant", "content": paso.get("contenido", "")}
        if paso.get("llamadas"):
            msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": c["nombre"], "arguments": json.dumps(c["args"])}}
                                 for i, c in enumerate(paso["llamadas"])]
        return httpx.Response(200, json={"choices": [{"message": msg}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 10}})

    def _sse(self, paso: dict[str, Any]) -> httpx.Response:
        trozos = []
        texto = paso.get("contenido", "")
        for i in range(0, len(texto), 7):
            trozos.append({"choices": [{"index": 0, "delta": {"content": texto[i:i + 7]}}]})
        for i, c in enumerate(paso.get("llamadas") or []):
            args = json.dumps(c["args"], ensure_ascii=False)
            mitad = len(args) // 2  # los argumentos llegan partidos, como en llama.cpp
            trozos.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "id": f"call_{i}", "type": "function", "function": {"name": c["nombre"],
                                                                                "arguments": args[:mitad]}}]}}]})
            trozos.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "function": {"arguments": args[mitad:]}}]}}]})
        trozos.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 120, "completion_tokens": 12}})
        cuerpo = "".join(f"data: {json.dumps(t, ensure_ascii=False)}\n\n" for t in trozos) + "data: [DONE]\n\n"
        return httpx.Response(200, content=cuerpo.encode(), headers={"content-type": "text/event-stream"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._manejar)

    def chats(self) -> list[dict[str, Any]]:
        return [p["cuerpo"] for p in self.peticiones if p["ruta"].endswith("/chat/completions")
                and "/agents/" not in p["ruta"]]
