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

    def para_llm(self, sistema: str, turnos_previos: int | None = None) -> list[dict[str, Any]]:
        """`turnos_previos`: None = toda la memoria. Un número N = el turno en curso completo y, de los N turnos
        anteriores, solo la pregunta y la respuesta final (sin llamadas ni resultados de herramientas). Con un modelo
        chico (Llama 3.1 8B) el historial completo lo hace repetir el dictamen anterior en vez de responder."""
        if turnos_previos is None:
            return [{"role": "system", "content": sistema}, *self.mensajes]
        usuarios = [i for i, m in enumerate(self.mensajes) if m["role"] == "user"]
        if not usuarios:
            return [{"role": "system", "content": sistema}, *self.mensajes]
        actual = self.mensajes[usuarios[-1]:]
        previos: list[dict[str, Any]] = []
        anteriores = usuarios[:-1][-turnos_previos:] if turnos_previos > 0 else []
        limites = anteriores + [usuarios[-1]]
        for a, b in zip(limites[:-1], limites[1:]):
            finales = [m for m in self.mensajes[a + 1:b] if m["role"] == "assistant" and not m.get("tool_calls")
                       and m.get("content")]
            previos.append(self.mensajes[a])
            if finales:
                previos.append({"role": "assistant", "content": finales[-1]["content"]})
        return [{"role": "system", "content": sistema}, *previos, *actual]

    def historial(self) -> list[dict[str, Any]]:
        return list(self.mensajes)
