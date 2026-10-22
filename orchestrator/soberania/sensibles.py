"""«Lo que no salió»: inventario de los datos sensibles que la sesión procesó sin salir del equipo.

Cada resultado de herramienta MCP, documento leído, fragmento de política y grabación de voz se clasifica con los
niveles de la «Política de uso de IA y clasificación de datos» (data/politicas, §2). Con un servicio de IA en la nube,
todo esto habría viajado a un tercero; la §3 de esa política lo prohíbe para los datos restringidos y confidenciales.

Se cuentan valores **distintos** (la misma cuenta vista en tres herramientas cuenta una vez) y solo se guardan
huellas para deduplicar: el inventario no duplica los datos ni los escribe en ningún lado.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

FUENTE = "Política de uso de IA y clasificación de datos, §2"

# Nivel → (etiqueta, [(categoría, singular, plural)]). El orden es el que muestra el panel.
NIVELES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "restringido": ("Restringido", [
        ("clientes", "cliente", "clientes"),
        ("nombres", "nombre de persona", "nombres de personas"),
        ("cuentas", "cuenta o tarjeta", "cuentas y tarjetas"),
        ("montos", "saldo o monto", "saldos y montos"),
        ("movimientos", "movimiento", "movimientos"),
        ("casos", "caso de fraude o ticket", "casos de fraude y tickets"),
        ("documentos", "documento escaneado", "documentos escaneados"),
        ("voz", "grabación de voz", "grabaciones de voz"),
    ]),
    "confidencial": ("Confidencial", [
        ("reglas", "regla antifraude", "reglas antifraude"),
    ]),
    "interno": ("Interno", [
        ("politicas", "sección de manuales", "secciones de manuales"),
    ]),
}

_CLIENTE = re.compile(r"\bCLI-\d{4}\b")
_CUENTA = re.compile(r"\bAT-\d{5}-\d{2}\b")
# Claves que guardan el nombre de una persona (en documentos y resultados de validación).
_CLAVES_NOMBRE = {"titular", "depositante", "solicitante", "cliente_nombre", "nombre_titular"}
# Claves con montos de dinero de clientes.
_CLAVES_MONTO = {"saldo", "monto", "monto_total", "monto_efectivo", "monto_cheques", "monto_solicitado", "total",
                 "total_gtq", "saldo_promedio_declarado", "ingreso_mensual_declarado"}


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto).casefold())
    return " ".join("".join(c for c in t if unicodedata.category(c) != "Mn").split())


def _huella(*partes: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in partes).encode()).hexdigest()[:16]


class Inventario:
    """Datos sensibles distintos vistos en una sesión, por categoría."""

    def __init__(self) -> None:
        self._vistos: dict[str, set[str]] = {cat: set() for _, cats in NIVELES.values() for cat, _, _ in cats}

    def _agregar(self, categoria: str, *valor: Any) -> None:
        self._vistos[categoria].add(_huella(*valor))

    # ─── Entradas ───

    def herramienta(self, nombre: str, argumentos: dict[str, Any] | None, resultado: Any) -> None:
        """Resultado (y argumentos) de una herramienta MCP: lo que el modelo leyó y escribió."""
        self._recorrer(argumentos or {}, None)
        self._recorrer(resultado, None)
        if not isinstance(resultado, dict):
            return
        if nombre == "buscar_politicas":
            for f in resultado.get("fragmentos") or []:
                if isinstance(f, dict) and f.get("documento"):
                    self._agregar("politicas", f.get("documento"), f.get("seccion"))
        if nombre == "detectar_anomalias":
            if resultado.get("total_hallazgos"):
                self._agregar("casos", "anomalias", resultado.get("cliente_id"))
            for regla in (resultado.get("reglas") or {}):
                self._agregar("reglas", regla)

    def documento(self, documento_id: str, campos: dict[str, Any] | None) -> None:
        """Documento escaneado leído por el modelo de visión."""
        self._agregar("documentos", documento_id)
        self._recorrer(campos or {}, None)

    def voz(self, turno_id: str) -> None:
        """Una grabación del micrófono transcrita por Whisper en el equipo."""
        self._agregar("voz", turno_id)

    # ─── Recorrido de resultados ───

    def _recorrer(self, valor: Any, padre: dict[str, Any] | None, clave: str | None = None) -> None:
        if isinstance(valor, dict):
            self._fila(valor)
            for k, v in valor.items():
                self._recorrer(v, valor, k)
        elif isinstance(valor, (list, tuple)):
            for v in valor:
                self._recorrer(v, padre, clave)
        elif isinstance(valor, str):
            for c in _CLIENTE.findall(valor):
                self._agregar("clientes", c)
            for c in _CUENTA.findall(valor):
                self._agregar("cuentas", c)
            if clave in _CLAVES_NOMBRE and valor.strip():
                self._agregar("nombres", _norm(valor))
        elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
            if clave in _CLAVES_MONTO and padre is not None:
                dueno = padre.get("transaccion_id") or padre.get("id") or padre.get("cuenta_id") or ""
                self._agregar("montos", dueno, clave, round(float(valor), 2))

    def _fila(self, d: dict[str, Any]) -> None:
        """Reconoce filas de las tablas del banco por su forma."""
        ident = d.get("id")
        if isinstance(ident, str) and _CLIENTE.fullmatch(ident) and d.get("nombre"):
            self._agregar("nombres", _norm(d["nombre"]))
        if "fecha_hora" in d and "monto" in d:
            trx = d.get("transaccion_id") or d.get("id")
            if trx is not None:
                self._agregar("movimientos", trx)
        for trx in d.get("transacciones") or []:   # ráfagas de detectar_anomalias
            if isinstance(trx, int):
                self._agregar("movimientos", trx)
        if "prioridad" in d and "titulo" in d and ident is not None:
            self._agregar("casos", "ticket", ident)

    # ─── Salida ───

    def resumen(self) -> dict[str, Any]:
        """Lo que va en `sesion.acumulado.datos_sensibles` (docs/CONTRATO_EVENTOS.md)."""
        niveles = []
        for nivel, (etiqueta, cats) in NIVELES.items():
            categorias = [{"id": cat, "etiqueta": singular if n == 1 else plural, "cantidad": n}
                          for cat, singular, plural in cats if (n := len(self._vistos[cat]))]
            niveles.append({"nivel": nivel, "etiqueta": etiqueta,
                            "total": sum(c["cantidad"] for c in categorias), "categorias": categorias})
        return {"total": sum(n["total"] for n in niveles), "niveles": niveles, "fuente": FUENTE}
