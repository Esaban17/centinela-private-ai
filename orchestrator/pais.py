"""Modelos y agentes de VMware Private AI Services (PAIS 3.0) para el orquestador.

`PaisLLM` tiene la misma interfaz que `orchestrator.ollama.Ollama` (`chat`, `modelo`, `version`, `cerrar`), así
que el agente, el modelo rápido y la visión no saben si hablan con Ollama o con PAIS. Traduce:

  - mensajes de la memoria (formato Ollama) → formato OpenAI: `tool_calls` con `arguments` en texto e `id`,
    mensajes `tool` con `tool_call_id`, imágenes como `image_url` en base64;
  - `formato` (JSON Schema) → `response_format: json_schema`;
  - el streaming SSE (`data: {...}`) → los mismos `RespuestaLLM` y `al_recibir(texto)`.

`AgentePAIS` llama a un agente del Agent Builder de PAIS (p. ej. agente-banco: MCP demo-banco + RAG
politicas-banco). Es el «modo PAIS» de la UI: PAIS decide qué herramientas usar y Centinela solo muestra la
respuesta. La API de agentes no informa qué herramientas usó el agente.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

from comun import pais as cpais
from orchestrator.ollama import Delta, LlamadaHerramienta, OllamaNoDisponible, RespuestaLLM, observador_actual

log = logging.getLogger("centinela.pais")


class PaisNoDisponible(OllamaNoDisponible):
    """PAIS no respondió o respondió con error (servicio caído, token inválido, modelo inexistente)."""


RECORDATORIO_HERRAMIENTAS = (
    "Arriba están los resultados de {nombres}. Responda ahora SOLO esta pregunta del ejecutivo: «{pregunta}». "
    "Use esos datos concretos: copie el "
    "dictamen, los montos, tasas, motivos y códigos tal como aparecen. No mencione herramientas, funciones ni el "
    "formato de la respuesta. No vuelva a llamar una herramienta que ya respondió.")


def texto_herramienta(contenido: Any) -> str:
    """Resultados de buscar_politicas (JSON con metadatos del motor) → texto con la cita y el fragmento. El 8B
    entiende mejor «[NR-005, sección 1] …» que un JSON con puntajes, modos y motores."""
    texto = str(contenido or "")
    if '"fragmentos"' not in texto:
        return texto
    try:
        datos = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return texto
    if not isinstance(datos, dict) or not isinstance(datos.get("fragmentos"), list):
        return texto
    if not datos["fragmentos"]:
        return str(datos.get("mensaje") or "No se encontraron fragmentos de política relacionados.")
    partes = ["Fragmentos de las políticas del banco (cite documento y sección):"]
    for f in datos["fragmentos"]:
        codigo = f"{f.get('codigo')} · " if f.get("codigo") else ""
        partes.append(f"[{codigo}{f.get('cita') or f.get('documento')}]\n{str(f.get('fragmento') or '').strip()}")
    return "\n\n".join(partes)


def a_openai(mensajes: list[dict[str, Any]], recordatorio: bool = False) -> list[dict[str, Any]]:
    """Mensajes de la memoria de Centinela (formato Ollama) → formato OpenAI.

    `recordatorio`: si la conversación termina en resultados de herramientas, se agrega un mensaje de usuario que
    pide comunicar los datos. Llama 3.1 8B, sin él, suele responder «La respuesta es el dictamen que devuelve la
    herramienta» en vez de dar el dictamen, o vuelve a llamar la misma herramienta."""
    salida: list[dict[str, Any]] = []
    pendientes: list[str] = []  # ids de tool_calls sin respuesta, en orden
    for m in mensajes:
        rol = m.get("role")
        if rol == "assistant" and m.get("tool_calls"):
            llamadas = []
            for i, tc in enumerate(m["tool_calls"]):
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                llamadas.append({"id": tc.get("id") or f"call_{len(salida)}_{i}", "type": "function",
                                 "function": {"name": fn.get("name", ""),
                                              "arguments": args if isinstance(args, str)
                                              else json.dumps(args or {}, ensure_ascii=False)}})
            pendientes = [c["id"] for c in llamadas]
            salida.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": llamadas})
        elif rol == "tool":
            tid = m.get("tool_call_id") or (pendientes.pop(0) if pendientes else f"call_{len(salida)}")
            if tid in pendientes:
                pendientes.remove(tid)
            salida.append({"role": "tool", "tool_call_id": tid, "content": texto_herramienta(m.get("content"))})
        elif m.get("images"):
            partes: list[dict[str, Any]] = [{"type": "text", "text": m.get("content") or ""}]
            partes += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in m["images"]]
            salida.append({"role": rol, "content": partes})
        else:
            salida.append({"role": rol, "content": m.get("content") or ""})
    if recordatorio and salida and salida[-1]["role"] == "tool":
        nombres: list[str] = []
        for m in reversed(mensajes):
            if m.get("role") != "tool":
                break
            n = str(m.get("tool_name") or "la herramienta")
            if n not in nombres:
                nombres.insert(0, n)
        pregunta = next((str(m.get("content") or "") for m in reversed(mensajes) if m.get("role") == "user"), "")
        salida.append({"role": "user", "content": RECORDATORIO_HERRAMIENTAS.format(
            nombres=", ".join(nombres), pregunta=pregunta.strip()[:300])})
    return salida


def formato_openai(formato: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if formato is None:
        return None
    if formato == "json":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": "salida", "strict": True, "schema": formato}}


def _args(texto: str) -> dict[str, Any]:
    if not texto.strip():
        return {}
    try:
        v = json.loads(texto)
        return v if isinstance(v, dict) else {"_crudo": texto}
    except json.JSONDecodeError:
        return {"_crudo": texto}


class _Cliente:
    """httpx asíncrono contra PAIS con el token en el encabezado y un reintento si responde 401."""

    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None,
                 token: cpais.TokenPAIS | None = None):
        self.cfg = cfg
        p = cpais.seccion(cfg)
        self.token = token or cpais.TokenPAIS(cfg)
        self.http = httpx.AsyncClient(base_url=cpais.url_openai(cfg), transport=transport, trust_env=False,
                                      verify=cpais.verificar_tls(cfg),
                                      timeout=httpx.Timeout(float(p.get("timeout_s", 300)), connect=10.0))

    async def _token(self, forzar: bool = False) -> str:
        try:
            return await asyncio.to_thread(self.token.obtener, forzar)
        except cpais.ErrorPAIS as e:
            raise PaisNoDisponible(str(e)) from e

    async def cerrar(self) -> None:
        await self.http.aclose()


class PaisLLM(_Cliente):
    proveedor = "pais"

    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None,
                 token: cpais.TokenPAIS | None = None):
        super().__init__(cfg, transport, token)
        p = cpais.seccion(cfg)
        self.stream = bool(p.get("stream", True))
        self.json_schema = bool(p.get("json_schema", True))
        self.recordatorio = bool(p.get("recordatorio_herramientas", True))

    def modelo(self, rol: str) -> str:
        return self.cfg["modelos"][rol]["nombre"]

    def _cuerpo(self, rol: str, mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]] | None,
                formato: dict[str, Any] | str | None, opciones: dict[str, Any] | None) -> dict[str, Any]:
        m = self.cfg["modelos"].get(rol, {})
        cuerpo: dict[str, Any] = {"model": self.modelo(rol), "messages": a_openai(mensajes, self.recordatorio),
                                "stream": self.stream}
        if self.stream:
            cuerpo["stream_options"] = {"include_usage": True}
        op = opciones or {}
        temperatura = op.get("temperature", m.get("temperatura"))
        if temperatura is not None:
            cuerpo["temperature"] = float(temperatura)
        if m.get("max_tokens"):
            cuerpo["max_tokens"] = int(m["max_tokens"])
        if herramientas:
            cuerpo["tools"] = herramientas
        if formato is not None:
            if self.json_schema:
                cuerpo["response_format"] = formato_openai(formato)
            # Siempre se pide también en el prompt: si PAIS ignora response_format, el modelo igual responde JSON.
            esquema = formato if isinstance(formato, dict) else {}
            pista = ("Responda SOLO con un objeto JSON válido, sin texto adicional"
                     + (f", que cumpla este JSON Schema: {json.dumps(esquema, ensure_ascii=False)}" if esquema else "")
                     + ".")
            # En el primer mensaje de sistema: varias plantillas (Llama 3.1) solo aceptan uno, al inicio.
            msgs = cuerpo["messages"]
            if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str):
                msgs[0] = {**msgs[0], "content": f"{msgs[0]['content']}\n\n{pista}"}
            else:
                msgs.insert(0, {"role": "system", "content": pista})
        return cuerpo

    async def chat(self, rol: str, mensajes: list[dict[str, Any]], *, herramientas: list[dict[str, Any]] | None = None,
                   formato: dict[str, Any] | str | None = None, imagenes: list[bytes | str] | None = None,
                   opciones: dict[str, Any] | None = None, al_recibir: Delta | None = None,
                   tarea: str = "") -> RespuestaLLM:
        import base64

        modelo = self.modelo(rol)
        if imagenes:
            b64 = [i if isinstance(i, str) else base64.b64encode(i).decode("ascii") for i in imagenes]
            idx = max((i for i, m in enumerate(mensajes) if m.get("role") == "user"), default=len(mensajes) - 1)
            mensajes = [{**m, "images": b64} if i == idx else m for i, m in enumerate(mensajes)]
        cuerpo = self._cuerpo(rol, mensajes, herramientas, formato, opciones)

        obs = observador_actual.get()
        paso = obs.inicio(rol, modelo, tarea) if obs else 0
        r = RespuestaLLM(rol=rol, modelo=modelo)
        t0 = time.perf_counter()
        try:
            for intento in (1, 2):
                encabezados = {"Authorization": f"Bearer {await self._token(forzar=intento == 2)}"}
                try:
                    if self.stream:
                        estado = await self._chat_stream(cuerpo, encabezados, r, t0, al_recibir)
                    else:
                        estado = await self._chat_simple(cuerpo, encabezados, r, t0, al_recibir)
                except httpx.TransportError as e:
                    raise PaisNoDisponible(f"No se pudo conectar con PAIS en {self.http.base_url}: {e}") from e
                if estado == 401 and intento == 1 and not self.token.estatico:
                    continue
                if estado == 401:
                    raise PaisNoDisponible("PAIS rechazó el token (401). Revise el usuario de servicio en Keycloak.")
                break
        finally:
            r.duracion_ms = int((time.perf_counter() - t0) * 1000)
            if not r.generacion_ms and r.ttft_ms is not None:
                r.generacion_ms = max(0, r.duracion_ms - r.ttft_ms)
            if obs:
                obs.fin(paso, r)
        log.info("llm · pais · %s · %s · %d ms · %d→%d tokens · %d herramientas", rol, modelo, r.duracion_ms,
                 r.tokens_entrada, r.tokens_salida, len(r.llamadas))
        return r

    async def _emitir(self, texto: str, r: RespuestaLLM, t0: float, al_recibir: Delta | None) -> None:
        if not texto:
            return
        if r.ttft_ms is None:
            r.ttft_ms = int((time.perf_counter() - t0) * 1000)
        r.contenido += texto
        if al_recibir:
            res = al_recibir(texto)
            if res is not None:
                await res

    def _uso(self, uso: dict[str, Any] | None, r: RespuestaLLM) -> None:
        if uso:
            r.tokens_entrada = int(uso.get("prompt_tokens") or 0)
            r.tokens_salida = int(uso.get("completion_tokens") or 0)

    async def _chat_simple(self, cuerpo: dict[str, Any], encabezados: dict[str, str], r: RespuestaLLM, t0: float,
                           al_recibir: Delta | None) -> int:
        resp = await self.http.post("/chat/completions", json=cuerpo, headers=encabezados)
        if resp.status_code == 401:
            return 401
        if resp.status_code != 200:
            raise PaisNoDisponible(f"PAIS respondió {resp.status_code} para {cuerpo['model']}: {resp.text[:300]}")
        if "text/event-stream" in resp.headers.get("content-type", ""):  # pidió JSON y llegó SSE
            async def lineas():
                for ln in resp.text.splitlines():
                    yield ln
            await self._consumir_sse(lineas(), cuerpo, r, t0, al_recibir)
            return 200
        try:
            j = resp.json()
        except ValueError as e:
            raise PaisNoDisponible(f"PAIS devolvió una respuesta que no es JSON: {resp.text[:200]}") from e
        msg = ((j.get("choices") or [{}])[0]).get("message") or {}
        await self._emitir(msg.get("content") or "", r, t0, al_recibir)
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            r.llamadas.append(LlamadaHerramienta(fn.get("name", ""), args if isinstance(args, dict)
                                                 else _args(args or ""), tc.get("id")))
        self._uso(j.get("usage"), r)
        return 200

    async def _chat_stream(self, cuerpo: dict[str, Any], encabezados: dict[str, str], r: RespuestaLLM, t0: float,
                           al_recibir: Delta | None) -> int:
        async with self.http.stream("POST", "/chat/completions", json=cuerpo,
                                    headers={**encabezados, "Accept": "text/event-stream"}) as resp:
            if resp.status_code == 401:
                await resp.aread()
                return 401
            if resp.status_code != 200:
                detalle = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise PaisNoDisponible(f"PAIS respondió {resp.status_code} para {cuerpo['model']}: {detalle}")
            if "text/event-stream" not in resp.headers.get("content-type", "") and \
                    "json" in resp.headers.get("content-type", ""):
                # PAIS ignoró stream=true y devolvió la respuesta entera.
                j = json.loads(await resp.aread())
                msg = ((j.get("choices") or [{}])[0]).get("message") or {}
                await self._emitir(msg.get("content") or "", r, t0, al_recibir)
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    r.llamadas.append(LlamadaHerramienta(fn.get("name", ""), _args(fn.get("arguments") or ""),
                                                         tc.get("id")))
                self._uso(j.get("usage"), r)
                return 200
            await self._consumir_sse(resp.aiter_lines(), cuerpo, r, t0, al_recibir)
        return 200

    async def _consumir_sse(self, lineas: Any, cuerpo: dict[str, Any], r: RespuestaLLM, t0: float,
                            al_recibir: Delta | None) -> None:
        llamadas: dict[int, dict[str, Any]] = {}
        async for linea in lineas:
            linea = linea.strip()
            if not linea.startswith("data:"):
                continue
            dato = linea[5:].strip()
            if dato == "[DONE]":
                break
            try:
                trozo = json.loads(dato)
            except json.JSONDecodeError:
                continue
            if trozo.get("error"):
                raise PaisNoDisponible(f"PAIS ({cuerpo['model']}): {trozo['error']}")
            self._uso(trozo.get("usage"), r)
            for ch in trozo.get("choices") or []:
                delta = ch.get("delta") or ch.get("message") or {}
                await self._emitir(delta.get("content") or "", r, t0, al_recibir)
                for tc in delta.get("tool_calls") or []:
                    i = int(tc.get("index", len(llamadas)))
                    acc = llamadas.setdefault(i, {"id": None, "nombre": "", "args": ""})
                    acc["id"] = tc.get("id") or acc["id"]
                    fn = tc.get("function") or {}
                    acc["nombre"] += fn.get("name") or ""
                    a = fn.get("arguments")
                    acc["args"] += json.dumps(a) if isinstance(a, dict) else (a or "")
        for i in sorted(llamadas):
            acc = llamadas[i]
            r.llamadas.append(LlamadaHerramienta(acc["nombre"], _args(acc["args"]), acc["id"]))

    async def version(self) -> str | None:
        try:
            encabezados = {"Authorization": f"Bearer {await self._token()}"}
            resp = await self.http.get("/models", headers=encabezados, timeout=5.0)
            if resp.status_code != 200:
                return None
            ids = [m.get("id") for m in resp.json().get("data") or []]
            return f"PAIS · {', '.join(i for i in ids if i)}" if ids else "PAIS"
        except (httpx.HTTPError, ValueError, PaisNoDisponible):
            return None


class AgentePAIS(_Cliente):
    """Un agente del Agent Builder de PAIS (POST /agents/{id}/chat/completions, sin `model`)."""

    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None,
                 token: cpais.TokenPAIS | None = None):
        super().__init__(cfg, transport, token)
        ag = cpais.seccion(cfg).get("agente") or {}
        self.agente_id = str(ag.get("id") or "")
        self.nombre = str(ag.get("nombre") or "agente PAIS")
        self.historial = int(ag.get("historial_mensajes", 1))
        self.temperatura = ag.get("temperatura", 0.01)
        self.sesiones: dict[str, str] = {}  # sesión de Centinela → session_id de PAIS

    @property
    def configurado(self) -> bool:
        return bool(self.agente_id)

    async def preguntar(self, mensajes: list[dict[str, Any]], sesion: str | None = None) -> dict[str, Any]:
        """Envía los últimos `historial_mensajes` mensajes (user/assistant). Devuelve texto, uso y segundos.
        Con Llama 3.1 8B conviene historial 1: con más, tiende a repetir la respuesta anterior."""
        if not self.agente_id:
            raise PaisNoDisponible("Falta pais.agente.id en config.pais.yaml (el ID de agente-banco).")
        msgs = [{"role": m["role"], "content": m.get("content") or ""} for m in mensajes
                if m.get("role") in ("user", "assistant") and m.get("content")]
        msgs = msgs[-max(1, self.historial):]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        cuerpo: dict[str, Any] = {"messages": msgs, "stream": False}
        if self.temperatura is not None:
            cuerpo["temperature"] = float(self.temperatura)
        t0 = time.perf_counter()
        ruta = f"/agents/{self.agente_id}/chat/completions"
        for intento in (1, 2):
            try:
                resp = await self.http.post(ruta, json=cuerpo, headers={
                    "Authorization": f"Bearer {await self._token(forzar=intento == 2)}", "Accept": "application/json"})
            except httpx.TimeoutException as e:
                raise PaisNoDisponible("El agente de PAIS tardó demasiado en responder.") from e
            except httpx.TransportError as e:
                raise PaisNoDisponible(f"No se pudo conectar con PAIS: {e}") from e
            if resp.status_code == 401 and intento == 1 and not self.token.estatico:
                continue
            break
        if resp.status_code != 200:
            raise PaisNoDisponible(f"El agente de PAIS respondió {resp.status_code}: {resp.text[:300]}")
        j = resp.json()
        if sesion and j.get("session_id"):
            self.sesiones[sesion] = j["session_id"]
        texto = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        uso = j.get("usage") or {}
        return {"texto": texto.strip(), "uso": uso, "duracion_ms": int((time.perf_counter() - t0) * 1000),
                "session_id": j.get("session_id") or str(uuid.uuid4())}


def crear_llm(cfg: dict[str, Any]) -> Any:
    """Ollama local o PAIS, según `proveedor` en la configuración."""
    if cpais.es_pais(cfg):
        return PaisLLM(cfg)
    from orchestrator.ollama import Ollama

    return Ollama(cfg)
