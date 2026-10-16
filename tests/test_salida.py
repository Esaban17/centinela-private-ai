"""Guardia de salida: bloquea conexiones y resoluciones DNS fuera del equipo; deja pasar loopback."""

import asyncio
import socket

import httpx
import pytest

from comun import salida


@pytest.fixture
def guardia():
    salida.limpiar()
    salida.activar()
    yield
    salida.desactivar()
    salida.limpiar()


@pytest.mark.parametrize("host,local", [("127.0.0.1", True), ("127.8.0.1", True), ("::1", True), ("[::1]", True),
                                        ("localhost", True), ("::ffff:127.0.0.1", True), ("10.0.0.5", False),
                                        ("192.168.1.10", False), ("8.8.8.8", False), ("example.com", False),
                                        (b"127.0.0.1", True), (None, False)])
def test_es_local(host, local):
    assert salida.es_local(host) is local


def test_bloquea_ip_externa_sin_tocar_la_red(guardia):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(salida.ConexionBloqueada):
            s.connect(("203.0.113.10", 443))  # TEST-NET-3: no existe; la guardia corta antes de intentar
    finally:
        s.close()
    assert salida.bloqueos()[-1]["destino"] == "203.0.113.10:443"


def test_bloquea_dns_y_udp(guardia):
    with pytest.raises(salida.ConexionBloqueada):
        socket.getaddrinfo("telemetria.ejemplo.invalid", 443)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(salida.ConexionBloqueada):
            u.sendto(b"x", ("198.51.100.7", 53))
    finally:
        u.close()
    assert [b["evento"] for b in salida.bloqueos()] == ["socket.getaddrinfo", "socket.sendto"]


def test_deja_pasar_loopback_y_unix(guardia, tmp_path):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    cliente = socket.create_connection(servidor.getsockname(), timeout=2)  # getaddrinfo + connect locales
    cliente.close()
    servidor.close()
    assert socket.getaddrinfo("localhost", 80) and socket.getaddrinfo(None, 8765)
    u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with pytest.raises(OSError) as e:
        u.connect(str(tmp_path / "no-existe.sock"))
    assert not isinstance(e.value, salida.ConexionBloqueada)
    u.close()
    assert salida.bloqueos() == []


async def test_httpx_falla_rapido_con_un_error_de_conexion(guardia):
    """Así lo ven los clientes de la demo: un error de red normal, inmediato, y queda registrado."""
    async with httpx.AsyncClient(trust_env=False) as http:
        with pytest.raises(httpx.ConnectError):
            await asyncio.wait_for(http.get("http://203.0.113.10:11434/api/version"), 2)
    assert salida.bloqueos()


def test_inactiva_no_interfiere():
    salida.desactivar()
    salida.limpiar()
    with pytest.raises(OSError) as e:  # sin guardia: socket.gaierror o el error de red del sistema
        socket.getaddrinfo("nombre-que-no-existe.invalid", 80)
    assert not isinstance(e.value, salida.ConexionBloqueada)
    assert salida.bloqueos() == []


def test_instalar_respeta_la_configuracion():
    try:
        assert salida.instalar({"soberania": {"bloquear_salida": False}}) is False
        assert not salida.activa()
        assert salida.instalar({}) is True and salida.activa()
    finally:
        salida.desactivar()
