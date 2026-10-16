"""Guardia de salida: bloquea, dentro del proceso, cualquier conexión a una dirección que no sea local.

El monitor de soberanía *observa* conexiones; esta guardia las *impide* en los procesos Python de la demo
(orquestador, servidor MCP, scripts). Usa un audit hook de Python (PEP 578) sobre `socket.connect` y
`socket.sendto`: si una librería intenta salir del equipo (telemetría, descarga de pesos, un CDN), la llamada
falla al instante con `ConexionBloqueada` y queda en el log y en `bloqueos()`. También se vigilan
`socket.getaddrinfo` y `socket.gethostbyname`: resolver un nombre externo ya es una consulta DNS que sale del equipo
(y `connect` con un nombre resuelve antes de avisar al hook).

Se permite: loopback (127.0.0.0/8, ::1), `localhost` y sockets Unix. Todo lo demás se bloquea, incluida la red
local, por la misma regla del monitor: para la demo, cualquier cosa fuera del equipo es «salir».

    from comun import salida
    salida.instalar(cfg)          # si soberania.bloquear_salida es true (por defecto)

Un audit hook no se puede quitar; `desactivar()` lo deja inerte (lo usan las pruebas).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sys
import threading
import time
import traceback
from typing import Any

log = logging.getLogger("centinela.salida")

_EVENTOS_SOCKET = {"socket.connect", "socket.sendto"}       # (socket, dirección)
_EVENTOS_NOMBRE = {"socket.getaddrinfo", "socket.gethostbyname"}  # (nombre, ...)
_NOMBRES_LOCALES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_MAX_BLOQUEOS = 50

_instalado = False
_activo = False
_bloqueos: list[dict[str, Any]] = []
_candado = threading.Lock()


class ConexionBloqueada(PermissionError):
    """Intento de conexión fuera del equipo bloqueado por la guardia de salida."""


def es_local(host: Any) -> bool:
    """True si `host` (IP o nombre) es del propio equipo."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    h = host.strip().strip("[]").split("%", 1)[0].lower()
    if h in _NOMBRES_LOCALES:
        return True
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        return False  # un nombre que no es localhost obligaría a resolver DNS: eso ya es salir
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _destino(evento: str, args: tuple[Any, ...]) -> tuple[Any, str] | None:
    """(host, texto) del destino de un evento de red, o None si no sale del equipo por IP."""
    if evento in _EVENTOS_NOMBRE:
        host = args[0] if args else None
        if host in (None, "", b""):  # getaddrinfo(None, puerto) es para escuchar, no para salir
            return None
        puerto = args[1] if evento == "socket.getaddrinfo" and len(args) > 1 else None
        return host, f"{host}:{puerto}" if puerto is not None else str(host)
    if len(args) < 2:
        return None
    sock, direccion = args[0], args[1]
    if getattr(sock, "family", None) not in (socket.AF_INET, socket.AF_INET6):
        return None  # sockets Unix y otros: no salen del equipo
    if not isinstance(direccion, tuple) or not direccion:
        return None
    host = direccion[0]
    return host, f"{host}:{direccion[1] if len(direccion) > 1 else '?'}"


def _hook(evento: str, args: tuple[Any, ...]) -> None:
    if not _activo or (evento not in _EVENTOS_SOCKET and evento not in _EVENTOS_NOMBRE):
        return
    destino = _destino(evento, args)
    if destino is None or es_local(destino[0]):
        return
    origen = "".join(traceback.format_stack(limit=6)[:-1]).strip().splitlines()
    registro = {"hora": time.strftime("%H:%M:%S"), "destino": destino[1], "evento": evento,
                "origen": origen[-2].strip() if len(origen) >= 2 else ""}
    with _candado:
        _bloqueos.append(registro)
        del _bloqueos[:-_MAX_BLOQUEOS]
    log.error("CONEXIÓN SALIENTE BLOQUEADA → %s (%s) · %s", destino[1], evento, registro["origen"])
    raise ConexionBloqueada(f"Centinela bloquea conexiones fuera del equipo ({destino[1]}).")


def activar() -> None:
    global _instalado, _activo
    if not _instalado:
        sys.addaudithook(_hook)
        _instalado = True
    _activo = True


def desactivar() -> None:
    global _activo
    _activo = False


def activa() -> bool:
    return _activo


def bloqueos() -> list[dict[str, Any]]:
    with _candado:
        return list(_bloqueos)


def limpiar() -> None:
    with _candado:
        _bloqueos.clear()


def instalar(cfg: dict[str, Any]) -> bool:
    """Activa la guardia si `soberania.bloquear_salida` no es false. Devuelve si quedó activa."""
    if (cfg.get("soberania") or {}).get("bloquear_salida", True) is False:
        log.info("guardia de salida desactivada en config.yaml (soberania.bloquear_salida: false)")
        return False
    activar()
    log.info("guardia de salida activa: solo se permiten conexiones a este equipo")
    return True
