"""Voz local de Centinela (Fase 4): STT con Whisper y TTS con Kokoro, sin red.

La interfaz que engancha el orquestador está en `orchestrator/voz.py`. Este paquete tiene las piezas:

- `opciones`: lee `modelos.stt`, `modelos.tts` y `voz` de config.yaml.
- `audio`: ffmpeg → PCM 16 kHz, WAV de salida.
- `stt`: mlx-whisper (Mac) / faster-whisper (Linux, pruebas).
- `tts`: Kokoro con respaldo automático a `say`.
- `texto`: oraciones en streaming y normalización para leer en voz alta.
- `resumen`: respuesta hablada corta.
- `locutor`: sintetiza oración por oración mientras el LLM escribe.

Nada aquí importa mlx, faster-whisper ni kokoro al cargar: se importan al crear el motor, así las pruebas
corren en cualquier máquina.
"""
