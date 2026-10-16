"""Búsqueda en políticas internas (RAG local).

Fase 1: interfaz estable y respuesta honesta si el índice aún no existe.
Fase 3: `scripts/ingest.py` crea data/vectores/politicas.db (sqlite-vec + FTS5) con
embeddings locales de Ollama, y `buscar` hace búsqueda híbrida (vectorial + léxica).
"""

from __future__ import annotations

from typing import Any

from comun import config


def buscar(cfg: dict[str, Any], consulta: str, k: int) -> dict[str, Any]:
    ruta = config.ruta("vectores")
    if not ruta.exists():
        return {"disponible": False, "fragmentos": [],
                "mensaje": "El índice de políticas aún no está construido (ejecute `make ingest`). "
                           "No hay fragmentos que citar."}
    raise NotImplementedError("Búsqueda híbrida: se implementa en la Fase 3.")


def indice(cfg: dict[str, Any]) -> str:
    carpeta = config.ruta("politicas")
    docs = sorted(p for p in carpeta.glob("*") if p.suffix.lower() in {".md", ".pdf"}) if carpeta.exists() else []
    if not docs:
        return "# Políticas internas\n\nAún no hay documentos cargados (se agregan en la Fase 3)."
    lineas = ["# Políticas internas — Banco Atitlán (DEMO / DATOS FICTICIOS)", ""]
    for p in docs:
        titulo = p.stem.replace("_", " ")
        if p.suffix.lower() == ".md":
            for linea in p.read_text(encoding="utf-8").splitlines():
                if linea.startswith("# "):
                    titulo = linea[2:].strip()
                    break
        lineas.append(f"- **{titulo}** (`{p.name}`)")
    return "\n".join(lineas)
