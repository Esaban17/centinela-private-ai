"""El seed es determinista y siembra los casos del guion."""

import hashlib
import sqlite3
from datetime import date

from comun import config
from scripts.seed import generar
from tests.conftest import FECHA_REF


def _huella(ruta) -> str:
    con = sqlite3.connect(ruta)
    h = hashlib.sha256("\n".join(con.iterdump()).encode()).hexdigest()
    con.close()
    return h


def _generar(ruta, fecha=FECHA_REF):
    c = config.cargar()["seed"]
    return generar(ruta, fecha, int(c["semilla"]), int(c["clientes"]), int(c["transacciones_objetivo"]),
                   int(c["dias_historia"]))


def test_determinista(tmp_path):
    _generar(tmp_path / "a.db")
    _generar(tmp_path / "b.db")
    assert _huella(tmp_path / "a.db") == _huella(tmp_path / "b.db")


def test_otra_fecha_cambia_solo_las_fechas(tmp_path):
    r1 = _generar(tmp_path / "a.db")
    r2 = _generar(tmp_path / "b.db", date(2027, 3, 1))
    assert {k: v for k, v in r1.items() if k != "fecha_referencia"} == \
           {k: v for k, v in r2.items() if k != "fecha_referencia"}


def test_volumenes(db_plantilla):
    con = sqlite3.connect(db_plantilla)
    assert con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0] == 200
    assert 4800 <= con.execute("SELECT COUNT(*) FROM transacciones").fetchone()[0] <= 5300
    assert con.execute("SELECT valor FROM meta WHERE clave='aviso'").fetchone()[0] == "DEMO / DATOS FICTICIOS"


def test_casos_del_guion(db_plantilla):
    con = sqlite3.connect(db_plantilla)
    nombre = con.execute("SELECT nombre FROM clientes WHERE id='CLI-0042'").fetchone()[0]
    assert nombre == "Lucía Fernanda Cojtí Barrios"
    nocturnas = con.execute(
        "SELECT COUNT(*) FROM transacciones t JOIN cuentas c ON c.id=t.cuenta_id "
        "WHERE c.cliente_id='CLI-0042' AND t.fecha_hora >= '2026-10-21' "
        "AND CAST(substr(t.fecha_hora,12,2) AS INT) < 6").fetchone()[0]
    assert nocturnas == 6  # 2 compras en Panamá + ráfaga de 4 transferencias
    criticas = con.execute(
        "SELECT COUNT(*) FROM alertas_infra WHERE severidad='critica' AND estado='abierta' "
        "AND fecha >= '2026-11-19 09:00:00'").fetchone()[0]
    assert criticas == 2


def test_integridad_referencial(db_plantilla):
    con = sqlite3.connect(db_plantilla)
    huerfanas = con.execute(
        "SELECT COUNT(*) FROM transacciones t LEFT JOIN cuentas c ON c.id=t.cuenta_id WHERE c.id IS NULL"
    ).fetchone()[0]
    assert huerfanas == 0
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
