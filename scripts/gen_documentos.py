"""Genera los documentos demo (imágenes PNG) en data/documentos_demo/ a partir de banco.db.

    make documentos        # después de `make seed` (los documentos usan cuentas y movimientos reales de la base)

Cada imagen lleva la marca «DEMO / DATOS FICTICIOS». Formatos inventados: no imitan documentos de ningún
banco ni institución real, ni documentos de identidad oficiales.

| Archivo | Caso | Resultado esperado |
|---|---|---|
| boleta_deposito_ok.png | depósito real de un cliente | todo coincide |
| boleta_deposito_discrepancia.png | cuenta de CLI-0107, titular escrito con otro nombre | la cuenta existe y el depósito cuadra, pero el titular no coincide |
| solicitud_credito_discrepancia.png | solicitud pyme de CLI-0107 | titular distinto y saldo declarado inflado |
| factura_comercio_ok.png | compra con tarjeta de CLI-0042 | todo coincide |

`manifiesto.json` guarda los campos esperados de cada documento (sirve a las pruebas y a `ensayo.py`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from comun import config

MARCA = "DEMO / DATOS FICTICIOS"
BANCO = "Banco Atitlán (ficticio)"
NOMBRE_FALSO_0107 = "Carlos Eduardo Xicay Morales"  # parecido, pero no es el titular registrado

TINTA, GRIS, AZUL, ROJO = (25, 25, 35), (110, 110, 120), (20, 60, 120), (200, 40, 40)


class Lienzo:
    def __init__(self, ancho: int, alto: int, fuentes: list[str], negritas: list[str]):
        self.img = Image.new("RGB", (ancho, alto), (252, 251, 247))
        self.d = ImageDraw.Draw(self.img)
        self._fuente = next((f for f in fuentes if Path(f).exists()), None)
        self._negrita = next((f for f in negritas if Path(f).exists()), self._fuente)
        if self._fuente is None:
            raise SystemExit("No encontré una fuente con tildes. Agregue una ruta en config.yaml → vision.fuentes.")

    def f(self, tam: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self._negrita if negrita else self._fuente, tam)

    def texto(self, xy: tuple[int, int], t: str, tam: int = 26, negrita: bool = False, color=TINTA) -> None:
        self.d.text(xy, t, font=self.f(tam, negrita), fill=color)

    def campo(self, x: int, y: int, etiqueta: str, valor: str, ancho: int, tam: int = 28) -> None:
        """Etiqueta pequeña arriba, valor «escrito a mano» sobre una línea."""
        self.texto((x, y), etiqueta, 18, color=GRIS)
        self.texto((x + 6, y + 24), valor, tam, color=(15, 30, 90))
        self.d.line((x, y + 26 + tam + 6, x + ancho, y + 26 + tam + 6), fill=GRIS, width=2)

    def encabezado(self, titulo: str, subtitulo: str) -> None:
        w = self.img.width
        self.d.rectangle((0, 0, w, 110), fill=AZUL)
        self.texto((40, 22), BANCO, 40, True, (255, 255, 255))
        self.texto((40, 72), subtitulo, 20, color=(210, 220, 240))
        ancho = self.d.textlength(titulo, font=self.f(34, True))
        self.texto((w - 40 - int(ancho), 36), titulo, 34, True, (255, 255, 255))
        self.d.rectangle((0, 110, w, 150), fill=ROJO)
        aviso = f"{MARCA} · documento de demostración sin validez"
        ancho = self.d.textlength(aviso, font=self.f(22, True))
        self.texto(((w - int(ancho)) // 2, 117), aviso, 22, True, (255, 255, 255))

    def marca_agua(self) -> None:
        capa = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        dc = ImageDraw.Draw(capa)
        fuente = self.f(max(60, self.img.width // 12), True)
        paso = self.img.height // 3
        for y in range(paso // 2, self.img.height, paso):
            dc.text((self.img.width // 8, y), MARCA, font=fuente, fill=(200, 40, 40, 30))
        capa = capa.rotate(18, center=(self.img.width // 2, self.img.height // 2))
        self.img = Image.alpha_composite(self.img.convert("RGBA"), capa).convert("RGB")
        self.d = ImageDraw.Draw(self.img)

    def pie(self) -> None:
        h = self.img.height
        self.d.line((40, h - 70, self.img.width - 40, h - 70), fill=GRIS, width=1)
        self.texto((40, h - 58), f"{MARCA} · banco inexistente; nombres, cuentas y montos inventados.", 17,
                   color=GRIS)

    def guardar(self, ruta: Path) -> None:
        self.marca_agua()
        self.pie()
        self.img.save(ruta, format="PNG", optimize=True)


def q(monto: float, moneda: str = "GTQ") -> str:
    return f"{'Q' if moneda == 'GTQ' else 'US$'} {monto:,.2f}"


def dmy(fecha_hora: str) -> str:
    return datetime.fromisoformat(fecha_hora).strftime("%d/%m/%Y")


def _fila(con: sqlite3.Connection, sql: str, args: tuple = ()) -> dict[str, Any]:
    f = con.execute(sql, args).fetchone()
    if f is None:
        raise SystemExit(f"La base no tiene los datos esperados para los documentos demo ({sql[:60]}…).")
    return dict(f)


def boleta(cv: Lienzo, numero: str, d: dict[str, Any], agencia: str, titular: str, depositante: str) -> None:
    cv.encabezado("BOLETA DE DEPÓSITO", "Depósito en cuenta monetaria o de ahorro")
    y = 190
    cv.campo(40, y, "No. de boleta", numero, 300)
    cv.campo(380, y, "Fecha", dmy(d["fecha_hora"]), 260)
    cv.campo(680, y, "Agencia", agencia, 680)
    y += 110
    cv.campo(40, y, "Número de cuenta", d["cuenta_id"], 420, 34)
    cv.campo(500, y, "Moneda", "Quetzales (GTQ)" if d["moneda"] == "GTQ" else "Dólares (USD)", 300)
    y += 110
    cv.campo(40, y, "Nombre del titular de la cuenta", titular, 820, 32)
    y += 110
    cv.campo(40, y, "Nombre de quien deposita", depositante, 820)
    caja_x = 900
    cv.d.rectangle((caja_x, 410, 1360, 640), outline=GRIS, width=2)
    cv.texto((caja_x + 20, 425), "Detalle del depósito", 20, True, GRIS)
    cv.texto((caja_x + 20, 470), "Efectivo", 22, color=GRIS)
    cv.texto((caja_x + 200, 466), q(d["monto"], d["moneda"]), 28, color=(15, 30, 90))
    cv.texto((caja_x + 20, 520), "Cheques", 22, color=GRIS)
    cv.texto((caja_x + 200, 516), q(0, d["moneda"]), 28, color=(15, 30, 90))
    cv.d.line((caja_x + 20, 568, 1340, 568), fill=GRIS, width=2)
    cv.texto((caja_x + 20, 582), "TOTAL", 26, True)
    cv.texto((caja_x + 200, 578), q(d["monto"], d["moneda"]), 32, True)
    cv.texto((40, 660), "Sello de caja: RECIBIDO (demo)     Firma del depositante: ____________________", 20,
             color=GRIS)


def generar(db: Path, carpeta: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    v = cfg.get("vision", {})
    fuentes, negritas = v.get("fuentes", []), v.get("fuentes_negrita", [])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto: dict[str, Any] = {"aviso": MARCA, "generado": datetime.now().isoformat(" ", "seconds"),
                                  "fecha_referencia": _fila(con, "SELECT valor FROM meta WHERE clave='fecha_referencia'")
                                  ["valor"], "documentos": {}}

    # 1. Boleta correcta: el depósito más reciente de un cliente que no es del guion.
    d = _fila(con, "SELECT t.id, t.cuenta_id, t.fecha_hora, t.monto, t.ubicacion, c.moneda, cl.nombre FROM transacciones t "
                   "JOIN cuentas c ON c.id=t.cuenta_id JOIN clientes cl ON cl.id=c.cliente_id "
                   "WHERE t.tipo='deposito' AND c.tipo IN ('monetaria','ahorro') "
                   "AND cl.id NOT IN ('CLI-0042','CLI-0107') ORDER BY t.fecha_hora DESC, t.id DESC LIMIT 1")
    cv = Lienzo(1400, 760, fuentes, negritas)
    numero = f"DEP-{d['id']:07d}"
    agencia = f"Agencia {d['ubicacion']} (ficticia)"
    boleta(cv, numero, d, agencia, d["nombre"], d["nombre"])
    cv.guardar(carpeta / "boleta_deposito_ok.png")
    manifiesto["documentos"]["boleta_deposito_ok.png"] = {
        "tipo_documento": "boleta_deposito", "veredicto_esperado": "coincide",
        "campos": {"numero_boleta": numero, "fecha": d["fecha_hora"][:10], "agencia": agencia, "cuenta": d["cuenta_id"],
                   "titular": d["nombre"], "depositante": d["nombre"], "moneda": d["moneda"],
                   "monto_efectivo": d["monto"], "monto_cheques": 0.0, "monto_total": d["monto"]}}

    # 2. Boleta con discrepancia: cuenta de CLI-0107 y depósito real, pero otro nombre de titular.
    d = _fila(con, "SELECT t.id, t.cuenta_id, t.fecha_hora, t.monto, t.ubicacion, c.moneda FROM transacciones t "
                   "JOIN cuentas c ON c.id=t.cuenta_id WHERE c.cliente_id='CLI-0107' AND t.tipo='deposito' "
                   "ORDER BY t.fecha_hora DESC, t.id DESC LIMIT 1")
    cv = Lienzo(1400, 760, fuentes, negritas)
    numero = f"DEP-{d['id']:07d}"
    agencia = "Agencia Antigua Guatemala (ficticia)"
    boleta(cv, numero, d, agencia, NOMBRE_FALSO_0107, NOMBRE_FALSO_0107)
    cv.guardar(carpeta / "boleta_deposito_discrepancia.png")
    manifiesto["documentos"]["boleta_deposito_discrepancia.png"] = {
        "tipo_documento": "boleta_deposito", "veredicto_esperado": "con_discrepancias",
        "discrepancias_esperadas": ["titular_coincide"],
        "campos": {"numero_boleta": numero, "fecha": d["fecha_hora"][:10], "agencia": agencia, "cuenta": d["cuenta_id"],
                   "titular": NOMBRE_FALSO_0107, "depositante": NOMBRE_FALSO_0107, "moneda": d["moneda"],
                   "monto_efectivo": d["monto"], "monto_cheques": 0.0, "monto_total": d["monto"]}}

    # 3. Solicitud de crédito pyme de CLI-0107 con titular distinto y saldo declarado inflado.
    ref = manifiesto["fecha_referencia"]
    cta = _fila(con, "SELECT id FROM cuentas WHERE cliente_id='CLI-0107' AND tipo='monetaria' ORDER BY id LIMIT 1")
    campos_sol = {"numero_solicitud": "SC-2026-00418", "fecha": ref[:10], "solicitante": NOMBRE_FALSO_0107,
                  "empresa": "Comercial El Volcán (ficticia)", "departamento": "Sacatepéquez",
                  "tipo_credito": "Capital de trabajo pyme", "moneda": "GTQ", "monto_solicitado": 250000.0,
                  "plazo_meses": 36, "cuenta_desembolso": cta["id"], "saldo_promedio_declarado": 480000.0,
                  "ingreso_mensual_declarado": 65000.0}
    cv = Lienzo(1240, 1500, fuentes, negritas)
    cv.encabezado("SOLICITUD DE CRÉDITO", "Crédito pyme · formulario de demostración")
    y = 190
    cv.campo(40, y, "No. de solicitud", campos_sol["numero_solicitud"], 360)
    cv.campo(440, y, "Fecha", dmy(ref), 300)
    y += 120
    cv.texto((40, y), "1. Datos del solicitante", 26, True, AZUL)
    y += 50
    cv.campo(40, y, "Nombre completo del solicitante", campos_sol["solicitante"], 1150, 32)
    y += 110
    cv.campo(40, y, "Nombre comercial del negocio", campos_sol["empresa"], 700)
    cv.campo(780, y, "Departamento", campos_sol["departamento"], 410)
    y += 130
    cv.texto((40, y), "2. Crédito solicitado", 26, True, AZUL)
    y += 50
    cv.campo(40, y, "Tipo de crédito", campos_sol["tipo_credito"], 560)
    cv.campo(640, y, "Monto solicitado", q(campos_sol["monto_solicitado"]), 330, 32)
    cv.campo(1000, y, "Plazo", f"{campos_sol['plazo_meses']} meses", 190)
    y += 110
    cv.campo(40, y, "Cuenta para el desembolso", campos_sol["cuenta_desembolso"], 560, 32)
    cv.campo(640, y, "Moneda", "Quetzales (GTQ)", 330)
    y += 130
    cv.texto((40, y), "3. Información financiera declarada", 26, True, AZUL)
    y += 50
    cv.campo(40, y, "Saldo promedio mensual en Banco Atitlán", q(campos_sol["saldo_promedio_declarado"]), 560, 32)
    cv.campo(640, y, "Ingreso mensual del negocio", q(campos_sol["ingreso_mensual_declarado"]), 550, 32)
    y += 140
    cv.texto((40, y), "Declaro que la información es verídica (texto de demostración).", 20, color=GRIS)
    cv.texto((40, y + 70), "Firma del solicitante: ______________________________", 22, color=GRIS)
    cv.guardar(carpeta / "solicitud_credito_discrepancia.png")
    manifiesto["documentos"]["solicitud_credito_discrepancia.png"] = {
        "tipo_documento": "solicitud_credito", "veredicto_esperado": "con_discrepancias",
        "discrepancias_esperadas": ["titular_coincide", "saldo_declarado"], "campos": campos_sol}

    # 4. Factura correcta: compra diurna reciente con la tarjeta de CLI-0042 en Quetzaltenango.
    d = _fila(con, "SELECT t.id, t.cuenta_id, t.fecha_hora, t.monto, t.comercio, c.moneda, cl.nombre FROM transacciones t "
                   "JOIN cuentas c ON c.id=t.cuenta_id JOIN clientes cl ON cl.id=c.cliente_id "
                   "WHERE c.cliente_id='CLI-0042' AND c.tipo='tarjeta' AND t.tipo='compra' "
                   "AND t.ubicacion='Quetzaltenango' AND CAST(substr(t.fecha_hora,12,2) AS INT) BETWEEN 8 AND 19 "
                   "ORDER BY t.fecha_hora DESC, t.id DESC LIMIT 1")
    comercio = d["comercio"] if "ficti" in d["comercio"].lower() else f"{d['comercio']} (ficticio)"
    numero = f"{d['id']:08d}"
    registro = f"RT-FICT-{d['id'] % 9000 + 1000}"
    cv = Lienzo(1000, 1300, fuentes, negritas)
    cv.encabezado("FACTURA", "Comercio afiliado · pago con tarjeta")
    y = 180
    cv.texto((40, y), comercio, 38, True)
    cv.texto((40, y + 52), f"Registro tributario (ficticio): {registro} · Quetzaltenango", 20, color=GRIS)
    y += 120
    cv.campo(40, y, "Serie", "A-DEMO", 200)
    cv.campo(280, y, "Número", numero, 300)
    cv.campo(620, y, "Fecha de emisión", dmy(d["fecha_hora"]), 330)
    y += 110
    cv.campo(40, y, "Cliente", d["nombre"], 910, 32)
    y += 130
    cv.d.rectangle((40, y, 960, y + 230), outline=GRIS, width=2)
    cv.texto((60, y + 15), "Descripción", 20, True, GRIS)
    cv.texto((760, y + 15), "Importe", 20, True, GRIS)
    cv.texto((60, y + 60), "Consumo según detalle de caja (demo)", 24)
    cv.texto((760, y + 60), q(d["monto"], d["moneda"]), 24)
    cv.d.line((60, y + 160, 940, y + 160), fill=GRIS, width=2)
    cv.texto((60, y + 175), "TOTAL A PAGAR", 28, True)
    cv.texto((740, y + 172), q(d["monto"], d["moneda"]), 32, True)
    y += 270
    cv.campo(40, y, "Pagado con tarjeta Banco Atitlán, cuenta", d["cuenta_id"], 500, 30)
    cv.texto((40, y + 120), "Gracias por su compra. Documento de demostración: no es una factura válida.", 20,
             color=GRIS)
    cv.guardar(carpeta / "factura_comercio_ok.png")
    manifiesto["documentos"]["factura_comercio_ok.png"] = {
        "tipo_documento": "factura", "veredicto_esperado": "coincide",
        "campos": {"serie": "A-DEMO", "numero": numero, "fecha": d["fecha_hora"][:10], "comercio": comercio,
                   "registro_comercio": registro, "cliente_nombre": d["nombre"], "cuenta_tarjeta": d["cuenta_id"],
                   "moneda": d["moneda"], "total": d["monto"]}}

    con.close()
    (carpeta / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifiesto


def main() -> None:
    cfg = config.cargar()
    db = config.ruta("db")
    if not db.exists():
        raise SystemExit(f"No existe {db}. Ejecute `make seed` primero.")
    m = generar(db, config.ruta("documentos_demo"), cfg)
    for nombre, info in m["documentos"].items():
        print(f"{nombre:38} {info['tipo_documento']:18} esperado: {info['veredicto_esperado']}")


if __name__ == "__main__":
    main()
