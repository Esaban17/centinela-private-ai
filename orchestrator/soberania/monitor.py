"""Monitor de soberanía: junta conexiones, red, Ollama, memoria y costo en una instantánea.

Las métricas por turno y el acumulado de la sesión no viven aquí: salen de los eventos
`llm.fin`, `turno.fin` y `sesion.acumulado` del orquestador (docs/CONTRATO_EVENTOS.md §6).
"""

from __future__ import annotations

import time

import psutil

from . import conexiones, costos, ollama, red

PROCESOS_POR_DEFECTO = ["ollama"]


class MonitorSoberania:
    def __init__(self, cfg: dict, pid_raiz: int | None = None):
        self.cfg = cfg
        self.pid_raiz = pid_raiz
        sob = cfg.get("soberania") or {}
        self.procesos = list(sob.get("procesos") or PROCESOS_POR_DEFECTO)
        self.url_ollama = (cfg.get("ollama") or {}).get("url", "http://127.0.0.1:11434")
        # Máximo de conexiones salientes vistas en la sesión: si alguna apareció aunque
        # sea un segundo, el panel lo recuerda (no se "esconde" un pico).
        self.max_salientes = 0
        self.historial_salientes: list[dict] = []

    def instantanea(self, tokens_entrada: int = 0, tokens_salida: int = 0) -> dict:
        """Estado actual. Los tokens acumulados los manda la UI (salen de `sesion.acumulado`)."""
        t0 = time.perf_counter()
        con = conexiones.revisar(self.procesos, self.pid_raiz)
        if con.total_salientes:
            self.max_salientes = max(self.max_salientes, con.total_salientes)
            ahora = time.strftime("%H:%M:%S")
            for c in con.salientes:
                self.historial_salientes.append({"hora": ahora, "proceso": c.proceso, "remoto": c.remoto})
            self.historial_salientes = self.historial_salientes[-20:]

        mem = psutil.virtual_memory()
        return {
            "marca": (self.cfg.get("demo") or {}).get("marca_demo", "DEMO / DATOS FICTICIOS"),
            "hora": time.strftime("%H:%M:%S"),
            "conexiones": {
                **con.a_dict(),
                "max_sesion": self.max_salientes,
                "historial": self.historial_salientes,
            },
            "red": red.estado_red(),
            "ollama": ollama.modelos_cargados(self.url_ollama),
            "sistema": {
                "memoria_total_gb": round(mem.total / 1024 ** 3, 1),
                "memoria_usada_gb": round((mem.total - mem.available) / 1024 ** 3, 1),
                "memoria_pct": mem.percent,
            },
            "costo": costos.costo_equivalente(tokens_entrada, tokens_salida, self.cfg.get("costos")),
            "medicion_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
