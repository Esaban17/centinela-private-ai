"""Verifica que Centinela funciona sin internet y que nada sale del equipo (`make check-offline`).

    python -m scripts.check_offline              # revisiones + el guion completo una vez
    python -m scripts.check_offline --estricto   # además exige que el equipo esté SIN red (Wi-Fi apagado)
    python -m scripts.check_offline --sin-prueba # solo revisiones estáticas (no necesita Ollama)

Revisa, en este orden:

1. config.yaml: Ollama, la API y la UI solo en loopback; ningún modelo `:cloud`; guardia de salida activa.
2. Variables de modo offline (HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, telemetría apagada).
3. La UI: ningún asset externo (scripts, estilos, fuentes, íconos) y la política CSP en index.html.
4. Ollama: que corra como `ollama serve` (la app de la barra busca actualizaciones) y que escuche solo en loopback.
5. La red del equipo: con red es una prueba parcial; con --estricto, tener red es una falla.
6. El guion completo una vez, con la guardia de salida activa y el monitor de conexiones muestreando cada
   segundo a este proceso, al servidor MCP y a Ollama: todo debe responder y las conexiones salientes ser 0.
7. Si el orquestador está levantado, su propio monitor (el del panel): 0 conexiones en toda la sesión.

Termina con código 1 si hay alguna falla (❌).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psutil

from comun import config, salida
from orchestrator import registro
from orchestrator.soberania import red
from scripts.reporte import Chequeo, cierre, fase
from scripts.warmup import es_nube, modelos_requeridos, url_orquestador

VARIABLES_OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                     "ANONYMIZED_TELEMETRY": "False", "DO_NOT_TRACK": "1"}
EXTENSIONES_WEB = {".html", ".css", ".js", ".mjs", ".svg", ".json", ".webmanifest"}
URL = re.compile(r"""(?:(?:https?|wss?):)?//[A-Za-z0-9.\-\[\]:]+[^\s"'`<>)]*""")
# Nombres de espacio XML: identificadores, el navegador no los descarga.
NO_SON_DESCARGAS = ("http://www.w3.org/",)


def _host(valor: str) -> str:
    valor = str(valor or "")
    if "//" not in valor:
        valor = "//" + valor
    return urlparse(valor).hostname or ""


def revisar_config(cfg: dict[str, Any]) -> list[Chequeo]:
    out = []
    destinos = {"ollama.url": cfg["ollama"]["url"],
                "orquestador.host": cfg["orquestador"].get("host", "127.0.0.1"),
                "ui.host": (cfg.get("ui") or {}).get("host", "127.0.0.1")}
    servidor = (cfg["ollama"].get("servidor") or {}).get("OLLAMA_HOST")
    if servidor:
        destinos["ollama.servidor.OLLAMA_HOST"] = servidor
    for clave, valor in destinos.items():
        host = _host(valor) if clave.endswith(("url", "OLLAMA_HOST")) else str(valor)
        out.append(Chequeo(f"{clave} = {valor}", "ok") if salida.es_local(host) else
                   Chequeo(f"{clave} = {valor}", "falla", "no es de este equipo: use 127.0.0.1"))
    nube = [f"{rol}: {m}" for rol, m in modelos_requeridos(cfg).items() if es_nube(m)]
    out.append(Chequeo("modelos locales (ningún :cloud)", "falla", ", ".join(nube)) if nube else
               Chequeo("modelos locales (ningún :cloud)", "ok"))
    if (cfg.get("soberania") or {}).get("bloquear_salida", True) is False:
        out.append(Chequeo("guardia de salida", "aviso", "desactivada en config.yaml (soberania.bloquear_salida)"))
    else:
        out.append(Chequeo("guardia de salida", "ok", "orquestador y servidor MCP bloquean conexiones externas"))
    return out


def revisar_entorno(entorno: dict[str, str] | None = None) -> list[Chequeo]:
    entorno = os.environ if entorno is None else entorno
    faltan = [f"{k}={v}" for k, v in VARIABLES_OFFLINE.items() if entorno.get(k) != v]
    return [Chequeo("variables de modo offline", "aviso", "faltan " + ", ".join(faltan) + " (use los comandos make)")
            if faltan else Chequeo("variables de modo offline", "ok", ", ".join(VARIABLES_OFFLINE))]


def assets_externos(carpeta: Path) -> list[str]:
    """Referencias a recursos fuera del equipo en la UI («archivo:línea → url»)."""
    hallazgos = []
    for ruta in sorted(carpeta.rglob("*")):
        if ruta.suffix.lower() not in EXTENSIONES_WEB or not ruta.is_file():
            continue
        for n, linea in enumerate(ruta.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in URL.finditer(linea):
                url = m.group(0)
                if url.startswith("//") and not re.search(r"""(?:src|href|url)\s*[=(]\s*["']?$""",
                                                          linea[:m.start()]):
                    continue  # un comentario «// ...» de JS, no una URL relativa al protocolo
                if url.startswith(NO_SON_DESCARGAS) or salida.es_local(_host(url)):
                    continue
                hallazgos.append(f"{ruta.relative_to(carpeta)}:{n} → {url[:80]}")
    return hallazgos


def revisar_web(carpeta: Path) -> list[Chequeo]:
    out = []
    externos = assets_externos(carpeta)
    out.append(Chequeo("UI sin assets externos", "falla", "; ".join(externos[:5])) if externos else
               Chequeo("UI sin assets externos", "ok", "JS, CSS, fuentes e íconos se sirven desde web/"))
    indice = carpeta / "index.html"
    html = indice.read_text(encoding="utf-8") if indice.exists() else ""
    csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', html)
    if csp and "default-src 'self'" in csp.group(1):
        out.append(Chequeo("política CSP del navegador", "ok", "default-src 'self'"))
    else:
        out.append(Chequeo("política CSP del navegador", "aviso", "index.html no limita los orígenes a 'self'"))
    return out


def revisar_procesos_ollama() -> list[Chequeo]:
    out = []
    escuchas: list[str] = []
    app_barra = False
    encontrado = False
    for p in psutil.process_iter(["name", "exe"]):
        nombre = (p.info.get("name") or "").lower()
        if "ollama" not in nombre:
            continue
        encontrado = True
        if "Ollama.app/Contents/MacOS/Ollama" in (p.info.get("exe") or ""):
            app_barra = True
        try:
            for c in p.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN and c.laddr and not salida.es_local(c.laddr.ip):
                    escuchas.append(f"{c.laddr.ip}:{c.laddr.port}")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    if not encontrado:
        return [Chequeo("proceso de Ollama", "aviso", "no se encontró: `make ollama` en otra terminal")]
    out.append(Chequeo("Ollama como servicio", "aviso", "corre la app de la barra de menú, que busca "
                       "actualizaciones: ciérrela y use `make ollama`") if app_barra else
               Chequeo("Ollama como servicio", "ok", "ollama serve"))
    out.append(Chequeo("Ollama escucha solo en este equipo", "falla", "escucha en " + ", ".join(sorted(set(escuchas)))
                       + ": defina OLLAMA_HOST=127.0.0.1:11434 (make ollama)") if escuchas else
               Chequeo("Ollama escucha solo en este equipo", "ok"))
    return out


def revisar_red(estricto: bool) -> list[Chequeo]:
    estado = red.estado_red()
    if not estado["online"]:
        return [Chequeo("equipo sin red", "ok", "prueba en condiciones de escenario (Wi-Fi apagado)")]
    interfaces = ", ".join(i["interfaz"] for i in estado["interfaces"])
    return [Chequeo("equipo con red", "falla" if estricto else "aviso",
                    f"interfaces activas: {interfaces}. Para la prueba completa apague el Wi-Fi y repita con "
                    "--estricto")]


async def prueba_funcional(cfg: dict[str, Any]) -> list[Chequeo]:
    from scripts.ensayo import Vigia
    from scripts.guion import copia_db, correr_guion, crear_orquestador

    out = []
    bloqueos_antes = len(salida.bloqueos())
    try:
        with copia_db(config.ruta("db", cfg)) as db, Vigia(cfg, intervalo=0.5) as vigia:
            orq = crear_orquestador(cfg, db)
            try:
                await orq.host.conectar()
                resultados = await correr_guion(orq, cfg.get("guion", []), "check-offline", al_terminar_paso=lambda r:
                                                print(f"   {r.id:10s} {'ok' if r.ok else 'FALLÓ'} · {r.total_ms} ms",
                                                      flush=True))
            finally:
                await orq.cerrar()
    except Exception as e:  # p. ej. Ollama apagado: la prueba no se pudo hacer
        return [Chequeo("guion sin red", "falla", f"{type(e).__name__}: {e}"[:200])]
    for r in resultados:
        out.append(Chequeo(f"guion «{r.id}»", "ok" if r.ok else "falla",
                           f"{r.total_ms} ms" + ("" if r.ok else " · " + "; ".join(r.problemas))))
    detalle = "; ".join(f"{h['proceso']} → {h['remoto']}" for h in vigia.detalle[:5])
    out.append(Chequeo(f"conexiones salientes ({vigia.muestras} muestras)", "falla" if vigia.max_salientes else "ok",
                       detalle or "0 en orquestador, servidor MCP y Ollama"))
    bloqueos = salida.bloqueos()[bloqueos_antes:]
    out.append(Chequeo("intentos de salida bloqueados", "falla", "; ".join(b["destino"] for b in bloqueos[:5]))
               if bloqueos else Chequeo("intentos de salida bloqueados", "ok", "ninguno"))
    return out


async def revisar_orquestador(cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> list[Chequeo]:
    try:
        async with httpx.AsyncClient(base_url=url_orquestador(cfg), trust_env=False, transport=transport) as http:
            r = await http.get("/api/soberania/conexiones", timeout=5.0)
            r.raise_for_status()
            c = r.json()
    except httpx.HTTPError:
        return [Chequeo("monitor del panel", "aviso", "el orquestador no está levantado; no se revisó")]
    if c.get("salientes") or c.get("max_sesion"):
        destinos = "; ".join(f"{d['proceso']} → {d['remoto']}" for d in c.get("detalle", [])[:5]) or \
                   "; ".join(f"{h['proceso']} → {h['remoto']}" for h in c.get("historial", [])[:5])
        return [Chequeo("monitor del panel", "falla", f"ahora {c.get('salientes', 0)}, máximo de la sesión "
                                                      f"{c.get('max_sesion', 0)}: {destinos}")]
    return [Chequeo("monitor del panel", "ok", f"0 conexiones salientes en la sesión · {len(c.get('procesos', []))} "
                                               "procesos vigilados")]


async def principal(args: argparse.Namespace, cfg: dict[str, Any]) -> list[Chequeo]:
    res: list[Chequeo] = []
    fase("Configuración", revisar_config(cfg), res)
    fase("Entorno", revisar_entorno(), res)
    fase("Interfaz web", revisar_web(config.RAIZ / "web"), res)
    fase("Ollama", revisar_procesos_ollama(), res)
    fase("Red del equipo", revisar_red(args.estricto), res)
    if not args.sin_prueba:
        print("\n▸ Guion completo con la guardia de salida y el monitor encendidos…", flush=True)
        fase("Prueba funcional (guion completo)", await prueba_funcional(cfg), res)
    fase("Orquestador en marcha", await revisar_orquestador(cfg), res)
    return res


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--estricto", action="store_true", help="falla si el equipo tiene red")
    p.add_argument("--sin-prueba", action="store_true", help="no correr el guion (no necesita Ollama)")
    args = p.parse_args()
    registro.configurar("WARNING")
    cfg = config.cargar()
    salida.instalar(cfg)
    listo = "REVISIÓN ESTÁTICA SIN FALLAS (sin probar el guion)" if args.sin_prueba else "SIN SALIDAS A INTERNET"
    sys.exit(cierre(asyncio.run(principal(args, cfg)), listo, "REVISAR ANTES DE LA DEMO"))


if __name__ == "__main__":
    main()
