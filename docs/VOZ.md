# Voz local (Fase 4)

> DEMO / DATOS FICTICIOS. Todo corre en la Mac: ni el audio ni el texto salen del equipo.

## Qué hay

```
Navegador (webm/opus o mp4)
   │  POST /api/sesiones/{id}/audio            (endpoint de la Fase 2)
   ▼
orchestrator/voz.py ── crear_transcriptor(cfg) ── await transcribir(audio, formato)
   │   ffmpeg → PCM 16 kHz → filtro de silencio → Whisper (language="es") → filtro de alucinaciones
   │
   │   respuesta del agente (llm.delta en streaming, o resumen_hablado)
   ▼
orchestrator/voz.py ── crear_sintetizador(cfg) ── await sintetizar(texto) → WAV
       └─ .locutor(): oración por oración mientras el LLM escribe
          Kokoro (es-419) ──falla o tarda──► say -v Paulina
```

| Pieza | Archivo | Notas |
|---|---|---|
| Ganchos del orquestador | `orchestrator/voz.py` | Las dos fábricas del contrato (§5 de `CONTRATO_EVENTOS.md`), async, el modelo corre en un hilo. |
| STT | `orchestrator/voz_local/stt.py` | `motor: auto` = mlx-whisper en Apple Silicon, faster-whisper en lo demás. |
| TTS | `orchestrator/voz_local/tts.py` | Kokoro (`kokoro-onnx`) con respaldo automático a `say`. |
| Oraciones y normalización | `orchestrator/voz_local/texto.py` | Corta en oraciones mientras llega el texto y lo deja «leíble». |
| Locutor | `orchestrator/voz_local/locutor.py` | Sintetiza cada oración en cuanto termina; `cancelar()` para el botón de silenciar. |
| Resumen hablado | `orchestrator/voz_local/resumen.py` | Campo del agente → modelo rápido → recorte. |
| Scripts | `scripts/descargar_modelos_voz.py`, `comparar_voces.py`, `probar_voz.py` | `make voz-modelos`, `make voces`, `make voz-prueba`. |

Nada de `mlx`, `faster_whisper` ni `kokoro_onnx` se importa al cargar los módulos: las pruebas corren en
cualquier máquina con motores simulados.

## Cómo lo engancha el orquestador (Fase 2)

```python
from orchestrator import voz

stt = voz.crear_transcriptor(cfg)       # lanza voz.VozNoDisponible si no hay motor → «no disponible»
tts = voz.crear_sintetizador(cfg)

t = await stt.transcribir(audio_bytes, "audio/webm")   # Transcripcion(texto, modelo, idioma)
# t.texto == "" si fue silencio: no hace falta mandar nada al agente.

# Opción A (recomendada para la meta de ≤ 5 s): hablar mientras el agente escribe.
locutor = tts.locutor()                 # tope: voz.max_oraciones_habladas
enviar = asyncio.create_task(emitir_voz(locutor.fragmentos()))   # cada FragmentoVoz → voz.audio
for delta in llm:                       # el mismo texto que va en llm.delta
    locutor.alimentar(delta)
locutor.terminar(); await enviar        # locutor.cancelar() si la persona silencia o pregunta otra cosa

# Opción B: hablar el resumen_hablado al final.
r = await voz.resumen_hablado(respuesta, cfg, generar=lambda p: ollama_rapido(p))
for oracion in tts.oraciones(r.texto):
    audio = await tts.sintetizar(oracion)     # Audio(datos=WAV, formato="wav", motor)
```

`FragmentoVoz` trae `indice`, `texto`, `wav`, `motor`, `duracion_ms`, `latencia_ms` (síntesis de esa oración)
y `desde_inicio_ms`. Si una oración no se pudo sintetizar, `wav` es `None` y `error` dice por qué: el texto
igual está en el chat y la voz sigue con la siguiente.

**Por qué recomiendo la opción A.** El contrato v1 dice que la voz habla `resumen_hablado`. Eso obliga a
esperar la respuesta completa del agente más una llamada al modelo rápido antes de la primera palabra, y con
herramientas de por medio difícilmente entra en 5 s. Con el locutor la primera oración suena en cuanto el
agente la escribe y el tope de oraciones mantiene la respuesta hablada más corta que la escrita. Las dos
están implementadas; se elige con `voz.modo_habla` (`primeras_oraciones` | `resumen`).

`voz.precalentar_voz(cfg)` carga Whisper y Kokoro y hace una inferencia corta con cada uno (para `make warmup`).

## Decisiones

1. **Kokoro con fonemizador `es-419`.** Las tres voces en español de Kokoro (`ef_dora`, `em_alex`,
   `em_santa`) tienen timbre de España, pero el fonemizador (espeak-ng) acepta español latinoamericano:
   «Lucía» y «Zacapa» se pronuncian con s y no con z. El timbre no cambia; el seseo sí. Verificado
   fonemizando el guion con `es` y `es-419`.
2. **Normalización antes de hablar.** El fonemizador leía «Q12,345.67» como «doce coma trescientos…». Ahora:
   montos con moneda («12345 quetzales con 67 centavos»), horas («las 2 y 14 de la madrugada»), fechas ISO,
   porcentajes, abreviaturas (Sr., Licda.) y Markdown. Tablas, bloques de código y encabezados no se leen.
3. **Las cuentas nunca se dicen completas.** `AT-00042-01` se lee «cuenta terminada en 42 01», aunque el
   texto escrito la traiga completa. Lo que suena en un salón lo oye todo el salón.
4. **Whisper con español forzado y vocabulario.** `language="es"` y un `prompt_inicial` con los nombres del
   guion (Cojtí, VKS, NSX, vCenter…) en `config.yaml`.
5. **Filtro de silencio y de alucinaciones.** Con silencio o ruido Whisper inventa «Subtítulos realizados por
   la comunidad de Amara.org» o «Gracias por ver el video». El audio por debajo de `umbral_silencio_rms` no
   se transcribe y esas frases se descartan: un clic accidental del botón no manda basura al agente.
6. **Respaldo automático.** Si Kokoro falla, esa oración sale con `say`; tras 2 fallos seguidos, o si una
   oración tarda más de `timeout_tts_s`, todo el turno sigue con `say`. El evento `voz.audio` trae `motor`,
   así el panel puede mostrar que cambió.
7. **Nada se descarga en ejecución.** Kokoro y Whisper se cargan desde `./modelos`; faster-whisper con
   `local_files_only=True`. Si falta un modelo, el error dice qué comando correr.

## Qué probar en la Mac (no se pudo en el contenedor Linux)

```bash
brew install ffmpeg
make setup && make voz-modelos     # con internet, una vez (~2 GB: Whisper turbo MLX + Kokoro)
# apagar el Wi-Fi a partir de aquí
make voz-prueba                    # TTS → STT y tiempo al primer audio
make voces                         # abrir salida/voces/index.html y elegir voz
```

- [ ] `make voz-prueba` dice `STT mlx-whisper` y la transcripción coincide con la frase.
- [ ] Latencia STT de una pregunta de ~4 s (medir con un audio propio: `python -m scripts.probar_voz --audio pregunta.m4a`).
- [ ] «Primer audio listo a los … ms» de `--streaming`: objetivo < 800 ms en la Mac.
- [ ] Elegir voz: Kokoro `em_alex`/`ef_dora` con `es-419` contra `say` Paulina. Si ninguna de Kokoro
      convence, `modelos.tts.motor: say` deja a Paulina como principal (y Kokoro como respaldo si se quiere).
- [ ] Voces mejoradas de macOS: Ajustes del Sistema → Accesibilidad → Contenido leído → Voces → Español
      (México) → «Paulina (mejorada)». Se descarga una vez y suena bastante mejor que la normal; aparece en
      `make voces` y se fija con su nombre en `voz_say` (confirmar el nombre exacto con `say -v "?"`; suele ser «Paulina (Enhanced)»).
- [ ] Memoria con Whisper y Kokoro cargados (estimado 2.4 GB en DECISIONES.md).

## Medido en el contenedor (Linux x86, solo CPU)

Kokoro fp32 con `es-419`: «Buenas tardes.» listo en ~0.5 s; RTF ≈ 0.35–0.7 (sintetiza más rápido que el
tiempo real). En Apple Silicon debería ser mejor, pero hay que medirlo. Whisper no se pudo probar aquí: la red
del contenedor no deja descargar modelos de Hugging Face, así que la prueba de ida y vuelta (`tests/test_voz_real.py`)
se salta sola si falta el modelo.

## Riesgos para el escenario

- **Acento de Kokoro.** El timbre de España puede sonar ajeno en Guatemala. Elegir en `make voces` antes de
  ensayar; Paulina mejorada es un plan B digno.
- **Oraciones muy largas** tardan más en sintetizarse: se cortan en una coma pasados `max_caracteres_oracion`.
  Un prompt de sistema que pida oraciones cortas ayuda más que cualquier ajuste aquí.
- **Safari** graba en mp4/aac: se decodifica desde archivo temporal (desde stdin ffmpeg no lo lee). Probar el
  navegador que se usará en escena.
