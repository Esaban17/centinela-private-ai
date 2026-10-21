"""Precalienta Centinela antes de subir al escenario (`make warmup`).

1. Revisa que la base, el índice de políticas y los documentos demo existan.
2. Revisa que Ollama responda en este equipo y que los 4 modelos de config.yaml estén descargados
   (nunca un modelo `:cloud`, que rompería la regla 100 % local).
3. Carga cada modelo con keep_alive=-1 y le hace una inferencia corta, con las mismas opciones que usa el
   orquestador (si cambiara num_ctx, Ollama recargaría el modelo en escena).
4. Corre cada pregunta del guion una vez sobre una copia de la base: deja caliente el prompt del agente.
5. Precalienta la voz. Whisper y Kokoro viven dentro del orquestador: si está levantado (`make demo`), se le
   pide a él; si no, se cargan aquí solo para dejar los pesos en la caché del disco.
6. Verifica con /api/ps que los 4 modelos sigan cargados, sin vencimiento, en GPU y dentro del presupuesto
   (el menor entre ollama.presupuesto_gb y lo que deja libre la RAM de este equipo).

    python -m scripts.warmup                 # todo
    python -m scripts.warmup --sin-guion     # solo modelos y voz (más rápido)

Termina con código 1 si algo impide la demo (❌). Los avisos (⚠️) no la impiden, pero hay que leerlos.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from comun import config, salida
from comun import modelos as sel_modelos
from comun.ollama_http import ClienteOllama, ErrorOllama
from orchestrator import registro
from orchestrator.ollama import Ollama, OllamaNoDisponible
from scripts.reporte import Chequeo, cierre, fase

ROLES = ("agente", "rapido", "vision", "embeddings")
GB = 1024 ** 3


def modelos_requeridos(cfg: dict[str, Any]) -> dict[str, str]:
    return {rol: cfg["modelos"][rol]["nombre"] for rol in ROLES}


con_etiqueta = sel_modelos.con_etiqueta


es_nube = sel_modelos.es_nube


def revisar_nombres(modelos: dict[str, str]) -> list[Chequeo]:
    return [Chequeo(f"{rol}: {m}", "falla", "es un modelo :cloud de Ollama (corre fuera del equipo); "
                                            "elija una etiqueta local en config.yaml")
            for rol, m in modelos.items() if es_nube(m)]


def revisar_descargados(modelos: dict[str, str], etiquetas: list[str]) -> list[Chequeo]:
    presentes = {con_etiqueta(e) for e in etiquetas}
    return [Chequeo(f"{rol}: {m} descargado", "ok") if con_etiqueta(m) in presentes else
            Chequeo(f"{rol}: {m} descargado", "falla", f"no está en Ollama: `ollama pull {m}` (con internet)")
            for rol, m in modelos.items()]


def _sin_vencimiento(expira: str | None) -> bool:
    """keep_alive=-1 hace que Ollama ponga un vencimiento a siglos de distancia."""
    if not expira:
        return False
    try:
        fecha = datetime.fromisoformat(expira.replace("Z", "+00:00"))
    except ValueError:
        return False
    return fecha.year - datetime.now(timezone.utc).year >= 50


def analizar_ps(ps: dict[str, Any], modelos: dict[str, str], presupuesto_gb: float | None) -> list[Chequeo]:
    """Lo que dice /api/ps frente a lo que la demo necesita."""
    cargados = {con_etiqueta(m.get("name") or m.get("model") or ""): m for m in ps.get("models", [])}
    out: list[Chequeo] = []
    for rol, nombre in modelos.items():
        m = cargados.get(con_etiqueta(nombre))
        if m is None:
            out.append(Chequeo(f"{rol} cargado", "falla", f"{nombre} no está en memoria. Revise "
                               "OLLAMA_MAX_LOADED_MODELS (make ollama) y la memoria libre"))
            continue
        tam, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        gpu = round(100 * vram / tam) if tam else 100
        detalle = f"{tam / GB:.1f} GB, {gpu} % en GPU"
        if gpu < 99:
            out.append(Chequeo(f"{rol} cargado", "aviso", f"{detalle}: parte corre en CPU y será lento. Suba el "
                               "límite de GPU (scripts/setup_mac.sh --limite-gpu) o baje num_ctx"))
        elif not _sin_vencimiento(m.get("expires_at")):
            out.append(Chequeo(f"{rol} cargado", "aviso", f"{detalle}, pero vence {m.get('expires_at')}: "
                               "Ollama no recibió keep_alive=-1 (¿OLLAMA_KEEP_ALIVE distinto?)"))
        else:
            out.append(Chequeo(f"{rol} cargado", "ok", f"{detalle}, sin vencimiento"))
    sobran = [n for n in cargados if n not in {con_etiqueta(x) for x in modelos.values()}]
    if sobran:
        out.append(Chequeo("modelos ajenos a la demo en memoria", "aviso",
                           ", ".join(sobran) + " — `ollama stop <modelo>` para liberar memoria"))
    total = sum(int(m.get("size") or 0) for m in cargados.values()) / GB
    if presupuesto_gb:
        estado = "ok" if total <= float(presupuesto_gb) else "aviso"
        out.append(Chequeo("memoria de los modelos", estado, f"{total:.1f} GB de un presupuesto de "
                                                             f"{float(presupuesto_gb):.0f} GB"))
    return out


def revisar_ram(cfg: dict[str, Any]) -> tuple[list[Chequeo], float | None]:
    """La selección de modelos frente a la RAM de este equipo, y el presupuesto de Ollama que deja libre."""
    r = (cfg.get("seleccion_modelos") or {}).get("resuelto") or {}
    est = sel_modelos.estimar(cfg["modelos"], cfg, r.get("ram_gb"))
    estado = {"ok": "ok", "justo": "aviso", "excede": "aviso"}[est["nivel"]]
    out = [Chequeo(f"perfil {r.get('perfil', 'manual')} ({r.get('motivo', '')})", estado, est["mensaje"])]
    out += [Chequeo("memoria", "aviso", a) for a in est["avisos"]]
    voz = sum(f["gb"] or 0 for f in est["filas"] if f["rol"] in ("stt", "tts"))
    return out, round(est["disponible_gb"] - voz, 1)


def revisar_archivos(cfg: dict[str, Any]) -> list[Chequeo]:
    out = []
    db = config.ruta("db", cfg)
    out.append(Chequeo("base del banco", "ok") if db.exists() else
               Chequeo("base del banco", "falla", "falta: `make seed`"))
    vec = config.ruta("vectores", cfg)
    out.append(Chequeo("índice de políticas", "ok") if vec.exists() else
               Chequeo("índice de políticas", "falla", "falta: `make ingest`"))
    docs = config.ruta("documentos_demo", cfg)
    faltan = [p["documento_id"][5:] for p in cfg.get("guion", [])
              if str(p.get("documento_id", "")).startswith("demo-") and not (docs / p["documento_id"][5:]).exists()]
    out.append(Chequeo("documentos demo", "falla", f"faltan {', '.join(faltan)}: `make documentos`") if faltan
               else Chequeo("documentos demo", "ok"))
    return out


def imagen_prueba() -> bytes:
    """Una imagen mínima con texto, para que el modelo de visión haga una inferencia real."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 120), "white")
    d = ImageDraw.Draw(img)
    d.text((16, 20), "DEMO / DATOS FICTICIOS", fill="black")
    d.text((16, 60), "Monto: Q 100.00", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def cargar_modelos(cfg: dict[str, Any], ollama: Ollama, cliente: ClienteOllama) -> list[Chequeo]:
    """Carga cada modelo con una inferencia corta, por el mismo camino que usa la demo."""
    out = []
    pruebas = {
        "agente": lambda: ollama.chat("agente", [{"role": "user", "content": "Responda solo: listo"}]),
        "rapido": lambda: ollama.chat("rapido", [{"role": "user", "content": "Responda solo: listo"}]),
        "vision": lambda: ollama.chat("vision", [{"role": "user", "content": "¿Qué dice la imagen? Breve."}],
                                      imagenes=[imagen_prueba()], opciones={"temperature": 0}),
        "embeddings": lambda: asyncio.to_thread(cliente.embeddings, cfg["modelos"]["embeddings"]["nombre"],
                                                ["precalentar el índice de políticas"]),
    }
    for rol, prueba in pruebas.items():
        t = time.perf_counter()
        print(f"   cargando {rol} ({cfg['modelos'][rol]['nombre']})…", flush=True)
        try:
            r = await prueba()
        except (OllamaNoDisponible, ErrorOllama) as e:
            out.append(Chequeo(f"{rol}: inferencia", "falla", str(e)[:200]))
            continue
        ms = int((time.perf_counter() - t) * 1000)
        if rol == "embeddings":
            out.append(Chequeo(f"{rol}: inferencia", "ok", f"{ms} ms, {len(r[0])} dimensiones"))
        elif not r.contenido.strip():
            out.append(Chequeo(f"{rol}: inferencia", "aviso", f"{ms} ms, respondió vacío"))
        else:
            out.append(Chequeo(f"{rol}: inferencia", "ok", f"{ms} ms · {r.tokens_por_s} tok/s"))
    return out


def url_orquestador(cfg: dict[str, Any]) -> str:
    oq = cfg["orquestador"]
    return f"http://{oq.get('host', '127.0.0.1')}:{int(oq.get('puerto', 8765))}"


async def precalentar_voz(cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> list[Chequeo]:
    base = url_orquestador(cfg)
    estado: dict[str, Any] | None = None
    async with httpx.AsyncClient(base_url=base, trust_env=False, transport=transport) as http:
        try:
            (await http.get("/api/salud", timeout=2.0)).raise_for_status()
        except httpx.HTTPError:
            pass  # el orquestador no está levantado
        else:
            print(f"   pidiendo al orquestador ({base}) que cargue Whisper y Kokoro…", flush=True)
            estado = (await http.post("/api/precalentar", timeout=300.0)).json()
            origen = "en el orquestador"
    if estado is None:
        from orchestrator import voz

        print("   el orquestador no está levantado: cargo la voz aquí (solo deja los pesos en la caché del disco)",
              flush=True)
        estado = await voz.precalentar_voz(cfg)
        origen = "fuera del orquestador; `make demo` la vuelve a cargar al arrancar"
    out = []
    motor_tts = cfg["modelos"]["tts"].get("motor", "kokoro")
    for comp in ("stt", "tts"):
        e = str(estado.get(comp, "sin respuesta"))
        nombre = f"voz {comp.upper()} ({origen})"
        if not e.startswith("ok"):
            out.append(Chequeo(nombre, "aviso", f"{e} — la demo sigue con texto y botones del guion"))
        elif comp == "tts" and motor_tts not in e:
            out.append(Chequeo(nombre, "aviso", f"{e}: {motor_tts} no cargó y habla el respaldo"))
        else:
            out.append(Chequeo(nombre, "ok", e))
    return out


async def correr_guion_una_vez(cfg: dict[str, Any]) -> list[Chequeo]:
    from scripts.guion import copia_db, correr_guion, crear_orquestador

    out = []
    with copia_db(config.ruta("db", cfg)) as db:
        orq = crear_orquestador(cfg, db)
        try:
            await orq.host.conectar()

            def informar(r: Any) -> None:
                print(f"   {r.id:10s} {'ok' if r.ok else 'FALLÓ'} · {r.total_ms} ms", flush=True)

            for r in await correr_guion(orq, cfg.get("guion", []), "warmup", al_terminar_paso=informar):
                out.append(Chequeo(f"guion «{r.id}»", "ok" if r.ok else "aviso",
                                   f"{r.total_ms} ms · {', '.join(r.herramientas) or 'sin herramientas'}"
                                   + ("" if r.ok else " · " + "; ".join(r.problemas))))
        finally:
            await orq.cerrar()
    return out


async def principal(args: argparse.Namespace, cfg: dict[str, Any]) -> list[Chequeo]:
    resultado: list[Chequeo] = []

    fase("Archivos de la demo", revisar_archivos(cfg), resultado)
    modelos = modelos_requeridos(cfg)
    fase("Modelos de config.yaml", revisar_nombres(modelos) or [Chequeo("todas las etiquetas son locales", "ok")],
         resultado)
    chequeos_ram, libre_ollama = revisar_ram(cfg)
    fase("RAM del equipo", chequeos_ram, resultado)
    presupuesto = cfg["ollama"].get("presupuesto_gb")
    if libre_ollama is not None:
        presupuesto = min(float(presupuesto), libre_ollama) if presupuesto else libre_ollama

    url = cfg["ollama"]["url"]
    cliente = ClienteOllama.desde_config(cfg, timeout=600)
    try:
        try:
            etiquetas = cliente.etiquetas()
            version = cliente.version()
        except ErrorOllama:
            fase("Ollama", [Chequeo(f"Ollama en {url}", "falla", "no responde: `make ollama` en otra terminal")],
                 resultado)
            return resultado
        fase("Ollama", [Chequeo(f"Ollama {version} en {url}", "ok"), *revisar_descargados(modelos, etiquetas)],
             resultado)
        if any(c.estado == "falla" for c in resultado):
            return resultado

        ollama = Ollama(cfg)
        try:
            print("\n▸ Carga de modelos (la primera vez tarda: se leen ~30 GB del disco)")
            cargas = await cargar_modelos(cfg, ollama, cliente)
            for c in cargas:
                print("  ", c)
            resultado.extend(cargas)
        finally:
            await ollama.cerrar()

        if not args.sin_guion:
            print("\n▸ Guion completo, una vez (copia de la base; aprueba los tickets)")
            fase("Resultado del guion", await correr_guion_una_vez(cfg), resultado)
        if not args.sin_voz:
            print("\n▸ Precalentando la voz…")
            fase("Voz", await precalentar_voz(cfg), resultado)

        fase("Memoria (ollama ps)", analizar_ps(cliente.cargados(), modelos, presupuesto),
             resultado)
    finally:
        cliente.cerrar()
    return resultado


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sin-guion", action="store_true", help="no correr las preguntas del guion")
    p.add_argument("--sin-voz", action="store_true", help="no precalentar Whisper ni Kokoro")
    args = p.parse_args()
    registro.configurar("WARNING")
    cfg = config.cargar()
    salida.instalar(cfg)
    t = time.perf_counter()
    chequeos = asyncio.run(principal(args, cfg))
    codigo = cierre(chequeos, "LISTO", "NO LISTO")
    print(f"Tiempo: {time.perf_counter() - t:.0f} s.")
    if not codigo:
        print("Los modelos quedan cargados sin vencimiento. No cierre Ollama hasta terminar la charla.")
    sys.exit(codigo)


if __name__ == "__main__":
    main()
