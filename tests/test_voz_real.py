"""Pruebas con modelos reales. Se saltan si los modelos no están descargados (make voz-modelos)."""

import pytest

from comun import config
from orchestrator import voz
from orchestrator.voz_local.opciones import desde_config

op = desde_config(config.cargar())
hay_kokoro = op.ruta_kokoro.exists() and op.ruta_voces.exists()


@pytest.mark.skipif(not hay_kokoro, reason="Kokoro no descargado")
async def test_kokoro_real_es419(cfg):
    pytest.importorskip("kokoro_onnx")
    s = voz.crear_sintetizador(cfg)
    a = await s.sintetizar("Buenas tardes. La cliente tiene Q18,500.00 en movimientos inusuales.")
    assert a.datos[:4] == b"RIFF" and s.ultimo.motor == "kokoro" and s.ultimo.duracion_ms > 1500


@pytest.mark.skipif(not hay_kokoro, reason="Kokoro no descargado")
async def test_ida_y_vuelta_kokoro_whisper(cfg):
    try:
        t = voz.crear_transcriptor(cfg)
    except voz.VozNoDisponible as e:
        pytest.skip(str(e))
    s = voz.crear_sintetizador(cfg)
    a = await s.sintetizar("Centinela, abra un ticket para el equipo de fraude.")
    r = await t.transcribir(a.datos, "wav")
    assert "ticket" in r.texto.lower() and "fraude" in r.texto.lower()
