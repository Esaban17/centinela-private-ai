"""Fixtures: base sembrada en un directorio temporal y servidor MCP en memoria."""

import copy
import shutil
from datetime import date
from pathlib import Path

import pytest

from comun import config
from scripts.seed import generar

FECHA_REF = date(2026, 11, 20)


@pytest.fixture(scope="session")
def db_plantilla(tmp_path_factory) -> Path:
    cfg = config.cargar()["seed"]
    ruta = tmp_path_factory.mktemp("plantilla") / "banco.db"
    generar(ruta, FECHA_REF, int(cfg["semilla"]), int(cfg["clientes"]), int(cfg["transacciones_objetivo"]),
            int(cfg["dias_historia"]))
    return ruta


@pytest.fixture
def db(db_plantilla, tmp_path) -> Path:
    """Copia fresca por prueba, para que las escrituras no se filtren entre pruebas."""
    ruta = tmp_path / "banco.db"
    shutil.copy(db_plantilla, ruta)
    return ruta


@pytest.fixture
def cfg() -> dict:
    return copy.deepcopy(config.cargar())


@pytest.fixture
def servidor(db, cfg):
    from mcp_server.server import crear_servidor

    return crear_servidor(db, cfg)
