"""Búsqueda en políticas internas (RAG local): índice y búsqueda híbrida.

`scripts/ingest.py` construye `data/vectores/politicas.db`, un solo archivo SQLite con:

- `fragmentos`: un fragmento por sección (`##`) o subsección (`###`) de cada documento, con su embedding;
- `fragmentos_fts`: índice léxico FTS5 (sin tildes, BM25);
- `fragmentos_vec`: índice vectorial `sqlite-vec` (distancia coseno), si la extensión se puede cargar.

La búsqueda combina el ranking vectorial y el léxico con *Reciprocal Rank Fusion*. Si Ollama o el modelo de
embeddings no responden, la búsqueda léxica sigue devolviendo fragmentos citables (lo dice en `modo`).
Si `sqlite-vec` no se puede cargar en este Python, la similitud se calcula en Python sobre los mismos vectores.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any

from comun import config
from mcp_server.db import sin_acentos

log = logging.getLogger("banco-atitlan.rag")

ESQUEMA_INDICE = """
CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE fragmentos (
    id         INTEGER PRIMARY KEY,
    archivo    TEXT NOT NULL,
    documento  TEXT NOT NULL,
    codigo     TEXT,
    seccion    TEXT NOT NULL,
    orden      INTEGER NOT NULL,
    texto      TEXT NOT NULL,
    embedding  BLOB
);
CREATE VIRTUAL TABLE fragmentos_fts USING fts5(
    documento, seccion, texto, content='fragmentos', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

# Palabras vacías que no aportan a la búsqueda léxica (la consulta suele venir de la voz).
VACIAS = set("""a al ante bajo con contra de del desde durante e el en entre es esta este esto hacia hasta la las
le les lo los mas me mi nos nuestra nuestro o para pero por que qué se segun según sin sobre su sus te tiene
un una uno unos unas y ya cual cuál cuales cuáles como cómo cuando cuándo donde dónde dice decir hay caso
politica política nuestra banco""".split())


def a_blob(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def de_blob(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def normalizar(vector: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / n for x in vector]


def cargar_sqlite_vec(con: sqlite3.Connection) -> bool:
    """Intenta cargar la extensión sqlite-vec. Devuelve False si este Python no permite extensiones."""
    try:
        import sqlite_vec

        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        return True
    except (ImportError, AttributeError, sqlite3.Error) as e:
        log.warning("sqlite-vec no disponible (%s); se usará similitud en Python.", e)
        return False


def consulta_fts(texto: str) -> str | None:
    """Convierte la pregunta en una consulta FTS5 segura: palabras significativas unidas con OR (prefijo)."""
    palabras = [p for p in re.split(r"[^a-z0-9ñ]+", sin_acentos(texto)) if len(p) > 2 and p not in VACIAS]
    vistas: list[str] = []
    for p in palabras:
        if p not in vistas:
            vistas.append(p)
    if not vistas:
        return None
    # Prefijo con raíz de hasta 6 letras: «bloqueos» y «bloqueo» encuentran «bloqueo».
    return " OR ".join(f'"{p[:6]}"*' if len(p) > 6 else f'"{p}"*' for p in vistas[:16])


class IndicePoliticas:
    """Índice de políticas abierto en solo lectura. Se reabre solo si `make ingest` lo reconstruye."""

    def __init__(self, ruta: Path, cfg: dict[str, Any], ollama: Any | None = None):
        self.ruta = ruta
        self.cfg = cfg
        self.rag = cfg.get("rag", {})
        self.modelo = cfg["modelos"]["embeddings"]["nombre"]
        self._ollama = ollama
        self._con: sqlite3.Connection | None = None
        self._mtime: float | None = None
        self.vec = False
        self.meta: dict[str, str] = {}

    @property
    def ollama(self) -> Any:
        if self._ollama is None:
            from comun.ollama_http import ClienteOllama

            self._ollama = ClienteOllama.desde_config(self.cfg, timeout=float(self.rag.get("timeout_embeddings_s", 10)))
        return self._ollama

    def _abrir(self) -> sqlite3.Connection | None:
        if not self.ruta.exists():
            return None
        mtime = self.ruta.stat().st_mtime
        if self._con is not None and mtime == self._mtime:
            return self._con
        if self._con is not None:
            self._con.close()
        con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        self.meta = {f["clave"]: f["valor"] for f in con.execute("SELECT clave, valor FROM meta")}
        self.vec = self.meta.get("sqlite_vec") == "1" and cargar_sqlite_vec(con)
        self._con, self._mtime = con, mtime
        return con

    def _lexica(self, con: sqlite3.Connection, consulta: str, n: int) -> list[int]:
        q = consulta_fts(consulta)
        if q is None:
            return []
        try:
            filas = con.execute("SELECT rowid FROM fragmentos_fts WHERE fragmentos_fts MATCH ? "
                                "ORDER BY bm25(fragmentos_fts, 2.0, 3.0, 1.0) LIMIT ?", (q, n)).fetchall()
        except sqlite3.OperationalError as e:
            log.warning("FTS5 rechazó la consulta %r: %s", q, e)
            return []
        return [f[0] for f in filas]

    def _vectorial(self, con: sqlite3.Connection, vector: list[float], n: int) -> list[int]:
        vector = normalizar(vector)
        if self.vec:
            filas = con.execute("SELECT rowid FROM fragmentos_vec WHERE embedding MATCH ? AND k = ? "
                                "ORDER BY distance", (a_blob(vector), n)).fetchall()
            return [f[0] for f in filas]
        puntajes = []
        for f in con.execute("SELECT id, embedding FROM fragmentos WHERE embedding IS NOT NULL"):
            otro = de_blob(f["embedding"])
            puntajes.append((sum(a * b for a, b in zip(vector, otro)), f["id"]))
        puntajes.sort(reverse=True)
        return [i for _, i in puntajes[:n]]

    def buscar(self, consulta: str, k: int) -> dict[str, Any]:
        con = self._abrir()
        if con is None:
            return {"disponible": False, "modo": None, "fragmentos": [],
                    "mensaje": "El índice de políticas aún no está construido (ejecute `make ingest`). "
                               "No hay fragmentos que citar."}
        n = max(int(self.rag.get("candidatos", 12)), k * 3)
        avisos: list[str] = []
        lexica = self._lexica(con, consulta, n)
        vectorial: list[int] = []
        if self.meta.get("embeddings") != "1":
            avisos.append("El índice se construyó sin embeddings; solo búsqueda léxica.")
        elif self.meta.get("modelo_embeddings") != self.modelo:
            avisos.append(f"El índice usa {self.meta.get('modelo_embeddings')} y la configuración {self.modelo}; "
                          "ejecute `make ingest`. Solo búsqueda léxica.")
        else:
            try:
                vector = self.ollama.embeddings(self.modelo, [consulta])[0]
                vectorial = self._vectorial(con, vector, n)
            except Exception as e:  # Ollama caído o modelo no cargado: seguimos con la léxica
                log.warning("embeddings no disponibles: %s", e)
                avisos.append("El modelo de embeddings no respondió; resultados solo por búsqueda léxica.")

        rrf_k = float(self.rag.get("rrf_k", 60))
        puntaje: dict[int, float] = {}
        fuentes: dict[int, list[str]] = {}
        for nombre, ranking in (("vectorial", vectorial), ("lexica", lexica)):
            for pos, rid in enumerate(ranking, 1):
                puntaje[rid] = puntaje.get(rid, 0.0) + 1.0 / (rrf_k + pos)
                fuentes.setdefault(rid, []).append(nombre)
        canales = (1 if vectorial else 0) + (1 if lexica else 0)
        maximo = canales / (rrf_k + 1) if canales else 1.0
        mejores = sorted(puntaje, key=lambda r: (-puntaje[r], r))[:k]

        fragmentos = []
        for rid in mejores:
            f = con.execute("SELECT archivo, documento, codigo, seccion, texto FROM fragmentos WHERE id=?",
                            (rid,)).fetchone()
            fragmentos.append({
                "documento": f["documento"], "archivo": f["archivo"], "codigo": f["codigo"],
                "seccion": f["seccion"], "fragmento": f["texto"],
                "puntaje": round(puntaje[rid] / maximo, 3), "coincidencia": fuentes[rid],
                "cita": f"{f['documento']}, sección {f['seccion']}",
            })
        modo = "hibrida" if vectorial and lexica else ("vectorial" if vectorial else ("lexica" if lexica else None))
        res: dict[str, Any] = {"disponible": True, "modo": modo, "fragmentos": fragmentos,
                               "motor_vectorial": "sqlite-vec" if self.vec else "python",
                               "instruccion": "Cite documento y sección de los fragmentos que use. Si no responden "
                                              "la pregunta, dígalo."}
        if avisos:
            res["avisos"] = avisos
        if not fragmentos:
            res["mensaje"] = "No encontré fragmentos de política relacionados con la consulta."
        return res


_indices: dict[str, IndicePoliticas] = {}


def obtener_indice(cfg: dict[str, Any], ollama: Any | None = None) -> IndicePoliticas:
    ruta = config.ruta("vectores", cfg)
    clave = str(ruta)
    if clave not in _indices or (ollama is not None and _indices[clave]._ollama is not ollama):
        _indices[clave] = IndicePoliticas(ruta, cfg, ollama)
    return _indices[clave]


def buscar(cfg: dict[str, Any], consulta: str, k: int, ollama: Any | None = None) -> dict[str, Any]:
    return obtener_indice(cfg, ollama).buscar(consulta, k)


def indice(cfg: dict[str, Any]) -> str:
    carpeta = config.ruta("politicas", cfg)
    docs = sorted(p for p in carpeta.glob("*.md")) if carpeta.exists() else []
    if not docs:
        return "# Políticas internas\n\nAún no hay documentos cargados (ejecute `make ingest`)."
    lineas = ["# Políticas internas — Banco Atitlán (DEMO / DATOS FICTICIOS)", ""]
    for p in docs:
        titulo, secciones = p.stem.replace("_", " "), []
        for linea in p.read_text(encoding="utf-8").splitlines():
            if linea.startswith("# ") and titulo == p.stem.replace("_", " "):
                titulo = linea[2:].strip()
            elif linea.startswith("## "):
                secciones.append(linea[3:].strip())
        lineas.append(f"- **{titulo}** (`{p.name}`)")
        lineas += [f"  - {s}" for s in secciones]
    return "\n".join(lineas)
