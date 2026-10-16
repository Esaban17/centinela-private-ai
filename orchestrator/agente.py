"""Loop del agente: mensaje → LLM con herramientas MCP → ejecución → resultados → respuesta final.

Enrutamiento multi-modelo visible (cada uso emite un chip `modelo.uso`):
  imagen → visión · intención, título de ticket y resumen hablado → rápido · razonamiento con herramientas → agente.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from comun.mascaras import enmascarar
from orchestrator import prompts
from orchestrator.ganchos import GanchoNoDisponible, disponible
from orchestrator.mcp_host import MCPNoDisponible, solicitante_actual
from orchestrator.ollama import OllamaNoDisponible, RespuestaLLM, observador_actual

if TYPE_CHECKING:
    from orchestrator.orquestador import Orquestador, Sesion

log = logging.getLogger("centinela.agente")

ETAPAS = ("stt", "vision", "intencion", "llm", "herramientas", "aprobacion", "resumen", "tts")


class ErrorTurno(Exception):
    def __init__(self, codigo: str, mensaje: str, etapa: str | None = None, recuperable: bool = True):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.etapa, self.recuperable = codigo, mensaje, etapa, recuperable


def oraciones(texto: str) -> list[str]:
    """Parte un texto en oraciones para sintetizar voz de a una."""
    limpio = re.sub(r"[*_`#>|]+", "", texto or "")
    limpio = re.sub(r"\s+", " ", limpio).strip()
    partes = re.split(r"(?<=[.!?…])\s+", limpio)
    return [p.strip() for p in partes if len(p.strip()) > 1]


class Turno:
    """Un turno de conversación. Es también el observador de Ollama y quien pide las aprobaciones."""

    def __init__(self, orq: Orquestador, sesion: Sesion, turno_id: str):
        self.orq, self.sesion, self.id = orq, sesion, turno_id
        self.cfg = orq.cfg
        oq = self.cfg["orquestador"]
        self.timeouts = {k: float(v) for k, v in oq["timeouts_s"].items()}
        self.paso = 0
        self.t0 = time.perf_counter()
        self.latencias = dict.fromkeys(ETAPAS, 0)
        self.ttft_ms: int | None = None
        self.tokens_entrada = self.tokens_salida = 0
        self.agente_tokens = 0
        self.agente_gen_ms = 0
        self.modelos: list[str] = []
        self.herramientas: list[str] = []
        self.iteraciones = 0
        self.llamada_actual: dict[str, Any] | None = None
        self.aprobaciones_pendientes: set[str] = set()
        self.citas: list[dict[str, Any]] = []
        self.locutor: Any = None  # habla mientras el agente escribe (voz.modo_habla: primeras_oraciones)
        self.audios_emitidos = 0

    # ───────────── eventos ─────────────

    def emitir(self, tipo: str, datos: dict[str, Any] | None = None) -> None:
        self.sesion.bus.emitir(tipo, datos, self.id)

    @contextmanager
    def etapa(self, nombre: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.latencias[nombre] += int((time.perf_counter() - t) * 1000)

    # ───────────── Observador de Ollama ─────────────

    def inicio(self, rol: str, modelo: str, tarea: str) -> int:
        self.paso += 1
        if modelo not in self.modelos:
            self.modelos.append(modelo)
        self.emitir("modelo.uso", {"paso": self.paso, "rol": rol, "modelo": modelo, "tarea": tarea})
        self.emitir("llm.inicio", {"paso": self.paso, "rol": rol, "modelo": modelo, "iteracion": self.iteraciones})
        return self.paso

    def fin(self, paso: int, r: RespuestaLLM) -> None:
        self.tokens_entrada += r.tokens_entrada
        self.tokens_salida += r.tokens_salida
        if r.rol == "agente":
            self.agente_tokens += r.tokens_salida
            self.agente_gen_ms += r.generacion_ms
        self.emitir("llm.fin", {"paso": paso, "rol": r.rol, "modelo": r.modelo, "iteracion": self.iteraciones,
                                "ttft_ms": r.ttft_ms, "duracion_ms": r.duracion_ms,
                                "tokens_entrada": r.tokens_entrada, "tokens_salida": r.tokens_salida,
                                "tokens_por_s": r.tokens_por_s,
                                "herramientas_pedidas": [c.nombre for c in r.llamadas]})

    # ───────────── Aprobación humana (la pide el servidor MCP por elicitation) ─────────────

    async def pedir_aprobacion(self, mensaje: str, esquema: dict[str, Any] | None) -> tuple[bool, str]:
        aprobacion_id = f"ap-{uuid.uuid4().hex[:8]}"
        llamada = self.llamada_actual or {}
        fut: asyncio.Future[tuple[bool, str, str]] = asyncio.get_running_loop().create_future()
        self.orq.aprobaciones[aprobacion_id] = (fut, self)
        self.aprobaciones_pendientes.add(aprobacion_id)
        espera = self.timeouts["aprobacion"]
        self.emitir("aprobacion.solicitada", {
            "aprobacion_id": aprobacion_id, "llamada_id": llamada.get("llamada_id"),
            "herramienta": llamada.get("nombre"), "mensaje": mensaje, "argumentos": llamada.get("argumentos", {}),
            "expira_en_s": int(espera)})
        log.info("aprobación solicitada · %s · %s", aprobacion_id, enmascarar(mensaje).replace("\n", " | "))
        t = time.perf_counter()
        try:
            aprobada, comentario, origen = await asyncio.wait_for(asyncio.shield(fut), espera)
        except TimeoutError:
            aprobada, comentario, origen = False, "tiempo agotado sin respuesta de la persona", "tiempo_agotado"
        finally:
            self.orq.aprobaciones.pop(aprobacion_id, None)
            self.aprobaciones_pendientes.discard(aprobacion_id)
            self.latencias["aprobacion"] += int((time.perf_counter() - t) * 1000)
        self.sesion.acumulado["aprobaciones"]["aprobadas" if aprobada else "rechazadas"] += 1
        self.emitir("aprobacion.resuelta", {"aprobacion_id": aprobacion_id, "llamada_id": llamada.get("llamada_id"),
                                            "aprobada": aprobada, "comentario": comentario, "origen": origen})
        log.info("aprobación %s · %s · %s", aprobacion_id, "APROBADA" if aprobada else "rechazada", origen)
        return aprobada, comentario

    def cancelar_aprobaciones(self) -> None:
        for ap in list(self.aprobaciones_pendientes):
            entrada = self.orq.aprobaciones.get(ap)
            if entrada and not entrada[0].done():
                entrada[0].set_result((False, "turno cancelado", "cancelado"))


class Agente:
    def __init__(self, orq: Orquestador):
        self.orq = orq
        self.cfg = orq.cfg

    def _sistema(self) -> str:
        fecha = prompts.FECHA.format(fecha=self.orq.fecha_demo) if self.orq.fecha_demo else ""
        return prompts.SISTEMA.format(banco=self.cfg["demo"]["banco"], fecha=fecha)

    async def _con_tiempo(self, coro: Any, etapa: str, turno: Turno) -> Any:
        try:
            return await asyncio.wait_for(coro, turno.timeouts.get(etapa, turno.timeouts["llm"]))
        except TimeoutError:
            raise ErrorTurno("tiempo_agotado", _MENSAJES_TIEMPO.get(etapa, "La operación tardó demasiado."),
                             etapa) from None

    # ───────────── punto de entrada ─────────────

    async def ejecutar(self, turno: Turno, texto: str, entrada: str, documento_id: str | None = None,
                       audio: tuple[bytes, str] | None = None) -> None:
        sesion = turno.sesion
        observador_actual.set(turno)
        solicitante_actual.set(turno)
        ok, cancelado, intencion_tarea, voz_tarea = False, False, None, None
        try:
            if audio is not None:
                texto = await self._transcribir(turno, *audio)
            turno.emitir("turno.inicio", {"entrada": entrada, "texto": texto, "documento_id": documento_id})
            log.info("turno %s · %s · %s", turno.id, entrada, enmascarar(texto)[:200])

            contenido_usuario = texto
            if documento_id:
                contenido_usuario = await self._leer_documento(turno, texto, documento_id)
            elif not texto.strip():
                raise ErrorTurno("mensaje_invalido", "No recibí ninguna pregunta. ¿En qué le puedo ayudar?")

            ruta = self.cfg["orquestador"].get("enrutamiento", {})
            if ruta.get("clasificar_intencion", True):
                intencion_tarea = asyncio.create_task(self._clasificar(turno, texto, documento_id))

            sint = self.orq.ganchos.sintetizador
            hablar = sesion.hablar and disponible(sint)
            modo = self.cfg.get("voz", {}).get("modo_habla", "resumen")
            if hablar and modo == "primeras_oraciones" and hasattr(sint, "locutor"):
                turno.locutor = sint.locutor()
                voz_tarea = asyncio.create_task(self._emitir_locutor(turno, turno.locutor))

            sesion.memoria.agregar({"role": "user", "content": contenido_usuario})
            final = await self._loop(turno)

            resumen = None
            if final and ruta.get("resumen_hablado", True) and turno.locutor is None:
                resumen = await self._resumen_hablado(turno, final)
            turno.emitir("respuesta.final", {"texto": final, "resumen_hablado": resumen, "citas": turno.citas})
            if turno.locutor is not None and voz_tarea is not None:
                turno.locutor.terminar()
                await self._con_tiempo(voz_tarea, "voz_total", turno)
            elif hablar:
                await self._hablar(turno, resumen or " ".join(oraciones(final)[:2]))
            ok = True
        except asyncio.CancelledError:
            cancelado = True
            turno.cancelar_aprobaciones()
        except ErrorTurno as e:
            turno.emitir("error", {"codigo": e.codigo, "mensaje": e.mensaje, "etapa": e.etapa,
                                   "recuperable": e.recuperable})
            log.warning("turno %s · %s · %s", turno.id, e.codigo, e.mensaje)
        except OllamaNoDisponible as e:
            log.error("Ollama: %s", e)
            turno.emitir("error", {"codigo": "ollama_no_disponible", "etapa": "llm", "recuperable": True,
                                   "mensaje": "No pude comunicarme con los modelos locales (Ollama). "
                                              "Revise que `ollama serve` esté corriendo."})
        except MCPNoDisponible as e:
            log.error("MCP: %s", e)
            turno.emitir("error", {"codigo": "mcp_no_disponible", "etapa": "herramienta", "recuperable": True,
                                   "mensaje": "El servidor de datos del banco no respondió. Intente de nuevo."})
        except Exception as e:  # nunca dejar la pantalla congelada
            log.exception("error inesperado en el turno %s", turno.id)
            turno.emitir("error", {"codigo": "interno", "etapa": None, "recuperable": True,
                                   "mensaje": f"Ocurrió un error inesperado ({type(e).__name__}). Intente de nuevo."})
        finally:
            if intencion_tarea is not None and not intencion_tarea.done():
                intencion_tarea.cancel()
            if voz_tarea is not None and not voz_tarea.done():
                turno.locutor.cancelar()
                voz_tarea.cancel()
            self._cerrar(turno, ok, cancelado)

    def _cerrar(self, turno: Turno, ok: bool, cancelado: bool) -> None:
        lat = dict(turno.latencias)
        lat["total"] = int((time.perf_counter() - turno.t0) * 1000)
        tps = round(turno.agente_tokens / (turno.agente_gen_ms / 1000), 1) if turno.agente_gen_ms else 0.0
        turno.emitir("turno.fin", {"ok": ok, "cancelado": cancelado, "iteraciones": turno.iteraciones, "metricas": {
            "latencias_ms": lat, "ttft_ms": turno.ttft_ms, "tokens_entrada": turno.tokens_entrada,
            "tokens_salida": turno.tokens_salida, "tokens_por_s": tps, "modelos": turno.modelos,
            "herramientas": turno.herramientas}})
        a = turno.sesion.acumulado
        a["turnos"] += 1
        a["tokens_entrada"] += turno.tokens_entrada
        a["tokens_salida"] += turno.tokens_salida
        a["herramientas_ejecutadas"] += len(turno.herramientas)
        for m in turno.modelos:
            if m not in a["modelos_usados"]:
                a["modelos_usados"].append(m)
        turno.sesion.bus.emitir("sesion.acumulado", dict(a))
        log.info("turno %s · fin · ok=%s · %d ms · %s", turno.id, ok, lat["total"], ", ".join(turno.herramientas))

    # ───────────── loop de herramientas ─────────────

    async def _loop(self, turno: Turno) -> str:
        sesion = turno.sesion
        oq = self.cfg["orquestador"]
        max_iter = int(oq.get("max_iteraciones", 6))
        herramientas = self.orq.host.para_ollama()

        async def delta(texto: str) -> None:
            if turno.ttft_ms is None:
                turno.ttft_ms = int((time.perf_counter() - turno.t0) * 1000)
            turno.emitir("llm.delta", {"paso": turno.paso, "texto": texto})
            if turno.locutor is not None:
                turno.locutor.alimentar(texto)

        while turno.iteraciones < max_iter:
            turno.iteraciones += 1
            with turno.etapa("llm"):
                r: RespuestaLLM = await self._con_tiempo(
                    self.orq.ollama.chat("agente", sesion.memoria.para_llm(self._sistema()), herramientas=herramientas,
                                         al_recibir=delta, tarea="Razonar y usar herramientas"), "llm", turno)
            sesion.memoria.agregar(r.mensaje())
            if not r.llamadas:
                return r.contenido.strip()
            for llamada in r.llamadas:
                resultado = await self._ejecutar_herramienta(turno, llamada.nombre, llamada.argumentos)
                msg = {"role": "tool", "tool_name": llamada.nombre, "content": resultado}
                if llamada.id:
                    msg["tool_call_id"] = llamada.id
                sesion.memoria.agregar(msg)
        raise ErrorTurno("max_iteraciones", "No logré completar la consulta en el número máximo de pasos. "
                                            "¿Puede reformularla o dividirla en partes?", "llm")

    async def _ejecutar_herramienta(self, turno: Turno, nombre: str, argumentos: dict[str, Any]) -> str:
        """Ejecuta una herramienta MCP y devuelve el texto que verá el modelo (los errores también le llegan,
        para que se corrija)."""
        oq = self.cfg["orquestador"]
        limite = int(oq.get("max_caracteres_resultado", 6000))
        llamada_id = f"{turno.id}-h{len(turno.herramientas) + 1}"
        herramienta = self.orq.host.herramientas.get(nombre)
        if herramienta is None:
            disponibles = ", ".join(self.orq.host.herramientas)
            return json.dumps({"error": f"La herramienta «{nombre}» no existe. Disponibles: {disponibles}."},
                              ensure_ascii=False)

        if nombre == "crear_ticket" and oq.get("enrutamiento", {}).get("titulo_ticket_rapido", True):
            argumentos = {**argumentos, "titulo": await self._titulo(turno, argumentos)}

        turno.herramientas.append(nombre)
        turno.llamada_actual = {"llamada_id": llamada_id, "nombre": nombre, "argumentos": argumentos}
        turno.emitir("herramienta.inicio", {"llamada_id": llamada_id, "paso": turno.paso, "nombre": nombre,
                                            "argumentos": argumentos, "escritura": herramienta.escritura})
        log.info("herramienta · %s · %s", nombre, json.dumps(enmascarar(argumentos), ensure_ascii=False))
        meta = {"usuario": self.orq.usuario, "modelo": self.orq.ollama.modelo("agente")}
        t = time.perf_counter()
        aprob_antes = turno.latencias["aprobacion"]
        timeout = self.cfg["orquestador"]["timeouts_s"]["herramienta"]
        if herramienta.escritura:
            timeout += self.cfg["orquestador"]["timeouts_s"]["aprobacion"]
        try:
            res = await self.orq.host.llamar(nombre, argumentos, meta, float(timeout), solicitante=turno)
            ok, datos, texto, resumen = res.ok, res.datos, res.texto, res.resumen()
        except TimeoutError:
            ok, datos = False, {"mensaje": f"La herramienta {nombre} tardó demasiado y se canceló."}
            texto, resumen = json.dumps(datos, ensure_ascii=False), datos["mensaje"]
        except MCPNoDisponible:
            raise
        except Exception as e:
            log.warning("herramienta %s falló: %s", nombre, e)
            ok, datos = False, {"mensaje": f"Error al ejecutar {nombre}: {e}"}
            texto, resumen = json.dumps(datos, ensure_ascii=False), datos["mensaje"]
        finally:
            turno.llamada_actual = None
        dur = int((time.perf_counter() - t) * 1000)
        turno.latencias["herramientas"] += max(0, dur - (turno.latencias["aprobacion"] - aprob_antes))
        turno.emitir("herramienta.fin", {"llamada_id": llamada_id, "nombre": nombre, "ok": ok, "duracion_ms": dur,
                                         "resumen": resumen, "resultado": datos})
        if nombre == "buscar_politicas" and isinstance(datos, dict) and datos.get("fragmentos"):
            citas = [{"documento": f.get("documento"), "seccion": f.get("seccion"),
                      "fragmento": f.get("fragmento") or f.get("texto"), "puntaje": f.get("puntaje")}
                     for f in datos["fragmentos"]]
            turno.citas.extend(citas)
            turno.emitir("citas", {"citas": citas})
        if len(texto) > limite:
            texto = texto[:limite] + " …[resultado recortado]"
        return texto

    # ───────────── modelo rápido ─────────────

    async def _rapido_json(self, turno: Turno, etapa: str, sistema: str, contenido: str, esquema: dict[str, Any],
                           tarea: str) -> dict[str, Any] | None:
        try:
            with turno.etapa(etapa):
                r = await asyncio.wait_for(self.orq.ollama.chat(
                    "rapido", [{"role": "system", "content": sistema}, {"role": "user", "content": contenido}],
                    formato=esquema, tarea=tarea), turno.timeouts.get(etapa, 10))
            return json.loads(r.contenido)
        except (TimeoutError, OllamaNoDisponible, json.JSONDecodeError) as e:
            log.warning("modelo rápido (%s) no respondió bien: %s", tarea, e)
            return None

    async def _clasificar(self, turno: Turno, texto: str, documento_id: str | None) -> None:
        t = time.perf_counter()
        if documento_id and not texto.strip():
            intencion, modelo = "documento", None
        else:
            r = await self._rapido_json(turno, "intencion", prompts.INTENCION, texto, prompts.ESQUEMA_INTENCION,
                                        "Clasificar intención")
            intencion = (r or {}).get("intencion")
            if intencion not in prompts.INTENCIONES:
                return
            modelo = self.orq.ollama.modelo("rapido")
        turno.emitir("intencion", {"intencion": intencion, "modelo": modelo,
                                   "duracion_ms": int((time.perf_counter() - t) * 1000)})

    async def _titulo(self, turno: Turno, argumentos: dict[str, Any]) -> str:
        original = str(argumentos.get("titulo") or "").strip()
        r = await self._rapido_json(turno, "resumen", prompts.TITULO.format(tipo=argumentos.get("tipo", "")),
                                    str(argumentos.get("detalle") or original), prompts.ESQUEMA_TITULO,
                                    "Titular el ticket")
        titulo = str((r or {}).get("titulo") or "").strip().strip('"').rstrip(".")
        return titulo[:90] if titulo else (original or "Ticket generado por Centinela")

    async def _resumen_hablado(self, turno: Turno, final: str) -> str | None:
        try:
            with turno.etapa("resumen"):
                r = await asyncio.wait_for(self.orq.ollama.chat(
                    "rapido", [{"role": "system", "content": prompts.RESUMEN_HABLADO},
                               {"role": "user", "content": final}], tarea="Resumen para voz"),
                    turno.timeouts.get("resumen", 10))
            return r.contenido.strip() or None
        except (TimeoutError, OllamaNoDisponible) as e:
            log.warning("resumen hablado no disponible: %s", e)
            return None

    # ───────────── ganchos: STT, visión, TTS ─────────────

    async def _transcribir(self, turno: Turno, audio: bytes, formato: str) -> str:
        stt = self.orq.ganchos.transcriptor
        try:
            with turno.etapa("stt"):
                tr = await self._con_tiempo(stt.transcribir(audio, formato), "stt", turno)
        except GanchoNoDisponible as e:
            raise ErrorTurno("voz_no_disponible", f"{e} Escriba su pregunta o use los botones del guion.",
                             "stt") from None
        except ErrorTurno:
            raise
        except Exception as e:  # p. ej. VozNoDisponible o AudioInvalido de la Fase 4
            log.warning("STT: %s: %s", type(e).__name__, e)
            raise ErrorTurno("voz_no_disponible", "No pude procesar el audio. Escriba su pregunta o use los "
                                                  "botones del guion.", "stt") from None
        if tr.modelo not in turno.modelos:
            turno.modelos.append(tr.modelo)
        turno.emitir("modelo.uso", {"paso": 0, "rol": "stt", "modelo": tr.modelo, "tarea": "Transcribir voz"})
        turno.emitir("transcripcion", {"texto": tr.texto, "modelo": tr.modelo, "idioma": tr.idioma,
                                       "duracion_ms": turno.latencias["stt"]})
        if not tr.texto.strip():
            raise ErrorTurno("mensaje_invalido", "No le escuché bien. ¿Puede repetir la pregunta?", "stt")
        return tr.texto

    async def _leer_documento(self, turno: Turno, texto: str, documento_id: str) -> str:
        doc = self.orq.documentos.obtener(documento_id)
        if doc is None:
            raise ErrorTurno("documento_no_encontrado", "No encontré ese documento. Súbalo de nuevo.", "vision")
        nombre, datos = doc
        try:
            with turno.etapa("vision"):
                ex = await self._con_tiempo(self.orq.ganchos.vision.extraer(datos, nombre), "vision", turno)
        except GanchoNoDisponible as e:
            raise ErrorTurno("vision_no_disponible", str(e), "vision") from None
        except ErrorTurno:
            raise
        except Exception as e:  # ErrorExtraccion de la Fase 3 trae un mensaje para la persona
            log.warning("visión: %s: %s", type(e).__name__, e)
            mensaje = str(e) if type(e).__name__ == "ErrorExtraccion" and str(e) else \
                "No pude leer el documento. Intente con una imagen más nítida."
            raise ErrorTurno("documento_ilegible", mensaje, "vision") from None
        turno.sesion.acumulado["documentos_analizados"] += 1
        turno.emitir("documento.extraido", {"documento_id": documento_id, "nombre": nombre,
                                            "tipo_documento": ex.tipo_documento, "campos": ex.campos,
                                            "modelo": ex.modelo, "duracion_ms": turno.latencias["vision"],
                                            "advertencias": ex.advertencias})
        adv = "".join(f"Advertencia de la lectura: {a}\n" for a in ex.advertencias)
        return prompts.DOCUMENTO.format(texto=texto.strip() or prompts.DOCUMENTO_TEXTO_POR_DEFECTO, nombre=nombre,
                                        tipo=ex.tipo_documento, advertencias=adv,
                                        campos=json.dumps(ex.campos, ensure_ascii=False, indent=2, default=str))

    async def _hablar(self, turno: Turno, texto: str) -> None:
        tts = self.orq.ganchos.sintetizador
        partes = tts.oraciones(texto) if hasattr(tts, "oraciones") else oraciones(texto)
        for oracion in partes:
            try:
                with turno.etapa("tts"):
                    audio = await self._con_tiempo(tts.sintetizar(oracion), "tts", turno)
            except Exception as e:
                turno.emitir("aviso", {"mensaje": "La voz no respondió; la respuesta queda en pantalla.",
                                       "etapa": "tts"})
                log.warning("TTS: %s", e)
                return
            self._emitir_audio(turno, oracion, audio.datos, audio.formato, audio.motor,
                               getattr(audio, "duracion_ms", None))

    def _emitir_audio(self, turno: Turno, texto: str, datos: bytes, formato: str, motor: str,
                      duracion_ms: int | None) -> None:
        if motor not in turno.modelos:
            turno.modelos.append(motor)
            turno.emitir("modelo.uso", {"paso": turno.paso, "rol": "tts", "modelo": motor, "tarea": "Sintetizar voz"})
        audio_id = self.orq.audios.guardar(datos)
        turno.emitir("voz.audio", {"indice": turno.audios_emitidos, "texto": texto, "url": f"/api/audio/{audio_id}",
                                   "formato": formato, "motor": motor, "duracion_ms": duracion_ms})
        turno.audios_emitidos += 1

    async def _emitir_locutor(self, turno: Turno, locutor: Any) -> None:
        """Consume los fragmentos del locutor de la Fase 4 (una oración sintetizada a la vez)."""
        avisado = False
        async for f in locutor.fragmentos():
            turno.latencias["tts"] += int(getattr(f, "latencia_ms", 0) or 0)
            if f.wav is None:
                if not avisado:
                    turno.emitir("aviso", {"mensaje": "Una parte de la voz no se pudo generar; el texto está en "
                                                      "pantalla.", "etapa": "tts"})
                    avisado = True
                continue
            self._emitir_audio(turno, f.texto, f.wav, "wav", f.motor or "tts", f.duracion_ms)


_MENSAJES_TIEMPO = {
    "llm": "El modelo tardó más de lo esperado. Intente de nuevo o reformule la pregunta.",
    "stt": "La transcripción de voz tardó demasiado. Escriba su pregunta o use los botones del guion.",
    "vision": "La lectura del documento tardó demasiado. Intente con otra imagen.",
    "tts": "La voz tardó demasiado; la respuesta queda en pantalla.",
}


def imagen_base64(datos: bytes) -> str:
    """Ayuda para la Fase 3: Ollama recibe las imágenes en base64."""
    return base64.b64encode(datos).decode("ascii")
