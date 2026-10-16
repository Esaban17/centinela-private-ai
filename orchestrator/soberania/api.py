"""Rutas /api/soberania/* para el Panel de Soberanía.

El orquestador las monta con:

    from orchestrator.soberania import crear_router
    app.include_router(crear_router(cfg))

Las funciones son síncronas a propósito: FastAPI las corre en su pool de hilos, así
psutil y la consulta a Ollama no bloquean el loop de eventos del agente.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from comun import salida

from .monitor import MonitorSoberania


def crear_router(cfg: dict, monitor: MonitorSoberania | None = None) -> APIRouter:
    monitor = monitor or MonitorSoberania(cfg)
    router = APIRouter(prefix="/api/soberania", tags=["soberania"])

    @router.get("")
    def estado(tokens_entrada: int = Query(0, ge=0), tokens_salida: int = Query(0, ge=0)) -> dict:
        """Instantánea del panel. La UI manda los tokens de `sesion.acumulado` para el costo."""
        return monitor.instantanea(tokens_entrada, tokens_salida)

    @router.get("/conexiones")
    def solo_conexiones() -> dict:
        """Solo el monitor de conexiones (lo usa `make check-offline`)."""
        return monitor.instantanea()["conexiones"]

    @router.post("/reiniciar")
    def reiniciar() -> dict:
        """Borra el historial de conexiones vistas y de intentos bloqueados (botón «Nueva sesión» de la UI)."""
        monitor.max_salientes = 0
        monitor.historial_salientes = []
        salida.limpiar()
        return {"ok": True}

    router.monitor = monitor  # type: ignore[attr-defined]  (acceso en pruebas)
    return router
