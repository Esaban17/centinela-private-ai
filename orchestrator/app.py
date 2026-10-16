"""API del orquestador (FastAPI): WebSocket, SSE y REST. Contrato en docs/CONTRATO_EVENTOS.md.

Solo escucha en 127.0.0.1 y sirve la UI desde web/ con assets locales.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from comun import config
from orchestrator import ganchos as mod_ganchos
from orchestrator.mcp_host import HostMCP, MCPNoDisponible, parametros_stdio
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador, TurnoEnCurso

log = logging.getLogger("centinela.api")

TIPOS_DOC = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".pdf": "application/pdf"}


class MensajeEntrada(BaseModel):
    texto: str = ""
    documento_id: str | None = None


class AprobacionEntrada(BaseModel):
    aprobar: bool
    comentario: str = ""


class Preferencias(BaseModel):
    hablar: bool


def crear_orquestador(cfg: dict[str, Any] | None = None) -> Orquestador:
    cfg = cfg or config.cargar()
    ollama = Ollama(cfg)
    host = HostMCP(parametros_stdio(cfg))
    return Orquestador(cfg, ollama, host, mod_ganchos.cargar(cfg, ollama))


def crear_app(orq: Orquestador | None = None) -> FastAPI:
    @asynccontextmanager
    async def ciclo(app: FastAPI):
        o = app.state.orq = orq or crear_orquestador()
        try:
            await o.host.conectar()
        except MCPNoDisponible as e:  # la API arranca igual; /api/salud lo muestra y se reintenta en el turno
            log.error("%s", e)
        # Whisper y Kokoro viven en este proceso: se cargan al arrancar, sin bloquear la API.
        precalentado = None
        if o.cfg.get("orquestador", {}).get("precalentar_al_arrancar", True):
            precalentado = asyncio.create_task(o.precalentar(), name="precalentar-voz")
        yield
        if precalentado is not None and not precalentado.done():
            precalentado.cancel()
        await o.cerrar()

    app = FastAPI(title="Centinela — orquestador (DEMO / DATOS FICTICIOS)", lifespan=ciclo,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    def o(request: Request | WebSocket) -> Orquestador:
        return request.app.state.orq

    def sesion(request: Request | WebSocket, sesion_id: str):
        try:
            return o(request).sesion(sesion_id)
        except ValueError:
            raise HTTPException(400, "sesion_id inválido") from None

    # ───────────── WebSocket ─────────────

    @app.websocket("/ws/{sesion_id}")
    async def ws(websocket: WebSocket, sesion_id: str, desde_seq: int | None = None):
        orq_ = o(websocket)
        try:
            s = orq_.sesion(sesion_id)
        except ValueError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cola = s.bus.suscribir(desde_seq)
        s.bus.emitir("sesion.lista", orq_.datos_sesion_lista())

        async def enviar() -> None:
            while True:
                await websocket.send_json(await cola.get())

        envio = asyncio.create_task(enviar())
        try:
            while True:
                try:
                    msg = await websocket.receive_json()
                except (json.JSONDecodeError, ValueError):
                    s.bus.emitir("error", {"codigo": "mensaje_invalido", "mensaje": "Mensaje no es JSON.",
                                           "etapa": None, "recuperable": True})
                    continue
                _atender_ws(orq_, s, msg if isinstance(msg, dict) else {})
        except WebSocketDisconnect:
            pass
        finally:
            envio.cancel()
            s.bus.desuscribir(cola)

    def _hablar(s: Any, hablar: bool) -> None:
        """Botón de silenciar: además de no hablar los próximos turnos, corta lo que está sonando."""
        s.hablar = hablar
        if not hablar and s.turno is not None and s.turno.locutor is not None:
            s.turno.locutor.cancelar()

    def _atender_ws(orq_: Orquestador, s: Any, msg: dict[str, Any]) -> None:
        tipo = msg.get("tipo")
        if tipo == "mensaje":
            try:
                orq_.iniciar_turno(s, str(msg.get("texto") or ""), "documento" if msg.get("documento_id") else "texto",
                                   msg.get("documento_id"))
            except TurnoEnCurso:
                pass
        elif tipo == "aprobacion":
            if not orq_.resolver_aprobacion(str(msg.get("aprobacion_id")), bool(msg.get("aprobar")),
                                            str(msg.get("comentario") or "")):
                s.bus.emitir("aviso", {"mensaje": "Esa aprobación ya no está pendiente.", "etapa": "aprobacion"})
        elif tipo == "cancelar":
            orq_.cancelar(s)
        elif tipo == "preferencias":
            if "hablar" in msg:
                _hablar(s, bool(msg["hablar"]))
        elif tipo == "ping":
            s.bus.emitir("pong", {})
        else:
            s.bus.emitir("error", {"codigo": "mensaje_invalido", "mensaje": f"Tipo de mensaje desconocido: {tipo}",
                                   "etapa": None, "recuperable": True})

    # ───────────── SSE ─────────────

    @app.get("/api/sesiones/{sesion_id}/eventos")
    async def sse(request: Request, sesion_id: str):
        s = sesion(request, sesion_id)
        ultimo = request.headers.get("last-event-id")
        desde = int(ultimo) if ultimo and ultimo.isdigit() else request.query_params.get("desde_seq")
        cola = s.bus.suscribir(int(desde) if desde is not None else None)
        if desde is None:
            s.bus.emitir("sesion.lista", o(request).datos_sesion_lista())

        async def flujo():
            try:
                while True:
                    try:
                        e = await asyncio.wait_for(cola.get(), 15)
                    except TimeoutError:
                        yield ": latido\n\n"
                        continue
                    yield f"id: {e['seq']}\nevent: {e['tipo']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            finally:
                s.bus.desuscribir(cola)

        return StreamingResponse(flujo(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ───────────── REST ─────────────

    @app.post("/api/sesiones/{sesion_id}/mensajes", status_code=202)
    async def mensaje(request: Request, sesion_id: str, cuerpo: MensajeEntrada):
        s = sesion(request, sesion_id)
        try:
            turno_id = o(request).iniciar_turno(s, cuerpo.texto, "documento" if cuerpo.documento_id else "texto",
                                                cuerpo.documento_id)
        except TurnoEnCurso:
            raise HTTPException(409, "turno_en_curso") from None
        return {"turno_id": turno_id}

    @app.post("/api/sesiones/{sesion_id}/audio", status_code=202)
    async def audio(request: Request, sesion_id: str, audio: UploadFile = File(...)):
        s = sesion(request, sesion_id)
        datos = await audio.read()
        formato = (audio.filename or "audio.webm").rsplit(".", 1)[-1].lower()
        try:
            turno_id = o(request).iniciar_turno(s, "", "voz", None, (datos, formato))
        except TurnoEnCurso:
            raise HTTPException(409, "turno_en_curso") from None
        return {"turno_id": turno_id}

    @app.post("/api/sesiones/{sesion_id}/cancelar")
    async def cancelar(request: Request, sesion_id: str):
        return {"ok": o(request).cancelar(sesion(request, sesion_id))}

    @app.post("/api/sesiones/{sesion_id}/preferencias")
    async def preferencias(request: Request, sesion_id: str, cuerpo: Preferencias):
        _hablar(sesion(request, sesion_id), cuerpo.hablar)
        return {"ok": True}

    @app.get("/api/sesiones/{sesion_id}/historial")
    async def historial(request: Request, sesion_id: str):
        return sesion(request, sesion_id).memoria.historial()

    @app.post("/api/aprobaciones/{aprobacion_id}")
    async def aprobacion(request: Request, aprobacion_id: str, cuerpo: AprobacionEntrada):
        if not o(request).resolver_aprobacion(aprobacion_id, cuerpo.aprobar, cuerpo.comentario):
            raise HTTPException(404, "La aprobación no existe o ya se resolvió.")
        return {"ok": True}

    @app.post("/api/documentos")
    async def subir_documento(request: Request, archivo: UploadFile = File(...)):
        nombre = archivo.filename or "documento"
        if "." + nombre.rsplit(".", 1)[-1].lower() not in TIPOS_DOC:
            raise HTTPException(415, "Formato no soportado (use PNG, JPG, WEBP o PDF).")
        doc_id = o(request).documentos.guardar(nombre, await archivo.read())
        return {"documento_id": doc_id, "nombre": nombre}

    @app.get("/api/documentos")
    async def documentos(request: Request):
        return o(request).documentos.listar()

    @app.get("/api/documentos/{documento_id}")
    async def documento(request: Request, documento_id: str):
        doc = o(request).documentos.obtener(documento_id)
        if doc is None:
            raise HTTPException(404, "Documento no encontrado.")
        nombre, datos = doc
        ext = "." + nombre.rsplit(".", 1)[-1].lower()
        return Response(datos, media_type=TIPOS_DOC.get(ext, "application/octet-stream"))

    @app.get("/api/audio/{audio_id}")
    async def obtener_audio(request: Request, audio_id: str):
        datos = o(request).audios.obtener(audio_id)
        if datos is None:
            raise HTTPException(404, "Audio no encontrado.")
        return Response(datos, media_type="audio/wav")

    @app.get("/api/auditoria")
    async def auditoria(request: Request, limite: int = 20):
        return o(request).auditoria(limite)

    @app.get("/api/salud")
    async def salud(request: Request):
        return await o(request).salud()

    @app.post("/api/precalentar")
    async def precalentar(request: Request):
        """Vuelve a cargar STT y TTS en el orquestador (lo llama `make warmup`)."""
        return await o(request).precalentar()

    @app.get("/api/guion")
    async def guion(request: Request):
        return o(request).cfg.get("guion", [])

    # ───────────── Panel de soberanía (Fase 5) ─────────────

    try:
        from orchestrator.soberania import crear_router
    except ModuleNotFoundError as e:
        if e.name != "orchestrator.soberania":
            raise
        log.info("panel de soberanía: módulo aún no integrado")
    else:
        app.include_router(crear_router(orq.cfg if orq is not None else config.cargar()))

    # ───────────── UI (Fase 5) ─────────────

    web = config.RAIZ / "web"
    if (web / "index.html").exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def inicio():
            return ("<!doctype html><meta charset='utf-8'><title>Centinela</title>"
                    "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem'>"
                    "<h1>Centinela — orquestador activo</h1><p>DEMO / DATOS FICTICIOS</p>"
                    "<p>La interfaz llega en la Fase 5. API: <a style='color:#8cf' href='/api/docs'>/api/docs</a>"
                    " · <a style='color:#8cf' href='/api/salud'>/api/salud</a></p></body>")

    @app.exception_handler(Exception)
    async def error_general(_request: Request, exc: Exception):
        log.exception("error en la API: %s", exc)
        return JSONResponse({"codigo": "interno", "mensaje": "Error interno del orquestador."}, status_code=500)

    return app
