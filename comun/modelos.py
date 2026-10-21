"""Selección de modelos según la RAM del equipo y alerta de memoria.

Tres piezas, todas sin red:

- `ram_total_gb()` y `limite_gpu_gb()`: cuánta memoria tiene este equipo (psutil) y cuánta deja macOS a la GPU.
- `aplicar_perfil(cfg)`: elige el perfil de `seleccion_modelos` (auto = por RAM) y lo vuelca en `cfg["modelos"]`.
  Lo llama `config.cargar()`, así orquestador, servidor MCP, `make pull-models` y `make warmup` ven lo mismo.
- `estimar(...)`: suma la memoria de una selección y dice si cabe (ok), va justa o se pasa (excede).

`CENTINELA_RAM_GB=24` simula otra máquina (p. ej. para ver en una Mac de 48 GB lo que verá una de 24 GB).

    python -m comun.modelos              # tabla de la selección actual y alerta (`make modelos`)
"""

from __future__ import annotations

import copy
import os
import platform
import subprocess
from typing import Any

ROLES_OLLAMA = ("agente", "rapido", "vision", "embeddings")
CLAVES_ROL = ("nombre", "num_ctx", "think")
GB = 1024 ** 3


# ───────────── el equipo ─────────────

def ram_total_gb() -> float:
    simulada = os.environ.get("CENTINELA_RAM_GB", "").strip()
    if simulada:
        return float(simulada)
    import psutil

    return round(psutil.virtual_memory().total / GB, 1)


def limite_gpu_gb(ram_gb: float) -> tuple[float, str]:
    """Memoria que la GPU puede fijar en macOS (Ollama y MLX la usan). `sysctl iogpu.wired_limit_mb` si se subió
    con `make setup-mac LIMITE_GPU=1`; si no, el valor por omisión de macOS: ~2/3 hasta 36 GB y ~3/4 arriba."""
    if not os.environ.get("CENTINELA_RAM_GB") and platform.system() == "Darwin":
        try:
            mb = int(subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"], capture_output=True, text=True,
                                    timeout=2).stdout.strip() or 0)
            if mb > 0:
                return round(mb / 1024, 1), "iogpu.wired_limit_mb"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return round(ram_gb * (2 / 3 if ram_gb <= 36 else 3 / 4), 1), "valor por omisión de macOS"


def es_nube(nombre: str) -> bool:
    """Los modelos `:cloud` de Ollama corren fuera del equipo: nunca en la demo."""
    etiqueta = nombre.rsplit(":", 1)[-1].lower() if ":" in nombre else ""
    return etiqueta == "cloud" or etiqueta.endswith("-cloud")


def con_etiqueta(nombre: str) -> str:
    """Ollama lista «bge-m3» como «bge-m3:latest»."""
    return nombre if ":" in nombre.rsplit("/", 1)[-1] else f"{nombre}:latest"


# ───────────── perfiles ─────────────

def _sel(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("seleccion_modelos") or {}


def resolver_perfil(cfg: dict[str, Any], ram_gb: float) -> str:
    """Nombre del perfil que aplica: el pedido, o con «auto» el más grande que quepa en `ram_gb`."""
    sel = _sel(cfg)
    pedido = str(sel.get("perfil") or "manual").strip()
    perfiles = sel.get("perfiles") or {}
    if pedido != "auto":
        return pedido
    caben = [(float(p.get("ram_minima_gb") or 0), n) for n, p in perfiles.items() if float(p.get("ram_minima_gb") or 0) <= ram_gb]
    return max(caben)[1] if caben else "manual"


def modelos_de_perfil(cfg: dict[str, Any], perfil: str, manual: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """La sección `modelos` de los archivos con el perfil encima (sin tocar `cfg`). Con «manual», encima van
    `manual` (o `seleccion_modelos.manual`, lo que guarda la UI)."""
    sel = _sel(cfg)
    modelos = copy.deepcopy(cfg.get("modelos") or {})
    # `base` = `modelos` tal como venía de los archivos, antes de cualquier perfil: así se cambia de perfil
    # sin arrastrar el anterior.
    for rol, valores in (sel.get("base") or {}).items():
        modelos[rol] = {**{k: v for k, v in modelos.get(rol, {}).items() if k not in CLAVES_ROL}, **copy.deepcopy(valores)}
    cambios = (manual if manual is not None else sel.get("manual")) if perfil == "manual" else \
        (sel.get("perfiles") or {}).get(perfil)
    for rol, valores in (cambios or {}).items():
        if rol in ROLES_OLLAMA and isinstance(valores, dict):
            modelos[rol] = {**modelos.get(rol, {}), **{k: v for k, v in valores.items() if k in CLAVES_ROL}}
    return modelos


def aplicar_perfil(cfg: dict[str, Any], ram_gb: float | None = None) -> dict[str, Any]:
    """Vuelca el perfil en `cfg["modelos"]` y anota en `seleccion_modelos.resuelto` cuál quedó y por qué."""
    sel = cfg.setdefault("seleccion_modelos", {})
    ram = ram_total_gb() if ram_gb is None else ram_gb
    sel.setdefault("base", {r: {k: v for k, v in ((cfg.get("modelos") or {}).get(r) or {}).items() if k in CLAVES_ROL}
                            for r in ROLES_OLLAMA})
    pedido = str(sel.get("perfil") or "manual").strip()
    perfil = resolver_perfil(cfg, ram)
    if perfil != "manual" and perfil not in (sel.get("perfiles") or {}):
        motivo = f"el perfil «{perfil}» no existe; se usa la sección `modelos`"
        perfil = "manual"
    elif pedido == "auto":
        motivo = f"auto: este equipo tiene {ram:g} GB de RAM"
    elif perfil == "manual":
        motivo = "selección manual"
    else:
        motivo = "elegido en la configuración"
    cfg["modelos"] = modelos_de_perfil(cfg, perfil)
    sel["resuelto"] = {"perfil": perfil, "pedido": pedido, "ram_gb": ram, "motivo": motivo}
    return cfg


# ───────────── estimación ─────────────

def memoria_modelo(nombre: str, num_ctx: int | None, catalogo: dict[str, Any],
                   tamanos: dict[str, float] | None = None) -> tuple[float | None, str]:
    """GB que ocupa `nombre` cargado con `num_ctx`, y de dónde sale la cifra (medido | estimado | ollama | ?)."""
    c = catalogo.get(nombre) or catalogo.get(con_etiqueta(nombre)) or catalogo.get(nombre.removesuffix(":latest"))
    if c:
        gb = float(c["gb"])
        if num_ctx and c.get("ctx") and c.get("kv_gb_8k"):
            gb += (int(num_ctx) - int(c["ctx"])) / 8192 * float(c["kv_gb_8k"])
        return round(max(gb, 0.1), 1), str(c.get("fuente") or "estimado")
    disco = (tamanos or {}).get(con_etiqueta(nombre))
    if disco:
        # Sin dato propio: pesos del disco + ~0.5 GB de caché KV por cada 8K de contexto.
        return round(disco + 0.5 * (int(num_ctx or 4096) / 8192), 1), "ollama"
    return None, "?"


def estimar(modelos: dict[str, Any], cfg: dict[str, Any], ram_gb: float | None = None,
            tamanos: dict[str, float] | None = None, limite_gpu: tuple[float, str] | None = None) -> dict[str, Any]:
    """Memoria de una selección frente a la RAM del equipo.

    `modelos`: como la sección `modelos` de config.yaml (al menos `nombre` y `num_ctx` por rol).
    `tamanos`: GB en disco de cada modelo descargado (Ollama /api/tags), para los que no están en el catálogo.
    """
    sel = _sel(cfg)
    catalogo = sel.get("catalogo") or {}
    voz = sel.get("voz") or {}
    ram = ram_total_gb() if ram_gb is None else float(ram_gb)
    reserva = float(sel.get("reserva_sistema_gb", 6))
    umbral = float(sel.get("umbral_justo", 0.9))  # fracción de la RAM total (modelos + reserva)
    gpu_gb, gpu_fuente = limite_gpu or limite_gpu_gb(ram)

    filas: list[dict[str, Any]] = []
    avisos: list[str] = []
    vistos: dict[str, tuple[str, int | None]] = {}
    total_ollama = 0.0
    for rol in ROLES_OLLAMA:
        m = modelos.get(rol) or {}
        nombre = str(m.get("nombre") or "").strip()
        if not nombre:
            continue
        ctx = int(m["num_ctx"]) if m.get("num_ctx") else None
        if es_nube(nombre):
            avisos.append(f"{rol}: {nombre} es un modelo :cloud de Ollama (corre fuera del equipo). No se permite.")
        clave = con_etiqueta(nombre)
        if clave in vistos:
            # Ollama carga una sola copia; si el num_ctx difiere, la recarga cada vez que cambia de tarea.
            rol_previo, ctx_previo = vistos[clave]
            if ctx and ctx_previo and ctx != ctx_previo:
                avisos.append(f"{rol} y {rol_previo} usan {nombre} con num_ctx distinto ({ctx} y {ctx_previo}): "
                              "Ollama lo recargaría al cambiar de tarea. Use el mismo num_ctx.")
            filas.append({"rol": rol, "nombre": nombre, "gb": 0.0, "fuente": "compartido", "comparte_con": rol_previo})
            continue
        vistos[clave] = (rol, ctx)
        gb, fuente = memoria_modelo(nombre, ctx, catalogo, tamanos)
        if gb is None:
            avisos.append(f"{rol}: no hay dato de memoria para {nombre} (no está en el catálogo ni descargado). "
                          "La suma lo deja fuera.")
        total_ollama += gb or 0.0
        filas.append({"rol": rol, "nombre": nombre, "gb": gb, "fuente": fuente, "comparte_con": None})

    stt = modelos.get("stt") or {}
    tts = modelos.get("tts") or {}
    motor_stt = str(stt.get("motor") or "auto")
    if motor_stt == "auto":
        motor_stt = "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster"
    stt_gb = float(voz.get("stt_mlx_gb", 2.0) if motor_stt == "mlx" else voz.get("stt_faster_gb", 1.6))
    tts_gb = float(voz.get("tts_kokoro_gb", 0.4)) if str(tts.get("motor") or "kokoro") == "kokoro" else 0.0
    if stt.get("nombre") or stt.get("nombre_faster"):
        filas.append({"rol": "stt", "nombre": stt.get("nombre") if motor_stt == "mlx" else stt.get("nombre_faster"),
                      "gb": stt_gb, "fuente": "estimado", "comparte_con": None})
    if tts_gb:
        filas.append({"rol": "tts", "nombre": "Kokoro-82M", "gb": tts_gb, "fuente": "estimado", "comparte_con": None})

    modelos_gb = round(total_ollama + stt_gb + tts_gb, 1)
    disponible = round(max(ram - reserva, 0.0), 1)
    total = round(modelos_gb + reserva, 1)
    uso = total / ram if ram else float("inf")
    nivel = "excede" if uso > 1 else "justo" if uso >= umbral else "ok"

    gpu_uso = round(total_ollama + (stt_gb if motor_stt == "mlx" else 0.0), 1)
    if platform.system() == "Darwin" or os.environ.get("CENTINELA_RAM_GB"):
        if gpu_uso > gpu_gb:
            # `make setup-mac LIMITE_GPU=1` deja 8 GB al sistema: solo sirve si eso da más que el límite actual.
            remedio = ("Suba el límite con `make setup-mac LIMITE_GPU=1` o elija modelos más chicos."
                       if ram - 8 > gpu_gb else "Elija modelos más chicos: en este equipo no conviene subir el límite.")
            avisos.append(f"Los modelos en GPU suman ~{gpu_uso:g} GB y macOS deja ~{gpu_gb:g} GB a la GPU "
                          f"({gpu_fuente}): una parte correrá en CPU y será lento. {remedio}")

    if nivel == "excede":
        mensaje = (f"La selección no cabe: necesita ~{total:g} GB (modelos {modelos_gb:g} + sistema {reserva:g}) "
                   f"y este equipo tiene {ram:g} GB. macOS usará swap y la demo se trabará. Elija modelos más chicos "
                   "o el perfil de menos RAM.")
    elif nivel == "justo":
        mensaje = (f"Cabe justo: ~{total:g} GB de {ram:g} GB ({round(100 * total / ram)} %). Cierre otras apps "
                   "antes de la demo.")
    else:
        mensaje = f"Cabe: ~{total:g} GB de {ram:g} GB ({round(100 * total / ram)} %)."
    return {"nivel": nivel, "mensaje": mensaje, "avisos": avisos, "filas": filas,
            "modelos_gb": modelos_gb, "reserva_gb": reserva, "total_gb": total, "ram_gb": ram,
            "disponible_gb": disponible, "uso_pct": round(100 * uso) if ram else None,
            "gpu": {"limite_gb": gpu_gb, "uso_gb": gpu_uso, "fuente": gpu_fuente}}


def opciones_por_rol(cfg: dict[str, Any], instalados: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Modelos que la UI ofrece en cada rol: los del catálogo para ese rol, los de los perfiles y los ya descargados."""
    sel = _sel(cfg)
    catalogo = sel.get("catalogo") or {}
    instalados_set = {con_etiqueta(n) for n in instalados or []}
    out: dict[str, list[dict[str, Any]]] = {}
    for rol in ROLES_OLLAMA:
        nombres: list[str] = [n for n, c in catalogo.items() if rol in (c.get("roles") or [])]
        for p in (sel.get("perfiles") or {}).values():
            n = ((p or {}).get(rol) or {}).get("nombre")
            if n and n not in nombres:
                nombres.append(n)
        actual = ((cfg.get("modelos") or {}).get(rol) or {}).get("nombre")
        if actual and actual not in nombres:
            nombres.append(actual)
        # Lo descargado que no está en el catálogo también se puede elegir (salvo :cloud), al final.
        extra = sorted(n for n in instalados or [] if not es_nube(n) and n not in nombres
                       and con_etiqueta(n) not in {con_etiqueta(x) for x in nombres})
        if rol == "embeddings":
            extra = [n for n in extra if "embed" in n or "bge" in n]
        else:
            extra = [n for n in extra if "embed" not in n and "bge" not in n]
        out[rol] = [{"nombre": n, "gb": (catalogo.get(n) or {}).get("gb"), "fuente": (catalogo.get(n) or {}).get("fuente"),
                     "descargado": (con_etiqueta(n) in instalados_set) if instalados is not None else None}
                    for n in nombres + extra]
    return out


def _imprimir(cfg: dict[str, Any]) -> int:
    r = (cfg.get("seleccion_modelos") or {}).get("resuelto") or {}
    est = estimar(cfg["modelos"], cfg, r.get("ram_gb"))
    print(f"Perfil: {r.get('perfil', '?')} ({r.get('motivo', '')})")
    for f in est["filas"]:
        gb = "—" if f["gb"] is None else f"{f['gb']:.1f} GB"
        extra = f" (mismo modelo que {f['comparte_con']})" if f["comparte_con"] else f" [{f['fuente']}]"
        print(f"  {f['rol']:<11} {str(f['nombre']):<38} {gb:>8}{extra}")
    print(f"  {'sistema':<11} {'macOS, navegador, Python (reserva)':<38} {est['reserva_gb']:>5.1f} GB")
    print(f"  {'TOTAL':<11} {'':<38} {est['total_gb']:>5.1f} GB de {est['ram_gb']:g} GB")
    icono = {"ok": "✅", "justo": "⚠️ ", "excede": "❌"}[est["nivel"]]
    print(f"\n{icono} {est['mensaje']}")
    for a in est["avisos"]:
        print(f"⚠️  {a}")
    return 1 if est["nivel"] == "excede" or any(":cloud" in a for a in est["avisos"]) else 0


if __name__ == "__main__":
    from comun import config

    raise SystemExit(_imprimir(config.cargar()))
