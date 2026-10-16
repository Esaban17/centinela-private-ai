"""Segmentación en oraciones y normalización para voz (sin modelos)."""

import pytest

from orchestrator.voz_local.texto import SegmentadorOraciones, limpiar_markdown, normalizar_para_voz, partir_en_oraciones


@pytest.mark.parametrize("entrada,esperado", [
    ("Saldo de Q12,345.67.", "Saldo de 12345 quetzales con 67 centavos."),
    ("Tiene Q1.00 y US$1,500.50", "Tiene un quetzal y 1500 dólares con 50 centavos"),
    ("Monto: 3,200 GTQ", "Monto: 3200 quetzales"),
    ("Pagó $20", "Pagó 20 dólares"),
    ("La cuenta AT-00042-01 y la cuenta AT-•••42-02", "La cuenta terminada en 42 01 y la cuenta terminada en 42 02"),
    ("El cliente CLI-0042 llamó", "El cliente 42 llamó"),
    ("a las 02:14 a. m. desde Zacapa", "a las 2 y 14 de la madrugada desde Zacapa"),
    ("a las 23:40", "a las 11 y 40 de la noche"),
    ("a las 12:00", "a las 12 del mediodía"),
    ("el 2026-11-18", "el 18 de noviembre de 2026"),
    ("el 2026-11-19T07:05:00", "el 19 de noviembre de 2026, a las 7 y 5 de la mañana"),
    ("riesgo 85%", "riesgo 85 por ciento"),
    ("Sr. Pérez y Licda. Gómez", "señor Pérez y licenciada Gómez"),
    ("Total de 1,234,567 transacciones", "Total de 1234567 transacciones"),
])
def test_normalizar(entrada, esperado):
    assert normalizar_para_voz(entrada) == esperado


def test_nunca_dice_la_cuenta_completa():
    assert "00042" not in normalizar_para_voz("Transfirió desde AT-00042-01 a AT-00107-03.")


def test_limpiar_markdown():
    t = limpiar_markdown("**Importante:** vea [la política](politicas/fraude.md) y `consulta_sql` 🚨 [1]")
    assert t.strip() == "Importante: vea la política y consulta_sql"


def test_partir_oraciones_basico():
    assert partir_en_oraciones("Hola, licenciado. ¿Cómo está? ¡Muy bien! Le confirmo que no. Listo") == [
        "Hola, licenciado.", "¿Cómo está?", "¡Muy bien!", "Le confirmo que no.", "Listo"]


def test_no_corta_decimales_ni_abreviaturas():
    assert partir_en_oraciones("La Sra. Cojtí transfirió 3.5 veces su promedio. Revise el No. 5 por favor.") == [
        "La señora Cojtí transfirió 3.5 veces su promedio.", "Revise el número 5 por favor."]


def test_am_pm_y_pregunta_siguiente():
    assert partir_en_oraciones("Fue a las 02:14 a. m. ¿Desea un ticket? Otra a las 3 p. m. Luego nada.") == [
        "Fue a las 2 y 14 de la madrugada.", "¿Desea un ticket?", "Otra a las 3 de la tarde.", "Luego nada."]


def test_descarta_tablas_codigo_y_encabezados():
    texto = ("## Resumen\nHay 2 alertas.\n\n| Componente | Estado |\n|---|---|\n| VKS | crítica. Ver runbook |\n\n"
             "```sql\nSELECT 1. FROM x\n```\n- Revisar el NSX Edge.\n1. Abrir ticket.\n---\nFin.")
    assert partir_en_oraciones(texto, min_caracteres=0) == ["Hay 2 alertas.", "Revisar el NSX Edge.",
                                                            "Abrir ticket.", "Fin."]


def test_streaming_igual_que_completo():
    texto = ("Buenas tardes, Sr. Pérez. Encontré **3 movimientos** inusuales:\n\n| a | b |\n|---|---|\n"
             "1. Transferencia de Q8,000.00 a las 02:14.\n2. Compra en Zacapa.\n¿Desea que abra un ticket? Sí")
    completo = partir_en_oraciones(texto)
    for paso in (1, 2, 5, 13):
        seg = SegmentadorOraciones()
        salida = []
        for i in range(0, len(texto), paso):
            salida += seg.agregar(texto[i:i + paso])
        salida += seg.terminar()
        assert salida == completo, paso


def test_entrega_la_oracion_en_cuanto_termina():
    seg = SegmentadorOraciones()
    assert seg.agregar("Buenas tardes") == []
    assert seg.agregar(".") == []              # todavía puede venir «...» o «.»»
    assert seg.agregar(" La cliente") == ["Buenas tardes."]


def test_junta_oraciones_muy_cortas():
    assert partir_en_oraciones("Sí. Claro. La cuenta está activa.", min_caracteres=8) == [
        "Sí. Claro.", "La cuenta está activa."]


def test_corta_oraciones_muy_largas_en_coma():
    larga = "Revisé las cuentas, " * 20
    partes = partir_en_oraciones(larga, max_caracteres=100)
    assert len(partes) > 1 and all(len(p) <= 100 for p in partes)
