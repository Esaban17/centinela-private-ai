"""`python -m orchestrator` levanta la API en 127.0.0.1 (puerto de config.yaml)."""

import uvicorn

from comun import config
from orchestrator import registro
from orchestrator.app import crear_app


def main() -> None:
    registro.configurar()
    oq = config.cargar()["orquestador"]
    uvicorn.run(crear_app(), host=oq.get("host", "127.0.0.1"), port=int(oq.get("puerto", 8765)), log_config=None)


if __name__ == "__main__":
    main()
