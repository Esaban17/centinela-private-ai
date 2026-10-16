"""Visión documental: extracción de campos con el modelo de visión local y validación cruzada contra la base.

- `documentos.modelos`: esquemas Pydantic de cada tipo de documento (boleta, solicitud de crédito, factura).
- `documentos.extraccion`: imagen → modelo de visión (Ollama) → JSON validado.
- `documentos.validacion`: campos extraídos → verificaciones contra `banco.db`.

DEMO / DATOS FICTICIOS.
"""
