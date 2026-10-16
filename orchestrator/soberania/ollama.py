"""Modelos cargados en Ollama y memoria usada (GET /api/ps)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .conexiones import es_loopback

_GB = 1024 ** 3
# Sin proxies: aunque el entorno defina HTTP(S)_PROXY, la consulta va directo a localhost.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _host_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "localhost" or es_loopback(host)


def modelos_cargados(url: str, timeout: float = 1.5) -> dict:
    """Consulta /api/ps. Nunca lanza: si Ollama no responde, lo dice en `error`."""
    if not _host_local(url):
        # Consultar un Ollama remoto sería una conexión saliente del propio monitor.
        return {"disponible": False, "error": "La URL de Ollama no es local; no se consulta.", "modelos": []}
    try:
        with _OPENER.open(url.rstrip("/") + "/api/ps", timeout=timeout) as r:  # noqa: S310 (URL local validada)
            datos = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"disponible": False, "error": f"Ollama no responde ({e.__class__.__name__}).", "modelos": []}

    modelos = []
    for m in datos.get("models", []):
        tam = int(m.get("size") or 0)
        vram = int(m.get("size_vram") or 0)
        detalles = m.get("details") or {}
        modelos.append({
            "nombre": m.get("name") or m.get("model"),
            "memoria_gb": round(tam / _GB, 2),
            "gpu_pct": round(100 * vram / tam) if tam else 0,
            "parametros": detalles.get("parameter_size"),
            "cuantizacion": detalles.get("quantization_level"),
            "contexto": m.get("context_length"),
            "expira": m.get("expires_at"),
        })
    return {
        "disponible": True,
        "modelos": modelos,
        "memoria_total_gb": round(sum(m["memoria_gb"] for m in modelos), 2),
    }
