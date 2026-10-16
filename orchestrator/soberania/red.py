"""Indicador de red del equipo (online / sin red).

Se calcula solo mirando las interfaces locales: si hay alguna interfaz activa, que no
sea loopback, con una dirección IPv4 o IPv6 global, el equipo "tiene red". No se hace
ningún ping ni consulta DNS, porque eso sería justamente una conexión saliente.
"""

from __future__ import annotations

import ipaddress
import socket

import psutil

# Interfaces virtuales que no significan "conectado a una red" (macOS y Linux).
_IGNORAR_PREFIJOS = ("lo", "utun", "awdl", "llw", "bridge", "gif", "stf", "anpi", "ap", "docker", "veth", "virbr")


def _direccion_util(familia: int, ip: str) -> bool:
    if familia not in (socket.AF_INET, socket.AF_INET6):
        return False
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


def estado_red() -> dict:
    """{'online': bool, 'interfaces': [...]} con las interfaces que dan conectividad."""
    stats = psutil.net_if_stats()
    activas: list[dict] = []
    for nombre, direcciones in psutil.net_if_addrs().items():
        if nombre.lower().startswith(_IGNORAR_PREFIJOS):
            continue
        st = stats.get(nombre)
        if not st or not st.isup:
            continue
        ips = [d.address.split("%", 1)[0] for d in direcciones if _direccion_util(d.family, d.address)]
        if ips:
            activas.append({"interfaz": nombre, "ips": ips})
    return {"online": bool(activas), "interfaces": activas}
