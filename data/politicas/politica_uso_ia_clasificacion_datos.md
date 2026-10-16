# Política de uso de inteligencia artificial y clasificación de datos

> DEMO / DATOS FICTICIOS — Documento ficticio del Banco Atitlán (banco inexistente) para una demostración. No es una política real ni de ninguna institución real.
> Código: POL-IAD-006 · Versión 1.2 · Vigente desde 2026-03-01 · Dueño: Gerencia de Seguridad de la Información

## 1. Objetivo y alcance

Esta política establece cómo se clasifica la información del Banco Atitlán y bajo qué reglas el personal puede usar herramientas de inteligencia artificial (IA), incluidos asistentes conversacionales, modelos de lenguaje, lectura automática de documentos y sistemas de recomendación.

Aplica a todos los colaboradores, contratistas y proveedores con acceso a información del banco, y a todo sistema de IA que procese información del banco, sin importar si es de desarrollo propio o de un proveedor.

El principio rector es que **los datos de los clientes no salen de la infraestructura del banco** y que **la IA asiste, pero las decisiones las toma una persona**. La Gerencia de Seguridad de la Información es responsable de esta política, en coordinación con Cumplimiento, Riesgo Operativo y Tecnología, y la revisa al menos una vez al año.

## 2. Niveles de clasificación de datos

Toda información del banco se clasifica en uno de cuatro niveles:

### 2.1 Público
Información aprobada para divulgación externa: tasas publicadas, ubicación de agencias, material publicitario. Puede compartirse sin restricción.

### 2.2 Interno
Información de uso general del personal que no contiene datos de clientes: manuales operativos, políticas, organigramas. No debe compartirse fuera del banco sin autorización.

### 2.3 Confidencial
Información sensible del negocio: estrategias, resultados financieros no publicados, configuraciones de sistemas, informes de auditoría. Acceso solo por necesidad de conocer.

### 2.4 Restringido
El nivel más alto. Incluye **todos los datos de clientes** (nombres, documentos de identificación, números de cuenta y tarjeta, saldos, movimientos, expedientes KYC), credenciales, llaves criptográficas y casos de fraude. Su acceso requiere autorización explícita, queda registrado y está sujeto a las restricciones de la sección 3.

Ante duda sobre el nivel de un dato, se aplica el nivel más alto que corresponda.

## 3. Prohibición de envío a servicios de IA externos

Los datos clasificados como **restringidos**, incluidos todos los datos de clientes, **no pueden enviarse a servicios de IA de terceros ni a servicios en la nube pública**. Esto incluye, sin limitarse a:

- Asistentes conversacionales de uso general en internet.
- Servicios de traducción, transcripción o resumen en línea.
- Extensiones de navegador o complementos de ofimática que envíen texto a servidores externos.
- Interfaces de programación de modelos alojadas fuera de la infraestructura del banco.

Tampoco se permite enviar datos **confidenciales** a estos servicios. La información **interna** solo puede usarse en servicios externos aprobados por Seguridad de la Información y nunca junto con datos de clientes.

Copiar, pegar, fotografiar o transcribir datos restringidos a cualquier herramienta no aprobada se considera un incidente de seguridad y debe reportarse en un máximo de 2 horas. El incumplimiento puede dar lugar a medidas disciplinarias.

## 4. Modelos aprobados e infraestructura propia

Solo pueden usarse modelos de IA **aprobados por el Comité de Tecnología y Riesgo** que **se ejecuten en infraestructura propia del banco** (centro de datos del banco o nube privada administrada por el banco), sin conexión de salida hacia internet para el procesamiento de datos.

Para aprobar un modelo se requiere:

1. Evaluación de seguridad: el modelo y sus componentes se ejecutan localmente y no envían datos ni telemetría al exterior.
2. Evaluación de calidad con casos de prueba del área usuaria, con al menos 50 casos documentados.
3. Definición del dueño funcional y del dueño técnico.
4. Inventario: el modelo se registra con su versión, fecha de aprobación y usos permitidos.

Los modelos aprobados se reevalúan cada 12 meses o ante cualquier cambio de versión. El asistente de consulta de políticas y el asistente de monitoreo de fraude son ejemplos de usos aprobados en infraestructura propia.

## 5. Humano en el circuito

Los sistemas de IA del banco pueden consultar información, resumir, clasificar y proponer acciones, pero **toda acción de escritura requiere la confirmación de una persona autorizada** antes de ejecutarse. Se consideran acciones de escritura, entre otras:

- Bloquear o desbloquear tarjetas, retener transferencias o suspender canales.
- Abrir, modificar o cerrar tickets.
- Modificar datos de clientes, límites o productos.
- Enviar comunicaciones a clientes.
- Ejecutar cambios en sistemas o infraestructura.

La propuesta del sistema queda en estado "pendiente de aprobación" y muestra a la persona la acción, los datos que la sustentan y la política aplicable (documento y sección). La persona puede aprobarla, modificarla o rechazarla; en todos los casos su decisión queda registrada. Ningún sistema de IA puede tener credenciales que le permitan ejecutar acciones de escritura sin esta confirmación.

## 6. Auditoría de consultas

**Cada consulta** realizada a un sistema de IA del banco queda registrada con:

- Usuario que consulta, fecha y hora.
- Texto de la consulta y respuesta generada (con datos sensibles enmascarados según la sección 7).
- Documentos o fuentes consultados por el sistema.
- Herramientas invocadas y acciones propuestas.
- Decisión humana tomada (aprobada, modificada o rechazada), cuando aplique.

Los registros se conservan durante un mínimo de 5 años, en almacenamiento interno, y no pueden modificarse ni eliminarse por los usuarios. Seguridad de la Información revisa mensualmente una muestra de al menos 100 consultas para detectar usos no autorizados, y Auditoría Interna los revisa al menos una vez al año. Los registros solo son accesibles para Seguridad de la Información, Auditoría Interna y Cumplimiento.

## 7. Enmascaramiento de datos

Los sistemas de IA y sus registros deben **enmascarar** los datos restringidos que no sean necesarios para la tarea:

- **Números de cuenta y tarjeta:** se muestran solo los últimos 4 dígitos (por ejemplo, `****-****-****-4821` o cuenta `******3390`).
- **Documento de identificación:** se muestran solo los últimos 3 caracteres.
- **Teléfono y correo:** se muestran parcialmente (por ejemplo, `****-**12` y `e*****@dominio`).
- Los saldos y montos pueden mostrarse completos solo al personal autorizado para la tarea en curso.

El enmascaramiento se aplica tanto en las respuestas mostradas en pantalla como en las respuestas leídas en voz alta por asistentes de voz. Ningún colaborador puede solicitar a la IA que muestre datos sin enmascarar si su rol no lo autoriza.

## 8. Decisiones de crédito y usos prohibidos

**Está prohibido usar IA para tomar decisiones automáticas de crédito sin revisión humana.** La IA puede apoyar el análisis (extraer datos de documentos, calcular indicadores, señalar inconsistencias), pero la aprobación, el rechazo o la modificación de condiciones de un crédito la decide un analista de crédito o el comité correspondiente, que debe dejar constancia de su revisión.

También están prohibidos:

- Usar IA para perfilar clientes por características no relacionadas con el riesgo financiero (origen étnico, religión, afiliación política).
- Generar documentos, firmas o comprobantes que puedan presentarse como auténticos.
- Usar IA para evadir controles internos o límites de aprobación.
- Entrenar o ajustar modelos con datos de clientes sin aprobación de Seguridad de la Información y Cumplimiento.

Todo cliente al que se le niegue un producto tiene derecho a solicitar que su caso sea revisado por una persona.
