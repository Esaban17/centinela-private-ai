"""Monitor de conexiones salientes de los procesos de la demo.

Revisa, proceso por proceso, las conexiones de red de:
  - el proceso actual (orquestador) y todos sus descendientes (el servidor MCP corre
    como hijo por stdio),
  - los procesos cuyo nombre coincide con `soberania.procesos` (por defecto Ollama y
    sus runners).

Cuenta como "saliente a internet" toda conexión con dirección remota que no sea
loopback (127.0.0.0/8, ::1). Las conexiones a la red local también cuentan: para la
demo, cualquier cosa fuera del equipo es "salir".

En macOS `psutil.net_connections()` global requiere root, por eso se consulta por PID
(los procesos de la demo corren con el mismo usuario). Ver docs/DECISIONES.md §3.7.

Nunca abre conexiones: solo observa.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import asdict, dataclass, field

import psutil


@dataclass
class Conexion:
    pid: int
    proceso: str
    local: str
    remoto: str
    estado: str
    protocolo: str


@dataclass
class ResultadoConexiones:
    salientes: list[Conexion] = field(default_factory=list)
    procesos: list[dict] = field(default_factory=list)      # procesos vigilados
    sin_permiso: list[dict] = field(default_factory=list)   # procesos que no se pudieron revisar
    loopback: int = 0                                        # conexiones locales observadas (informativo)

    @property
    def total_salientes(self) -> int:
        return len(self.salientes)

    def a_dict(self) -> dict:
        return {
            "salientes": self.total_salientes,
            "detalle": [asdict(c) for c in self.salientes],
            "procesos": self.procesos,
            "sin_permiso": self.sin_permiso,
            "loopback": self.loopback,
        }


def es_loopback(ip: str) -> bool:
    """True si la IP es loopback (incluye IPv4 mapeada en IPv6 y zonas `%lo0`)."""
    ip = ip.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _fmt(direccion) -> str:
    if not direccion:
        return ""
    ip, puerto = direccion.ip, direccion.port
    return f"[{ip}]:{puerto}" if ":" in ip else f"{ip}:{puerto}"


def procesos_demo(nombres: list[str], pid_raiz: int | None = None) -> dict[int, psutil.Process]:
    """Procesos a vigilar: raíz + descendientes + los que coinciden por nombre."""
    vigilados: dict[int, psutil.Process] = {}
    try:
        raiz = psutil.Process(pid_raiz or os.getpid())
        vigilados[raiz.pid] = raiz
        for hijo in raiz.children(recursive=True):
            vigilados[hijo.pid] = hijo
    except psutil.Error:
        pass

    nombres_l = [n.lower() for n in nombres if n]
    if nombres_l:
        for p in psutil.process_iter(["name", "exe"]):
            nombre = (p.info.get("name") or "").lower()
            exe = os.path.basename(p.info.get("exe") or "").lower()
            if any(n in nombre or n == exe for n in nombres_l):
                vigilados.setdefault(p.pid, p)
    return vigilados


def revisar(nombres: list[str], pid_raiz: int | None = None) -> ResultadoConexiones:
    """Inspecciona las conexiones inet de los procesos de la demo."""
    res = ResultadoConexiones()
    for pid, proc in sorted(procesos_demo(nombres, pid_raiz).items()):
        try:
            nombre = proc.name()
            conexiones = proc.net_connections(kind="inet")
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess):
            res.sin_permiso.append({"pid": pid, "proceso": _nombre_seguro(proc)})
            continue
        res.procesos.append({"pid": pid, "proceso": nombre})
        for c in conexiones:
            if not c.raddr:          # sockets en escucha o sin conectar: no salen a ningún lado
                continue
            if es_loopback(c.raddr.ip):
                res.loopback += 1
                continue
            res.salientes.append(Conexion(
                pid=pid,
                proceso=nombre,
                local=_fmt(c.laddr),
                remoto=_fmt(c.raddr),
                estado=c.status or "",
                protocolo="tcp" if c.type == socket.SOCK_STREAM else "udp",
            ))
    return res


def _nombre_seguro(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except psutil.Error:
        return "?"
