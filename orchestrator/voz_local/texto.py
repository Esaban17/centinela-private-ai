"""Texto para voz: partir en oraciones mientras llega el streaming y dejarlo listo para leer en voz alta.

Dos piezas:

- `SegmentadorOraciones`: recibe fragmentos del LLM (`llm.delta`) y devuelve oraciones completas en cuanto
  terminan, para que el TTS empiece a hablar antes de que acabe la respuesta. Descarta lo que no se lee bien
  en voz alta (tablas, bloques de código, encabezados).
- `normalizar_para_voz`: quita Markdown y convierte montos, horas, fechas e identificadores del banco a una
  forma que el fonemizador en español lee bien. Ejemplo: «Q12,345.67» se leía «doce coma trescientos…»;
  ahora es «12345 quetzales con 67 centavos». Las cuentas se dicen solo por su final (nunca completas en voz).
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------------------------------------

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]

_MD_IMAGEN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_ENLACE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CITA_NUM = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
_MD_NEGRITA = re.compile(r"(\*\*|__)(.+?)\1")
_MD_CURSIVA = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w])")
_MD_CODIGO = re.compile(r"`([^`]*)`")
_MD_VINETA = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+")
_MD_ENCABEZADO = re.compile(r"^\s*#{1,6}\s+")
_MD_CITA_BLOQUE = re.compile(r"^\s*>\s?")
_HTML = re.compile(r"<[^>]+>")

# Cuentas del banco ficticio: AT-00042-01 o ya enmascarada AT-•••42-01 → «cuenta terminada en 42 01»
_CUENTA = re.compile(r"\bAT-(?:•••|\*{3})?\d*?(\d{2})-(\d{2})\b")
_CLIENTE = re.compile(r"\bCLI-0*(\d+)\b")
_ID_GENERICO = re.compile(r"\b([A-Z]{2,5})-0*(\d+)\b")

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)"
_MONEDA_PREFIJO = re.compile(r"(?<![\w$])(US\$|USD|GTQ|Q|\$)\s?" + _NUM)
_MONEDA_SUFIJO = re.compile(r"(?<![\w.,])" + _NUM + r"\s?(GTQ|USD|quetzales|dólares)\b")
_MILES = re.compile(r"(?<![\d.,])(\d{1,3})((?:,\d{3})+)(?![\d,])")
_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](?=\d{1,2}:\d{2}))?")
_HORA = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?(?:\s?(a\.\s?m\.|p\.\s?m\.|am\b|pm\b|AM\b|PM\b))?")
_HORA_SOLA = re.compile(r"\b(\d{1,2})()\s?(a\.\s?m\.|p\.\s?m\.)")
_PORCENTAJE = re.compile(r"(\d)\s?%")
_NUMERAL = re.compile(r"(?:N\.º|Nº|N°|#)\s?(\d+)")
_ESPACIOS = re.compile(r"\s+")
_ARTICULO_DOBLE = re.compile(r"\b(?:las?)\s+(las?\s+\d)", re.IGNORECASE)
_EXPANSIONES = [
    (r"\bSr\.", "señor"), (r"\bSra\.", "señora"), (r"\bSrta\.", "señorita"), (r"\bSres\.", "señores"),
    (r"\bDr\.", "doctor"), (r"\bDra\.", "doctora"), (r"\bLic\.", "licenciado"), (r"\bLicda\.", "licenciada"),
    (r"\bIng\.", "ingeniero"), (r"\bInga\.", "ingeniera"), (r"\bUd\.", "usted"), (r"\bUds\.", "ustedes"),
    (r"\betc(?=\.)", "etcétera"), (r"\baprox\.", "aproximadamente"), (r"\bAv\.", "avenida"),
    (r"\b[Nn]o\.\s?(?=\d)", "número "), (r"\b[Nn]úm\.\s?(?=\d)", "número "),
]
_EXPANSIONES = [(re.compile(p), r) for p, r in _EXPANSIONES]


def _es_emoji(c: str) -> bool:
    return unicodedata.category(c) in ("So", "Sk", "Cs") or 0x1F000 <= ord(c) <= 0x1FAFF


def limpiar_markdown(texto: str) -> str:
    """Quita la sintaxis Markdown/HTML que el TTS leería literalmente."""
    t = _MD_ENCABEZADO.sub("", texto)
    t = _MD_CITA_BLOQUE.sub("", t)
    t = _MD_VINETA.sub("", t)
    t = _MD_IMAGEN.sub(r"\1", t)
    t = _MD_ENLACE.sub(r"\1", t)
    t = _MD_CITA_NUM.sub("", t)
    t = _MD_CODIGO.sub(r"\1", t)
    t = _MD_NEGRITA.sub(r"\2", t)
    t = _MD_CURSIVA.sub(lambda m: m.group(1) or m.group(2), t)
    t = _HTML.sub("", t)
    t = "".join(c for c in t if not _es_emoji(c))
    return t.replace("|", ", ").replace("•", "").replace("→", ", ").replace("—", ", ").replace("–", ", ")


def _monto(entero: str, decimales: str | None, moneda: str) -> str:
    entero = entero.replace(",", "")
    dolar = moneda.upper() in ("US$", "USD", "$", "DÓLARES")
    if int(entero) == 1:
        txt = "un dólar" if dolar else "un quetzal"
    else:
        txt = f"{entero} {'dólares' if dolar else 'quetzales'}"
    if decimales and int(decimales.ljust(2, "0")) > 0:
        c = int(decimales.ljust(2, "0"))
        txt += " con un centavo" if c == 1 else f" con {c} centavos"
    return txt


def _hora(m: re.Match) -> str:
    h, mi, sufijo = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(" ", "")
    if h > 23 or mi > 59:
        return m.group(0)
    if sufijo.startswith("p") and h < 12:
        h += 12
    elif sufijo.startswith("a") and h == 12:
        h = 0
    h12 = h % 12 or 12
    articulo = "la" if h12 == 1 else "las"
    minutos = (" en punto" if m.group(2) else "") if mi == 0 else f" y {mi}"
    if h < 6:
        parte = " de la madrugada"
    elif h < 12:
        parte = " de la mañana"
    elif h < 19:
        parte = " de la tarde"
    else:
        parte = " de la noche"
    texto = "las 12 del mediodía" if h == 12 and mi == 0 else f"{articulo} {h12}{minutos}{parte}"
    if sufijo.endswith("."):
        # «… a las 02:14 a. m. ¿Desea…?»: el punto de «m.» también cerraba la oración; se conserva.
        sigue = m.string[m.end():].lstrip()[:1]
        if not sigue or sigue.isupper() or sigue in "¿¡":
            texto += "."
    return texto


def _fecha(m: re.Match) -> str:
    a, me, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= me <= 12 and 1 <= d <= 31):
        return m.group(0)
    sufijo = ", a " if m.group(0)[-1] in "T " else ""
    return f"{d} de {_MESES[me - 1]} de {a}{sufijo}"


def normalizar_para_voz(texto: str) -> str:
    """Deja un texto listo para el TTS en español (Guatemala)."""
    t = limpiar_markdown(texto)
    t = _CUENTA.sub(lambda m: f"cuenta terminada en {m.group(1)} {m.group(2)}", t)
    t = re.sub(r"\b(la\s+)?cuenta\s+cuenta\b", lambda m: (m.group(1) or "") + "cuenta", t, flags=re.I)
    t = _CLIENTE.sub(r"cliente \1", t)
    t = re.sub(r"\bcliente\s+cliente\b", "cliente", t, flags=re.I)
    t = _ID_GENERICO.sub(r"\1 \2", t)
    t = _FECHA_ISO.sub(_fecha, t)
    t = _HORA.sub(_hora, t)
    t = _HORA_SOLA.sub(_hora, t)
    t = _ARTICULO_DOBLE.sub(r"\1", t)          # «a las 02:14» → «a las las 2…» → «a las 2…»
    t = _MONEDA_PREFIJO.sub(lambda m: _monto(m.group(2), m.group(3), m.group(1)), t)
    t = _MONEDA_SUFIJO.sub(lambda m: _monto(m.group(1), m.group(2), m.group(3)), t)
    t = _MILES.sub(lambda m: m.group(1) + m.group(2).replace(",", ""), t)
    t = _PORCENTAJE.sub(r"\1 por ciento", t)
    t = _NUMERAL.sub(r"número \1", t)
    for patron, reemplazo in _EXPANSIONES:
        t = patron.sub(reemplazo, t)
    t = t.replace("&", " y ").replace("§", " sección ").replace("/", " ")
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"(,\s*){2,}", ", ", t)
    return _ESPACIOS.sub(" ", t).strip(" ,;")


def tiene_contenido(texto: str) -> bool:
    return any(c.isalnum() for c in texto)


# ---------------------------------------------------------------------------------------------------------
# Segmentación incremental
# ---------------------------------------------------------------------------------------------------------

# Abreviaturas que terminan en punto pero no cierran la oración.
_ABREVIATURAS = {"sr", "sra", "srta", "sres", "dr", "dra", "lic", "licda", "ing", "inga", "arq", "prof", "profa",
                 "etc", "aprox", "av", "ave", "cía", "cia", "dept", "depto", "tel", "pág", "pag", "vs", "ej",
                 "admón", "mín", "máx", "min", "max", "ud", "uds", "atte", "gral", "s.a", "ltda"}
# Abreviaturas que solo lo son si les sigue un número («No. 5», pero «le confirmo que no.» sí cierra).
_ABREV_CON_NUMERO = {"no", "núm", "num", "nro", "art", "inc", "cap"}
_FINALES = ".!?…"
_CIERRES = "\"'”’»)]*_"
_PALABRA_FINAL = re.compile(r"([\w.]+)$")


class SegmentadorOraciones:
    """Convierte un flujo de fragmentos de texto en oraciones listas para el TTS.

    Uso:
        seg = SegmentadorOraciones()
        for delta in stream:
            for oracion in seg.agregar(delta):
                hablar(oracion)
        for oracion in seg.terminar():
            hablar(oracion)
    """

    def __init__(self, *, min_caracteres: int = 8, max_caracteres: int = 220, normalizar: bool = True):
        self.min_caracteres = min_caracteres
        self.max_caracteres = max_caracteres
        self.normalizar = normalizar
        self._buf = ""
        self._inicio_linea = True
        self._en_codigo = False
        self._descartar_linea = False
        self._pendiente = ""

    # -- API ---------------------------------------------------------------------------------------------
    def agregar(self, delta: str) -> list[str]:
        self._buf += delta
        salida: list[str] = []
        while (corte := self._buscar_corte()) is not None:
            trozo, self._buf = self._buf[:corte], self._buf[corte:]
            salida += self._emitir(trozo)
        while len(self._buf) > self.max_caracteres and not self._en_codigo and not self._descartar_linea:
            corte = self._corte_largo()
            trozo, self._buf = self._buf[:corte], self._buf[corte:]
            salida += self._emitir(trozo)
        return salida

    def terminar(self) -> list[str]:
        salida = self.agregar("")
        salida += self._emitir(self._buf) if self._buf else []
        self._buf = ""
        if self._pendiente:
            salida.append(self._pendiente)
            self._pendiente = ""
        return salida

    # -- Internos ----------------------------------------------------------------------------------------
    def _buscar_corte(self) -> int | None:
        b = self._buf
        for i, c in enumerate(b):
            if c == "\n":
                return i + 1
            if c not in _FINALES:
                continue
            j = i + 1
            while j < len(b) and (b[j] in _FINALES or b[j] in _CIERRES):
                j += 1
            if j >= len(b):
                return None                      # falta ver qué sigue
            if not b[j].isspace():
                continue                         # 3.5, AT-00042, «...?»
            if c == "." and self._es_abreviatura(b, i, j):
                continue
            return j
        return None

    def _es_abreviatura(self, b: str, i: int, j: int) -> bool:
        m = _PALABRA_FINAL.search(b[:i])
        if not m:
            return False
        palabra = m.group(1).lower()
        resto = b[j:].lstrip()
        if resto[:1] in ("¿", "¡"):
            return False                         # «… a. m. ¿Desea…?»: lo que sigue es otra oración
        if palabra == "m" and re.search(r"\b[ap]\.\s?m$", b[:i]) and resto[:1].isupper():
            return False                         # «… a las 2 p. m. Luego…»
        if len(palabra) == 1 and palabra.isalpha():
            return True                          # iniciales y «a. m.»
        if palabra in _ABREVIATURAS:
            return True
        if palabra in _ABREV_CON_NUMERO:
            return not resto or resto[0].isdigit()
        if palabra.isdigit():
            # «1. Revisar…» al inicio de línea es una lista numerada, no una oración.
            inicio = b.rfind("\n", 0, m.start()) + 1
            return b[inicio:m.start()].strip() == "" and self._inicio_linea_en(inicio)
        return False

    def _inicio_linea_en(self, inicio: int) -> bool:
        return inicio > 0 or self._inicio_linea

    def _corte_largo(self) -> int:
        b = self._buf[: self.max_caracteres]
        for sep in (";", ":", ","):
            k = b.rfind(sep)
            if k > self.max_caracteres // 3:
                return k + 1
        k = b.rfind(" ")
        return k + 1 if k > 0 else self.max_caracteres

    def _emitir(self, trozo: str) -> list[str]:
        inicio_linea = self._inicio_linea
        fin_linea = trozo.endswith("\n")
        self._inicio_linea = fin_linea
        crudo = trozo.strip()

        if inicio_linea and crudo.startswith("```"):
            self._en_codigo = not self._en_codigo
            self._descartar_linea = not fin_linea
            return []
        if self._en_codigo:
            return []
        if self._descartar_linea:
            self._descartar_linea = not fin_linea
            return []
        if inicio_linea and (crudo.startswith("|") or crudo.startswith("#") or re.fullmatch(r"[-=*_ ]{3,}", crudo)):
            self._descartar_linea = not fin_linea
            return []

        texto = normalizar_para_voz(crudo) if self.normalizar else crudo
        if not tiene_contenido(texto):
            return []
        if self._pendiente:
            texto = f"{self._pendiente} {texto}"
            self._pendiente = ""
        if len(texto) < self.min_caracteres:
            self._pendiente = texto
            return []
        return [texto]


def partir_en_oraciones(texto: str, *, min_caracteres: int = 8, max_caracteres: int = 220,
                        normalizar: bool = True) -> list[str]:
    """Versión no incremental: todo el texto de una vez."""
    seg = SegmentadorOraciones(min_caracteres=min_caracteres, max_caracteres=max_caracteres, normalizar=normalizar)
    return seg.agregar(texto) + seg.terminar()


def contar_palabras(texto: str) -> int:
    return len(texto.split())
