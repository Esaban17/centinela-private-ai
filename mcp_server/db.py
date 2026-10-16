"""Conexiones a SQLite: lectura (solo lectura real) y escritura (tickets y auditoría)."""

from __future__ import annotations

import sqlite3
import time
import unicodedata
from datetime import datetime
from pathlib import Path


def sin_acentos(texto: str | None) -> str:
    """Minúsculas y sin tildes, para búsquedas tolerantes a lo que transcribe la voz."""
    if texto is None:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _preparar(con: sqlite3.Connection) -> sqlite3.Connection:
    con.row_factory = sqlite3.Row
    con.create_function("sin_acentos", 1, sin_acentos, deterministic=True)
    return con


def conectar_lectura(db: Path) -> sqlite3.Connection:
    """Conexión abierta en modo solo lectura a nivel de archivo (mode=ro) y de sesión (query_only)."""
    if not db.exists():
        raise FileNotFoundError(f"No existe la base {db}. Ejecute `make seed` primero.")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return _preparar(con)


def conectar_escritura(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db, check_same_thread=False, timeout=5)
    return _preparar(con)


def fecha_referencia(con: sqlite3.Connection) -> datetime:
    """El «ahora» de la demo, guardado por el seed (ver config.seed.fecha_referencia)."""
    fila = con.execute("SELECT valor FROM meta WHERE clave = 'fecha_referencia'").fetchone()
    return datetime.fromisoformat(fila[0]) if fila else datetime.now().replace(microsecond=0)


def con_limite_de_tiempo(con: sqlite3.Connection, ms: int):
    """Instala un progress handler que aborta consultas que excedan `ms` milisegundos."""
    limite = time.monotonic() + ms / 1000

    def revisar() -> int:
        return 1 if time.monotonic() > limite else 0

    con.set_progress_handler(revisar, 10_000)
    return lambda: con.set_progress_handler(None, 0)
