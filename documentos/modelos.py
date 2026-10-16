"""Esquemas Pydantic de los documentos que lee el modelo de visión.

Los mismos esquemas sirven para dos cosas: (1) se envían a Ollama como JSON Schema (`format`) para que el
modelo devuelva exactamente esos campos, y (2) validan y normalizan lo que el modelo devolvió (montos con
«Q» y comas, fechas dd/mm/aaaa, números de cuenta con espacios, etc.).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TipoDocumento = Literal["boleta_deposito", "solicitud_credito", "factura"]
Moneda = Literal["GTQ", "USD"]

_CUENTA = re.compile(r"AT\D*(\d{5})\D*(\d{2})", re.IGNORECASE)
_FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y")


def normalizar_cuenta(valor: Any) -> str | None:
    """«AT 00107 01», «at-00107-01», «AT-00107-01» → «AT-00107-01». Si no reconoce el formato, lo deja igual."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    m = _CUENTA.search(texto)
    return f"AT-{m.group(1)}-{m.group(2)}" if m else texto.upper()


def normalizar_monto(valor: Any) -> float | None:
    """«Q 12,500.00», «12500», «$1,250.5», «Q12.500,00» → float."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    texto = re.sub(r"[^\d.,-]", "", str(valor))
    if not texto:
        return None
    if "," in texto and "." in texto:
        # El último separador es el decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes) == 2 and len(partes[1]) <= 2 else texto.replace(",", "")
    return round(float(texto), 2)


def normalizar_fecha(valor: Any) -> date | None:
    if valor is None or isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: «{texto}» (se espera AAAA-MM-DD o DD/MM/AAAA)")


def normalizar_moneda(valor: Any) -> str | None:
    if valor is None:
        return None
    t = str(valor).strip().upper()
    if t in {"Q", "GTQ", "QUETZAL", "QUETZALES"}:
        return "GTQ"
    if t in {"$", "US$", "USD", "DOLAR", "DÓLAR", "DOLARES", "DÓLARES"}:
        return "USD"
    return t


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _vacios_a_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and v.strip() in {"", "-", "—", "N/A", "n/a", "null"} else v


class BoletaDeposito(_Base):
    """Boleta de depósito en agencia."""

    numero_boleta: str | None = Field(None, description="Número impreso de la boleta")
    fecha: date | None = Field(None, description="Fecha del depósito (AAAA-MM-DD)")
    agencia: str | None = Field(None, description="Agencia donde se hizo el depósito")
    cuenta: str = Field(description="Número de cuenta destino, formato AT-00000-00")
    titular: str = Field(description="Nombre del titular de la cuenta, tal como aparece escrito")
    depositante: str | None = Field(None, description="Nombre de quien deposita")
    moneda: Moneda = Field("GTQ", description="GTQ (quetzales) o USD (dólares)")
    monto_efectivo: float | None = Field(None, description="Monto en efectivo")
    monto_cheques: float | None = Field(None, description="Monto en cheques")
    monto_total: float = Field(description="Total depositado")

    _cuenta = field_validator("cuenta", mode="before")(lambda v: normalizar_cuenta(v))
    _fecha = field_validator("fecha", mode="before")(lambda v: normalizar_fecha(v))
    _moneda = field_validator("moneda", mode="before")(lambda v: normalizar_moneda(v) or "GTQ")
    _montos = field_validator("monto_efectivo", "monto_cheques", "monto_total", mode="before")(
        lambda v: normalizar_monto(v))


class SolicitudCredito(_Base):
    """Solicitud de crédito pyme llenada por el cliente."""

    numero_solicitud: str | None = Field(None, description="Número de la solicitud")
    fecha: date | None = Field(None, description="Fecha de la solicitud (AAAA-MM-DD)")
    solicitante: str = Field(description="Nombre completo del solicitante")
    cliente_id: str | None = Field(None, description="Código de cliente si aparece (CLI-0000)")
    empresa: str | None = Field(None, description="Nombre comercial del negocio")
    departamento: str | None = Field(None, description="Departamento de residencia o del negocio")
    tipo_credito: str | None = Field(None, description="Tipo de crédito solicitado")
    moneda: Moneda = Field("GTQ", description="GTQ o USD")
    monto_solicitado: float = Field(description="Monto solicitado")
    plazo_meses: int | None = Field(None, description="Plazo en meses")
    cuenta_desembolso: str = Field(description="Cuenta para el desembolso, formato AT-00000-00")
    saldo_promedio_declarado: float | None = Field(None, description="Saldo promedio mensual que declara tener")
    ingreso_mensual_declarado: float | None = Field(None, description="Ingreso mensual declarado")

    _cuenta = field_validator("cuenta_desembolso", mode="before")(lambda v: normalizar_cuenta(v))
    _fecha = field_validator("fecha", mode="before")(lambda v: normalizar_fecha(v))
    _moneda = field_validator("moneda", mode="before")(lambda v: normalizar_moneda(v) or "GTQ")
    _montos = field_validator("monto_solicitado", "saldo_promedio_declarado", "ingreso_mensual_declarado",
                              mode="before")(lambda v: normalizar_monto(v))

    @field_validator("cliente_id", mode="before")
    @classmethod
    def _cliente(cls, v: Any) -> Any:
        if v is None:
            return None
        m = re.search(r"CLI\D*(\d{1,4})", str(v), re.IGNORECASE)
        return f"CLI-{int(m.group(1)):04d}" if m else None

    @field_validator("plazo_meses", mode="before")
    @classmethod
    def _plazo(cls, v: Any) -> Any:
        if v is None or isinstance(v, int):
            return v
        m = re.search(r"\d+", str(v))
        return int(m.group(0)) if m else None


class Factura(_Base):
    """Factura de un comercio pagada con tarjeta del banco."""

    serie: str | None = Field(None, description="Serie de la factura")
    numero: str | None = Field(None, description="Número de la factura")
    fecha: date | None = Field(None, description="Fecha de emisión (AAAA-MM-DD)")
    comercio: str = Field(description="Nombre del comercio emisor")
    registro_comercio: str | None = Field(None, description="Registro tributario del comercio, tal como aparece")
    cliente_nombre: str = Field(description="Nombre del cliente al que se factura")
    cuenta_tarjeta: str | None = Field(None, description="Cuenta de la tarjeta con que se pagó, formato AT-00000-00")
    moneda: Moneda = Field("GTQ", description="GTQ o USD")
    total: float = Field(description="Total de la factura")

    _cuenta = field_validator("cuenta_tarjeta", mode="before")(lambda v: normalizar_cuenta(v))
    _fecha = field_validator("fecha", mode="before")(lambda v: normalizar_fecha(v))
    _moneda = field_validator("moneda", mode="before")(lambda v: normalizar_moneda(v) or "GTQ")
    _montos = field_validator("total", mode="before")(lambda v: normalizar_monto(v))


ESQUEMAS: dict[str, type[_Base]] = {
    "boleta_deposito": BoletaDeposito,
    "solicitud_credito": SolicitudCredito,
    "factura": Factura,
}

NOMBRES: dict[str, str] = {
    "boleta_deposito": "boleta de depósito",
    "solicitud_credito": "solicitud de crédito",
    "factura": "factura de comercio",
}


class Clasificacion(BaseModel):
    tipo_documento: Literal["boleta_deposito", "solicitud_credito", "factura", "otro"]


def esquema_json(tipo: str) -> dict[str, Any]:
    """JSON Schema del tipo, en la forma que acepta `format` de Ollama (sin validadores privados)."""
    return ESQUEMAS[tipo].model_json_schema()


def validar_campos(tipo: str, campos: dict[str, Any]) -> _Base:
    """Valida y normaliza un dict de campos. Lanza `ValueError` si el tipo no existe o los campos no cuadran."""
    if tipo not in ESQUEMAS:
        raise ValueError(f"tipo de documento desconocido: «{tipo}» (use: {', '.join(ESQUEMAS)})")
    return ESQUEMAS[tipo].model_validate(campos)
