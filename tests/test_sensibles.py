"""«Lo que no salió»: clasificación de datos sensibles por nivel (orchestrator/soberania/sensibles.py)."""

from orchestrator.soberania.sensibles import Inventario


def _cantidades(inv: Inventario) -> dict[str, int]:
    return {c["id"]: c["cantidad"] for n in inv.resumen()["niveles"] for c in n["categorias"]}


def test_vacio():
    r = Inventario().resumen()
    assert r["total"] == 0
    assert [n["nivel"] for n in r["niveles"]] == ["restringido", "confidencial", "interno"]
    assert all(n["categorias"] == [] for n in r["niveles"])
    assert "Política de uso de IA" in r["fuente"]


def test_ficha_de_cliente_cuenta_valores_distintos():
    inv = Inventario()
    ficha = {"encontrado": True,
             "cliente": {"id": "CLI-0042", "nombre": "Lucía Fernanda Cojtí Barrios", "ejecutivo": "Ana López"},
             "productos": [{"id": "AT-00042-01", "tipo": "monetaria", "saldo": 48350.75},
                           {"id": "AT-00042-02", "tipo": "ahorro", "saldo": 1200.0}],
             "tickets_abiertos": [{"id": 7, "tipo": "servicio", "prioridad": "media", "titulo": "Reposición"}]}
    inv.herramienta("resumen_cliente", {"cliente_id": "CLI-0042"}, ficha)
    # La misma clienta y cuenta vistas otra vez (con tildes distintas) no suman.
    inv.herramienta("buscar_cliente", {"texto": "lucia cojti"},
                    {"clientes": [{"id": "CLI-0042", "nombre": "LUCIA FERNANDA COJTI BARRIOS"}]})
    inv.herramienta("movimientos", {"cuenta_id": "AT-00042-01"}, {"movimientos": []})
    assert _cantidades(inv) == {"clientes": 1, "nombres": 1, "cuentas": 2, "montos": 2, "casos": 1}


def test_anomalias_movimientos_reglas_y_politicas():
    inv = Inventario()
    inv.herramienta("detectar_anomalias", {"cliente_id": "CLI-0042"}, {
        "cliente_id": "CLI-0042", "total_hallazgos": 2,
        "hallazgos": [{"transaccion_id": 10, "cuenta_id": "AT-00042-01", "fecha_hora": "x", "monto": 900.0},
                      {"transaccion_id": 11, "cuenta_id": "AT-00042-01", "fecha_hora": "y", "monto": 50.0}],
        "rafagas": [{"transacciones": [11, 12, 13, 14], "total_gtq": 4000.0}],
        "reglas": {"umbral_z": 3.0, "horario_madrugada": "00:00-05:59", "rafaga": ">= 4"}})
    inv.herramienta("buscar_politicas", {"consulta": "bloqueo"}, {"fragmentos": [
        {"documento": "politica_prevencion_fraude_bloqueo_tarjetas.md", "seccion": "3. Bloqueo preventivo"},
        {"documento": "politica_prevencion_fraude_bloqueo_tarjetas.md", "seccion": "3. Bloqueo preventivo"},
        {"documento": "politica_kyc_debida_diligencia.md", "seccion": "2. Alcance"}]})
    c = _cantidades(inv)
    assert c["movimientos"] == 5 and c["casos"] == 1 and c["reglas"] == 3 and c["politicas"] == 2
    assert c["montos"] == 3   # dos montos de transacción y el total de la ráfaga


def test_documento_y_voz_y_etiquetas_en_singular():
    inv = Inventario()
    inv.documento("demo-boleta.png", {"cuenta": "AT-00107-01", "titular": "Pedro Pérez",
                                       "depositante": "María Gómez", "monto_total": 3500.0})
    inv.voz("t-0001")
    inv.voz("t-0002")
    r = inv.resumen()
    restringido = r["niveles"][0]
    etiquetas = {c["id"]: c["etiqueta"] for c in restringido["categorias"]}
    assert etiquetas["documentos"] == "documento escaneado" and etiquetas["voz"] == "grabaciones de voz"
    assert _cantidades(inv) == {"nombres": 2, "cuentas": 1, "montos": 1, "documentos": 1, "voz": 2}
    assert r["total"] == restringido["total"] == 7
