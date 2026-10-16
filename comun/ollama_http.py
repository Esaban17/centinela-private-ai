"""Cliente HTTP mínimo para el Ollama local (embeddings y chat con imágenes).

Solo habla con la URL de `config.yaml` (por defecto 127.0.0.1:11434). `trust_env=False` evita
que un proxy del sistema (HTTP(S)_PROXY) desvíe las peticiones fuera del equipo.

Las pruebas inyectan un `httpx.MockTransport` para simular Ollama sin modelos.
"""

from __future__ import annotations

from typing import Any

import httpx


class ErrorOllama(RuntimeError):
    """Ollama no respondió o respondió con error (servicio caído, modelo no descargado, timeout)."""


class ClienteOllama:
    def __init__(self, url: str = "http://127.0.0.1:11434", *, keep_alive: Any = -1, timeout: float = 120.0,
                 transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.keep_alive = keep_alive
        self._http = httpx.Client(base_url=self.url, timeout=timeout, transport=transport, trust_env=False)

    @classmethod
    def desde_config(cls, cfg: dict[str, Any], **kw: Any) -> "ClienteOllama":
        o = cfg.get("ollama", {})
        return cls(o.get("url", "http://127.0.0.1:11434"), keep_alive=o.get("keep_alive", -1), **kw)

    def _post(self, ruta: str, cuerpo: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        try:
            r = self._http.post(ruta, json=cuerpo, **({"timeout": timeout} if timeout else {}))
        except httpx.HTTPError as e:
            raise ErrorOllama(f"No se pudo contactar a Ollama en {self.url}: {e}") from e
        if r.status_code != 200:
            raise ErrorOllama(f"Ollama respondió {r.status_code} en {ruta}: {r.text[:300]}")
        return r.json()

    def embeddings(self, modelo: str, textos: list[str], timeout: float | None = None) -> list[list[float]]:
        """Vectores de `textos` con el modelo de embeddings (POST /api/embed)."""
        if not textos:
            return []
        datos = self._post("/api/embed", {"model": modelo, "input": textos, "keep_alive": self.keep_alive},
                           timeout)
        vectores = datos.get("embeddings") or []
        if len(vectores) != len(textos):
            raise ErrorOllama(f"Ollama devolvió {len(vectores)} vectores para {len(textos)} textos.")
        return vectores

    def chat(self, modelo: str, mensajes: list[dict[str, Any]], *, formato: dict[str, Any] | str | None = None,
             opciones: dict[str, Any] | None = None, think: bool | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        """Chat sin streaming (POST /api/chat). `formato` acepta un JSON Schema (salida estructurada).

        Devuelve la respuesta completa de Ollama: `message.content`, `eval_count`, `total_duration`, etc."""
        cuerpo: dict[str, Any] = {"model": modelo, "messages": mensajes, "stream": False,
                                  "keep_alive": self.keep_alive}
        if think is not None:  # solo se envía si se pide: algunos modelos rechazan el campo
            cuerpo["think"] = think
        if formato is not None:
            cuerpo["format"] = formato
        if opciones:
            cuerpo["options"] = opciones
        return self._post("/api/chat", cuerpo, timeout)

    def cerrar(self) -> None:
        self._http.close()
