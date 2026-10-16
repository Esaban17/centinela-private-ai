"""consulta_sql: solo lectura con tres capas (parser, LIMIT forzado, autorizador + conexión ro)."""

import sqlite3

import pytest

from mcp_server import sql_seguro
from mcp_server.db import conectar_lectura

PERMITIDAS = [
    "SELECT * FROM clientes",
    "select segmento, count(*) from clientes group by segmento;",
    "WITH x AS (SELECT * FROM cuentas WHERE moneda='USD') SELECT COUNT(*) FROM x",
    "SELECT id FROM clientes UNION SELECT cliente_id FROM cuentas",
    "SELECT * FROM v_esquema",
]

RECHAZADAS = [
    "DELETE FROM clientes",
    "UPDATE cuentas SET saldo = 0",
    "INSERT INTO tickets (tipo) VALUES ('x')",
    "DROP TABLE auditoria",
    "PRAGMA table_info(clientes)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "SELECT 1; DELETE FROM clientes",
    "WITH a AS (DELETE FROM clientes RETURNING *) SELECT * FROM a",
    "SELECT * FROM sqlite_master",
    "CREATE TABLE x (a)",
    "",
    "esto no es sql",
]


@pytest.mark.parametrize("sql", PERMITIDAS)
def test_permitidas(sql):
    seguro = sql_seguro.validar(sql, 50)
    assert seguro.endswith("LIMIT 50")


@pytest.mark.parametrize("sql", RECHAZADAS)
def test_rechazadas(sql):
    with pytest.raises(sql_seguro.SQLNoPermitido):
        sql_seguro.validar(sql, 50)


def test_limit_forzado_aunque_pidan_mas(db):
    con = conectar_lectura(db)
    filas = con.execute(sql_seguro.validar("SELECT * FROM transacciones LIMIT 100000", 7)).fetchall()
    assert len(filas) == 7


def test_autorizador_bloquea_aunque_el_parser_fallara(db):
    """Defensa en profundidad: incluso sin validar, la conexión no permite escribir."""
    con = conectar_lectura(db)
    con.set_authorizer(sql_seguro.autorizador)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM clientes")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("SELECT * FROM sqlite_master")
    assert con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0] == 200


def test_conexion_de_lectura_es_solo_lectura(db):
    con = conectar_lectura(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM clientes")
