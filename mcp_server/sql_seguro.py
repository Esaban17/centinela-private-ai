"""Validación de SQL de solo lectura para la herramienta `consulta_sql`.

Tres capas independientes:
1. Parser (sqlglot): una sola sentencia, de tipo SELECT/UNION/WITH, sin nodos de escritura.
2. LIMIT forzado envolviendo la consulta.
3. En ejecución: conexión mode=ro + PRAGMA query_only + autorizador de SQLite que solo
   permite leer (ver `autorizador`).
"""

from __future__ import annotations

import sqlite3

import sqlglot
from sqlglot import exp


class SQLNoPermitido(ValueError):
    """La consulta no es un SELECT de solo lectura válido."""


_PROHIBIDOS = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
    exp.Pragma, exp.Attach, exp.Detach, exp.Merge, exp.TruncateTable, exp.Transaction,
)
_TABLAS_OCULTAS = {"sqlite_master", "sqlite_schema", "sqlite_temp_master"}


def validar(sql: str, max_filas: int) -> str:
    """Devuelve la consulta segura (con LIMIT forzado) o lanza SQLNoPermitido."""
    texto = (sql or "").strip().rstrip(";").strip()
    if not texto:
        raise SQLNoPermitido("La consulta está vacía.")
    try:
        sentencias = [s for s in sqlglot.parse(texto, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLNoPermitido(f"No se pudo interpretar el SQL: {e.errors[0]['description'] if e.errors else e}")
    if len(sentencias) != 1:
        raise SQLNoPermitido("Solo se permite una sentencia por consulta.")
    arbol = sentencias[0]
    if not isinstance(arbol, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLNoPermitido("Solo se permiten consultas SELECT.")
    for nodo in arbol.walk():
        if isinstance(nodo, _PROHIBIDOS):
            raise SQLNoPermitido(f"Operación no permitida dentro de la consulta: {nodo.key.upper()}.")
        if isinstance(nodo, exp.Table) and nodo.name.lower() in _TABLAS_OCULTAS:
            raise SQLNoPermitido("Consulte el recurso esquema://banco o la vista v_esquema para ver el esquema.")
    return f"SELECT * FROM ({texto}) LIMIT {int(max_filas)}"


_PERMITIDAS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
_FUNCIONES_PROHIBIDAS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def autorizador(accion: int, arg1, arg2, _db, _origen) -> int:
    """Autorizador de SQLite: solo lectura, sin funciones peligrosas."""
    if accion == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _FUNCIONES_PROHIBIDAS:
        return sqlite3.SQLITE_DENY
    if accion == sqlite3.SQLITE_READ and (arg1 or "").lower() in _TABLAS_OCULTAS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if accion in _PERMITIDAS else sqlite3.SQLITE_DENY
