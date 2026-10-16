"""Indexa las políticas de data/politicas/ en data/vectores/politicas.db (RAG local).

    make ingest            # o: python -m scripts.ingest [--sin-embeddings]

1. Corta cada documento Markdown por secciones (`##`) y subsecciones (`###`); las secciones muy largas se
   parten por párrafos. Cada fragmento conserva documento + sección para poder citarlo.
2. Calcula embeddings con el modelo local de Ollama (config: modelos.embeddings).
3. Guarda todo en un solo archivo SQLite: tabla, índice léxico FTS5 e índice vectorial sqlite-vec.

El índice se escribe en un archivo temporal y se reemplaza al final: el servidor MCP nunca ve un índice a
medias. Si Ollama no está disponible se puede indexar solo la parte léxica con `--sin-embeddings`.

DEMO / DATOS FICTICIOS.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from comun import config
from mcp_server.rag import ESQUEMA_INDICE, a_blob, cargar_sqlite_vec, normalizar

log = logging.getLogger("ingest")

_CODIGO = re.compile(r"C[óo]digo:\s*([A-Z]{3}-[A-Z]{3}-\d{3})")


@dataclass
class Fragmento:
    archivo: str
    documento: str
    codigo: str | None
    seccion: str
    orden: int
    texto: str

    def para_embedding(self) -> str:
        # El encabezado da contexto al vector: «Política X — 3. Criterios de bloqueo».
        return f"{self.documento} — {self.seccion}\n{self.texto}"


def _partir(texto: str, maximo: int) -> list[str]:
    if len(texto) <= maximo:
        return [texto]
    partes, actual = [], ""
    for parrafo in re.split(r"\n\s*\n", texto):
        if actual and len(actual) + len(parrafo) + 2 > maximo:
            partes.append(actual.strip())
            actual = ""
        actual += parrafo + "\n\n"
    if actual.strip():
        partes.append(actual.strip())
    return partes


def fragmentar(ruta: Path, maximo: int = 1500) -> list[Fragmento]:
    """Corta un Markdown en fragmentos por sección. Omite el aviso DEMO (líneas `>` del encabezado)."""
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    documento, codigo = ruta.stem.replace("_", " "), None
    bloques: list[tuple[str, list[str]]] = []  # (sección, líneas)
    seccion_h2, actual = "Introducción", ("Introducción", [])
    for linea in lineas:
        if linea.startswith("# ") and not bloques and not actual[1]:
            documento = linea[2:].strip()
            continue
        if linea.startswith(">"):
            m = _CODIGO.search(linea)
            codigo = codigo or (m.group(1) if m else None)
            continue
        if linea.startswith("## "):
            bloques.append(actual)
            seccion_h2 = linea[3:].strip()
            actual = (seccion_h2, [])
            continue
        if linea.startswith("### "):
            bloques.append(actual)
            actual = (f"{seccion_h2} › {linea[4:].strip()}", [])
            continue
        actual[1].append(linea)
    bloques.append(actual)

    fragmentos: list[Fragmento] = []
    arrastre = ""  # texto corto de una sección `##` que solo introduce sus `###`
    for i, (seccion, cuerpo) in enumerate(bloques):
        texto = "\n".join(cuerpo).strip()
        if not texto:
            continue
        siguiente = bloques[i + 1][0] if i + 1 < len(bloques) else ""
        if len(texto) < 120 and " › " not in seccion and siguiente.startswith(f"{seccion} › "):
            arrastre = texto + "\n\n"
            continue
        for k, parte in enumerate(_partir(arrastre + texto, maximo)):
            fragmentos.append(Fragmento(ruta.name, documento, codigo, seccion if k == 0 else f"{seccion} (cont.)",
                                        len(fragmentos), parte))
        arrastre = ""
    return fragmentos


def construir(carpeta: Path, destino: Path, cfg: dict[str, Any], ollama: Any | None = None,
              con_embeddings: bool = True) -> dict[str, Any]:
    """Construye el índice y devuelve un resumen. `ollama` es un ClienteOllama (inyectable en pruebas)."""
    rag = cfg.get("rag", {})
    archivos = sorted(carpeta.glob("*.md"))
    omitidos = sorted(p.name for p in carpeta.glob("*") if p.suffix.lower() == ".pdf")
    if not archivos:
        raise SystemExit(f"No hay políticas .md en {carpeta}.")
    fragmentos = [f for a in archivos for f in fragmentar(a, int(rag.get("max_caracteres_fragmento", 1500)))]

    modelo = cfg["modelos"]["embeddings"]["nombre"]
    vectores: list[list[float]] = []
    if con_embeddings:
        if ollama is None:
            from comun.ollama_http import ClienteOllama

            ollama = ClienteOllama.desde_config(cfg, timeout=300)
        lote = int(rag.get("lote_embeddings", 16))
        for i in range(0, len(fragmentos), lote):
            grupo = fragmentos[i:i + lote]
            vectores += [normalizar(v) for v in ollama.embeddings(modelo, [f.para_embedding() for f in grupo])]
            log.info("embeddings %d/%d", min(i + lote, len(fragmentos)), len(fragmentos))
    dims = len(vectores[0]) if vectores else 0

    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(ESQUEMA_INDICE)
    con_vec = bool(vectores) and cargar_sqlite_vec(con)
    if con_vec:
        con.execute(f"CREATE VIRTUAL TABLE fragmentos_vec USING vec0(embedding float[{dims}] distance_metric=cosine)")
    for i, f in enumerate(fragmentos):
        blob = a_blob(vectores[i]) if vectores else None
        cur = con.execute("INSERT INTO fragmentos (archivo,documento,codigo,seccion,orden,texto,embedding) "
                          "VALUES (?,?,?,?,?,?,?)", (f.archivo, f.documento, f.codigo, f.seccion, f.orden, f.texto, blob))
        if con_vec:
            con.execute("INSERT INTO fragmentos_vec (rowid, embedding) VALUES (?, ?)", (cur.lastrowid, blob))
    con.execute("INSERT INTO fragmentos_fts (fragmentos_fts) VALUES ('rebuild')")
    huella = hashlib.sha256(b"".join(a.read_bytes() for a in archivos)).hexdigest()[:16]
    meta = {"modelo_embeddings": modelo if vectores else "", "dimensiones": str(dims),
            "embeddings": "1" if vectores else "0", "sqlite_vec": "1" if con_vec else "0",
            "documentos": str(len(archivos)), "fragmentos": str(len(fragmentos)), "huella": huella,
            "fecha": datetime.now().isoformat(" ", "seconds"), "aviso": "DEMO / DATOS FICTICIOS"}
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    con.commit()
    con.close()
    os.replace(tmp, destino)
    return {**meta, "destino": str(destino), "pdf_omitidos": omitidos}


def main() -> None:
    logging.basicConfig(level=os.environ.get("CENTINELA_LOG", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sin-embeddings", action="store_true", help="solo índice léxico (sin Ollama)")
    args = p.parse_args()
    cfg = config.cargar()
    try:
        r = construir(config.ruta("politicas"), config.ruta("vectores"), cfg, con_embeddings=not args.sin_embeddings)
    except Exception as e:
        from comun.ollama_http import ErrorOllama

        if isinstance(e, ErrorOllama):
            raise SystemExit(f"{e}\n¿Está corriendo `ollama serve` y descargado {cfg['modelos']['embeddings']['nombre']}? "
                             "Para indexar solo la parte léxica: python -m scripts.ingest --sin-embeddings") from e
        raise
    print(f"Índice listo: {r['documentos']} documentos, {r['fragmentos']} fragmentos, "
          f"embeddings={r['modelo_embeddings'] or 'no'} ({r['dimensiones']} dims), sqlite-vec={r['sqlite_vec'] == '1'} "
          f"→ {r['destino']}")
    if r["pdf_omitidos"]:
        print("PDF omitidos (convierta a Markdown): " + ", ".join(r["pdf_omitidos"]))


if __name__ == "__main__":
    main()
