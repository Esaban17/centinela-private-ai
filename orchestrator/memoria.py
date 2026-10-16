"""Memoria de conversación por sesión (en proceso; se pierde al reiniciar el orquestador)."""

from __future__ import annotations

from typing import Any


class Memoria:
    def __init__(self, max_mensajes: int = 40):
        self.max_mensajes = max_mensajes
        self.mensajes: list[dict[str, Any]] = []

    def agregar(self, mensaje: dict[str, Any]) -> None:
        self.mensajes.append(mensaje)
        self._recortar()

    def _recortar(self) -> None:
        """Conserva los últimos mensajes empezando siempre en un mensaje de la persona, para no dejar
        resultados de herramienta huérfanos. El turno en curso nunca se corta, aunque sea largo."""
        if len(self.mensajes) <= self.max_mensajes:
            return
        limite = len(self.mensajes) - self.max_mensajes
        usuarios = [i for i, m in enumerate(self.mensajes) if m["role"] == "user"]
        if not usuarios:
            return
        inicio = next((i for i in usuarios if i >= limite), usuarios[-1])
        self.mensajes = self.mensajes[inicio:]

    def para_llm(self, sistema: str) -> list[dict[str, Any]]:
        return [{"role": "system", "content": sistema}, *self.mensajes]

    def historial(self) -> list[dict[str, Any]]:
        return list(self.mensajes)
