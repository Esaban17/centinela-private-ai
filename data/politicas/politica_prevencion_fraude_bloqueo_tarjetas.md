# Política de prevención de fraude y bloqueo preventivo de tarjetas

> DEMO / DATOS FICTICIOS — Documento ficticio del Banco Atitlán (banco inexistente) para una demostración. No es una política real ni de ninguna institución real.
> Código: POL-FRA-001 · Versión 1.2 · Vigente desde 2026-03-01 · Dueño: Gerencia de Prevención de Fraude

## 1. Objetivo y alcance

Esta política establece cómo el Banco Atitlán detecta, evalúa y contiene operaciones potencialmente fraudulentas con tarjetas de débito y crédito, así como con transferencias electrónicas (ACH) iniciadas desde canales digitales. Su propósito es proteger el patrimonio de los clientes y del banco, reducir pérdidas por fraude y asegurar un trato justo y oportuno al cliente afectado.

Aplica a todas las tarjetas emitidas por el banco (segmentos personal, preferente, pyme y corporativo), a las transferencias ACH salientes y a todo el personal del Centro de Monitoreo de Fraude, la Mesa de Atención al Cliente y las agencias. También aplica a los sistemas automatizados de monitoreo, incluido el asistente de IA privado, que solo pueden sugerir acciones; la decisión final corresponde siempre a un analista humano, según la sección 8.

La Gerencia de Prevención de Fraude es responsable de mantener esta política, revisarla al menos una vez al año y comunicar los cambios al personal involucrado.

## 2. Señales de riesgo del motor de reglas

El motor de reglas del banco evalúa cada operación contra el historial del propio cliente y genera una señal cuando se cumple cualquiera de estos criterios:

### 2.1 Monto atípico
Se marca la operación cuyo monto tenga un z-score igual o mayor a 3 frente al promedio de los últimos 120 días del cliente. Es decir, el monto se aleja tres o más desviaciones estándar de lo que el cliente suele gastar.

### 2.2 Horario de madrugada
Se marca toda operación realizada entre las 00:00 y las 05:59 (hora de Guatemala) cuando el cliente no tiene la costumbre de operar en ese horario según su historial de 120 días.

### 2.3 Ubicación nueva
Se marca la operación realizada en una ciudad o comercio cuya ubicación nunca se ha visto en el historial del cliente. Si la ubicación corresponde a otro país, la señal se considera de mayor severidad.

### 2.4 Ráfaga de operaciones
Se marca la secuencia de 4 o más operaciones del mismo cliente dentro de una ventana de 30 minutos.

Cada señal se registra por separado; una misma operación puede activar varias señales distintas.

## 3. Criterios de bloqueo preventivo

El bloqueo preventivo de la tarjeta procede cuando se cumple al menos una de estas condiciones:

- La operación o el grupo de operaciones del cliente presenta **2 o más señales distintas** de las descritas en la sección 2 (por ejemplo, monto atípico más ubicación nueva).
- Se registra **una compra en el extranjero en horario de madrugada (00:00 a 05:59) sin aviso de viaje** registrado por el cliente.

Cuando procede el bloqueo, el analista debe:

1. Aplicar un **bloqueo preventivo temporal y reversible**, con una duración **máxima de 72 horas**. No es una cancelación de la tarjeta.
2. **Intentar contacto con el cliente dentro de los 30 minutos** siguientes, únicamente por un canal registrado en su expediente (teléfono, correo o notificación en la app).
3. **Abrir un ticket de fraude con prioridad alta**. La prioridad será **crítica si el monto acumulado de las operaciones sospechosas supera Q 25,000** (aproximadamente USD 3,250).

Si transcurren las 72 horas sin confirmación del cliente, el caso se escala al supervisor de turno, quien decide entre extender el bloqueo con justificación documentada o sustituir la tarjeta.

## 4. Transferencias ACH en ráfaga

Las transferencias ACH salientes se evalúan con las mismas señales de la sección 2. Se considera ráfaga ACH el envío de 4 o más transferencias en 30 minutos desde la misma cuenta, especialmente hacia beneficiarios nuevos o hacia cuentas de otros bancos registradas en las últimas 24 horas.

Ante una ráfaga ACH, el analista debe:

1. **Retener** las transferencias que aún no se hayan liquidado, dejándolas en estado "pendiente de verificación".
2. **Suspender temporalmente el canal** digital desde el que se originaron (app o banca en línea) por un máximo de 24 horas, sin afectar los demás canales del cliente.
3. Intentar contacto con el cliente en 30 minutos por canal registrado.
4. Abrir ticket de fraude con prioridad alta, o crítica si el monto acumulado supera Q 25,000.

Si el cliente confirma las transferencias, se liberan en un plazo máximo de 2 horas. Si no las reconoce, se solicita la devolución al banco receptor y se activa el procedimiento de reclamos (manual POL-REC-004).

## 5. Comunicación con el cliente

El contacto con el cliente se realiza exclusivamente por canales registrados en su expediente. Nunca se usa un número telefónico o correo proporcionado por un tercero, ni el que aparezca en la propia operación sospechosa.

Durante el contacto el colaborador debe:

- Identificarse con nombre y área, e informar que se trata de una verificación preventiva.
- Confirmar la identidad del cliente con preguntas de seguridad del expediente.
- Describir la operación (fecha, comercio, monto) sin revelar el número completo de la tarjeta; solo los últimos 4 dígitos.
- Nunca solicitar PIN, código de seguridad (CVV), contraseñas ni códigos de un solo uso.

Si no se logra contacto en el primer intento, se realizan hasta 3 intentos en las siguientes 2 horas y se envía una notificación en la app con el aviso "Su tarjeta fue bloqueada preventivamente; comuníquese con el banco". Todo intento queda registrado en el ticket.

## 6. Desbloqueo de la tarjeta

El desbloqueo procede cuando el cliente, debidamente identificado por canal registrado, confirma que reconoce las operaciones marcadas. El analista debe:

1. Verificar que la identificación del cliente se completó y quedó registrada.
2. Levantar el bloqueo en un plazo máximo de 15 minutos después de la confirmación.
3. Ofrecer al cliente registrar un aviso de viaje si las operaciones fueron en el extranjero, para reducir futuras alertas.
4. Cerrar el ticket con la clasificación "falso positivo confirmado por el cliente".

Si el cliente no reconoce alguna operación, la tarjeta no se desbloquea: se cancela definitivamente, se emite una reposición sin costo en un máximo de 5 días hábiles y el ticket se reclasifica como "fraude confirmado", iniciando el proceso de reclamo y contracargo.

El bloqueo se levanta automáticamente al cumplirse las 72 horas solo si un supervisor lo autoriza por escrito en el ticket.

## 7. Registro y auditoría

Toda alerta, bloqueo, retención, contacto y desbloqueo debe quedar registrado en el sistema de tickets con, como mínimo:

- Número de ticket, fecha y hora de cada acción.
- Señales activadas y valores (por ejemplo, z-score calculado, hora de la operación, país).
- Monto individual y acumulado.
- Analista que tomó la decisión y, en su caso, supervisor que la autorizó.
- Resultado del contacto con el cliente.
- Recomendaciones generadas por el asistente de IA y si fueron aceptadas o rechazadas.

Los registros se conservan durante un mínimo de 5 años y no pueden modificarse; las correcciones se agregan como anotaciones nuevas. La Auditoría Interna revisa trimestralmente una muestra de al menos 50 casos para verificar el cumplimiento de plazos (contacto en 30 minutos, máximo de 72 horas de bloqueo) y la correcta asignación de prioridades.

## 8. Aprobación humana obligatoria

Ninguna acción automatizada sobre la tarjeta o la cuenta del cliente se ejecuta sin la confirmación explícita de un analista del Centro de Monitoreo de Fraude. Esto incluye el bloqueo preventivo, la retención de transferencias, la suspensión de canal, el desbloqueo y la apertura o cierre de tickets.

El motor de reglas y el asistente de IA pueden detectar señales, calcular métricas, redactar el resumen del caso y proponer la acción que corresponde según esta política, pero la propuesta queda en estado "pendiente de aprobación" hasta que el analista la confirme. El analista puede rechazar la recomendación, en cuyo caso debe anotar el motivo en el ticket.

Para casos con prioridad crítica (monto acumulado superior a Q 25,000), además de la confirmación del analista se notifica al supervisor de turno en un máximo de 15 minutos. Esta regla de aprobación humana no admite excepciones.
