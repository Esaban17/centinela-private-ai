"""Cliente de Ollama (localhost) con métricas por llamada.

Habla con `/api/chat` en streaming (NDJSON). Cada llamada se reporta al observador del turno en curso
(un `contextvar`), que emite el «chip» del modelo y acumula tokens. Así cualquier componente que use
este cliente (agente, modelo rápido, visión) aparece solo en la UI y en el panel.

`trust_env=False`: el cliente ignora HTTP(S)_PROXY del entorno; solo habla con la URL de config.yaml.
"""

from __future__ import annotations

import base64
import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("centinela.ollama")


class OllamaNoDisponible(RuntimeError):
    """No se pudo hablar con Ollama (apagado, modelo inexistente o error del servidor)."""


@dataclass
class LlamadaHerramienta:
    nombre: str
    argumentos: dict[str, Any]
    id: str | None = None


@dataclass
class RespuestaLLM:
    rol: str
    modelo: str
    contenido: str = ""
    llamadas: list[LlamadaHerramienta] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    ttft_ms: int | None = None
    duracion_ms: int = 0
    generacion_ms: int = 0  # eval_duration de Ollama

    @property
    def tokens_por_s(self) -> float:
        return round(self.tokens_salida / (self.generacion_ms / 1000), 1) if self.generacion_ms else 0.0

    def mensaje(self) -> dict[str, Any]:
        """El mensaje del asistente tal como se guarda en la memoria de la conversación."""
        m: dict[str, Any] = {"role": "assistant", "content": self.contenido}
        if self.llamadas:
            m["tool_calls"] = [{"function": {"name": c.nombre, "arguments": c.argumentos},
                                **({"id": c.id} if c.id else {})} for c in self.llamadas]
        return m


class Observador(Protocol):
    def inicio(self, rol: str, modelo: str, tarea: str) -> int: ...

    def fin(self, paso: int, r: RespuestaLLM) -> None: ...


observador_actual: contextvars.ContextVar[Observador | None] = contextvars.ContextVar("observador", default=None)

Delta = Callable[[str], Awaitable[None] | None]


class Ollama:
    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        o = cfg["ollama"]
        self.keep_alive = o.get("keep_alive", -1)
        self.http = httpx.AsyncClient(base_url=o["url"], transport=transport, trust_env=False,
                                      timeout=httpx.Timeout(None, connect=5.0))

    def modelo(self, rol: str) -> str:
        return self.cfg["modelos"][rol]["nombre"]

    def _opciones(self, rol: str) -> dict[str, Any]:
        m = self.cfg["modelos"].get(rol, {})
        op: dict[str, Any] = {}
        if m.get("num_ctx"):
            op["num_ctx"] = int(m["num_ctx"])
        if m.get("temperatura") is not None:
            op["temperature"] = float(m["temperatura"])
        return op

    async def chat(self, rol: str, mensajes: list[dict[str, Any]], *, herramientas: list[dict[str, Any]] | None = None,
                   formato: dict[str, Any] | str | None = None, imagenes: list[bytes | str] | None = None,
                   opciones: dict[str, Any] | None = None, al_recibir: Delta | None = None,
                   tarea: str = "") -> RespuestaLLM:
        """Una llamada a /api/chat. `imagenes` (bytes o base64) se adjuntan al último mensaje de la persona;
        `opciones` se suman a las de config.yaml (p. ej. temperature)."""
        modelo = self.modelo(rol)
        if imagenes:
            b64 = [i if isinstance(i, str) else base64.b64encode(i).decode("ascii") for i in imagenes]
            idx = max((i for i, m in enumerate(mensajes) if m.get("role") == "user"), default=len(mensajes) - 1)
            mensajes = [{**m, "images": b64} if i == idx else m for i, m in enumerate(mensajes)]
        cuerpo: dict[str, Any] = {"model": modelo, "messages": mensajes, "stream": True,
                                  "keep_alive": self.keep_alive, "options": {**self._opciones(rol), **(opciones or {})}}
        if herramientas:
            cuerpo["tools"] = herramientas
        if formato is not None:
            cuerpo["format"] = formato
        think = self.cfg["modelos"].get(rol, {}).get("think")
        if think is not None:
            cuerpo["think"] = think

        obs = observador_actual.get()
        paso = obs.inicio(rol, modelo, tarea) if obs else 0
        r = RespuestaLLM(rol=rol, modelo=modelo)
        t0 = time.perf_counter()
        try:
            async with self.http.stream("POST", "/api/chat", json=cuerpo) as resp:
                if resp.status_code != 200:
                    detalle = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise OllamaNoDisponible(f"Ollama respondió {resp.status_code} para {modelo}: {detalle}")
                async for linea in resp.aiter_lines():
                    if not linea.strip():
                        continue
                    trozo = json.loads(linea)
                    if "error" in trozo:
                        raise OllamaNoDisponible(f"Ollama ({modelo}): {trozo['error']}")
                    msg = trozo.get("message") or {}
                    texto = msg.get("content") or ""
                    if texto:
                        if r.ttft_ms is None:
                            r.ttft_ms = int((time.perf_counter() - t0) * 1000)
                        r.contenido += texto
                        if al_recibir:
                            res = al_recibir(texto)
                            if res is not None:
                                await res
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args) if args.strip() else {}
                            except json.JSONDecodeError:
                                args = {"_crudo": args}
                        r.llamadas.append(LlamadaHerramienta(fn.get("name", ""), args, tc.get("id")))
                    if trozo.get("done"):
                        r.tokens_entrada = int(trozo.get("prompt_eval_count") or 0)
                        r.tokens_salida = int(trozo.get("eval_count") or 0)
                        r.generacion_ms = int((trozo.get("eval_duration") or 0) / 1e6)
        except httpx.TransportError as e:
            raise OllamaNoDisponible(f"No se pudo conectar con Ollama en {self.http.base_url}: {e}") from e
        finally:
            r.duracion_ms = int((time.perf_counter() - t0) * 1000)
            if obs:
                obs.fin(paso, r)
        log.info("llm · %s · %s · %d ms · %d→%d tokens · %d herramientas", rol, modelo, r.duracion_ms,
                 r.tokens_entrada, r.tokens_salida, len(r.llamadas))
        return r

    async def version(self) -> str | None:
        try:
            r = await self.http.get("/api/version", timeout=2.0)
            return r.json().get("version") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def cerrar(self) -> None:
        await self.http.aclose()
