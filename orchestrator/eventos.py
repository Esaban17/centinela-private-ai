"""Bus de eventos por sesión (contrato en docs/CONTRATO_EVENTOS.md).

Cada sesión numera sus eventos (`seq`), guarda los últimos en un búfer para reanudar y los reparte a
todos los suscriptores (WebSocket, SSE, CLI o pruebas).
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Any

VERSION_CONTRATO = 1
TAMANO_BUFER = 1000


def ahora_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Bus:
    def __init__(self, sesion_id: str, tamano_bufer: int = TAMANO_BUFER):
        self.sesion_id = sesion_id
        self.seq = 0
        self.bufer: deque[dict[str, Any]] = deque(maxlen=tamano_bufer)
        self._colas: set[asyncio.Queue[dict[str, Any]]] = set()

    def emitir(self, tipo: str, datos: dict[str, Any] | None = None, turno_id: str | None = None) -> dict[str, Any]:
        self.seq += 1
        evento = {"v": VERSION_CONTRATO, "seq": self.seq, "tipo": tipo, "sesion_id": self.sesion_id,
                  "turno_id": turno_id, "ts": ahora_iso(), "datos": datos or {}}
        self.bufer.append(evento)
        for cola in list(self._colas):
            cola.put_nowait(evento)
        return evento

    def suscribir(self, desde_seq: int | None = None) -> asyncio.Queue[dict[str, Any]]:
        cola: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if desde_seq is not None:
            for e in self.bufer:
                if e["seq"] > desde_seq:
                    cola.put_nowait(e)
        self._colas.add(cola)
        return cola

    def desuscribir(self, cola: asyncio.Queue[dict[str, Any]]) -> None:
        self._colas.discard(cola)
