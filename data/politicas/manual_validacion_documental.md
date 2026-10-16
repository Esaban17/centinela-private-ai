# Manual de validación de documentos presentados

> DEMO / DATOS FICTICIOS — Documento ficticio del Banco Atitlán (banco inexistente) para una demostración. No es una política real ni de ninguna institución real.
> Código: POL-VAL-007 · Versión 1.2 · Vigente desde 2026-03-01 · Dueño: Gerencia de Operaciones y Crédito

## 1. Objetivo y alcance

Este manual define cómo el Banco Atitlán valida los documentos que presentan sus clientes o terceros como base para una operación: boletas de depósito, solicitudes de crédito, facturas, cartas de instrucción y comprobantes de ingresos. Su objetivo es evitar que se procesen operaciones con documentos alterados, falsos o presentados por una persona distinta del titular.

Aplica a todo el personal de agencias, Operaciones, Crédito y Banca Pyme, y a los sistemas de lectura automática de documentos, incluido el asistente de IA privado.

Ningún documento se considera válido por el solo hecho de haber sido presentado: siempre se contrasta con los registros del banco. Este manual complementa la política de conocimiento del cliente (POL-KYC-002) y la política de prevención de fraude (POL-FRA-001).

## 2. Verificaciones obligatorias

Para cualquier documento presentado se verifican, en este orden:

1. **La cuenta existe y está activa.** Una cuenta cerrada, bloqueada o inexistente invalida el documento.
2. **El titular coincide exactamente con el registro.** El nombre que aparece en el documento debe ser idéntico al nombre registrado de la cuenta en el expediente KYC. Diferencias de apellidos, nombres omitidos o nombres distintos se consideran discrepancia (sección 4).
3. **La moneda coincide.** Un documento en dólares no puede aplicarse a una cuenta en quetzales sin la conversión registrada, y viceversa.
4. **El monto coincide con el movimiento registrado.** Para boletas de depósito, el monto de la boleta debe coincidir exactamente con el movimiento registrado en el sistema en la fecha indicada.
5. **Fecha y referencia** del documento corresponden a un movimiento real.

Si las cinco verificaciones son correctas, el documento se marca como "validado" con el nombre del colaborador que lo revisó.

## 3. Saldo declarado frente a saldo real

Cuando un documento declara un saldo (por ejemplo, una solicitud de crédito en la que el solicitante indica el saldo promedio de su cuenta o un estado de cuenta presentado como soporte), se compara con el saldo real registrado en el sistema:

- Se acepta una **tolerancia de hasta 10 %** entre el saldo declarado y el saldo real, para cubrir movimientos ocurridos entre la fecha del documento y la fecha de revisión.
- Si la diferencia es **mayor al 10 %**, el documento se marca como "inconsistente" y el analista debe solicitar al cliente una aclaración por escrito y un estado de cuenta actualizado.
- Si el saldo declarado supera al real en más del 50 %, o el estado de cuenta presentado no corresponde con los movimientos del sistema, se considera indicio de alteración y se reporta a Prevención de Fraude.

Ejemplo: un saldo declarado de Q 100,000 frente a un saldo real de Q 93,000 (7 % de diferencia) está dentro de la tolerancia; frente a un saldo real de Q 80,000 (20 %) no lo está.

## 4. Discrepancia de titular

Cuando el nombre del titular en el documento no coincide exactamente con el titular registrado de la cuenta, el colaborador debe:

1. **No procesar** la operación, aunque el resto de los datos sea correcto.
2. **Retener el documento** original (o la imagen, si se presentó en forma digital) y adjuntarlo al caso. No devolverlo a quien lo presentó.
3. **Contactar al titular registrado** únicamente por el **canal registrado** en su expediente, nunca por los datos de contacto que aparezcan en el documento o que proporcione quien lo presenta.
4. Si existe **indicio de suplantación** de identidad (el documento intenta acreditar fondos o desembolsos a nombre de otra persona, o el presentador no puede explicar la diferencia), **abrir ticket de fraude con prioridad alta**, o crítica si el monto supera Q 25,000 (aprox. USD 3,250), según POL-FRA-001.
5. Registrar la discrepancia como señal de alerta para Cumplimiento (POL-KYC-002).

Solo se procesa la operación si el titular registrado confirma por canal registrado y la diferencia se explica con documentación (por ejemplo, un nombre ya registrado como variante en el expediente).

## 5. Lectura automática con IA

El banco utiliza lectura automática de documentos con IA, ejecutada en infraestructura propia (POL-IAD-006), para extraer datos como nombre del titular, número de cuenta, monto, moneda y fecha, y para compararlos con los registros.

Reglas de uso:

- **Todo resultado de la lectura automática es revisado por una persona** antes de procesar la operación. La IA no valida ni rechaza documentos por sí sola.
- El sistema muestra al revisor los datos extraídos, el nivel de confianza de cada campo y las discrepancias detectadas, junto con la imagen original.
- Los campos con confianza menor al 90 % se marcan para revisión manual obligatoria campo por campo.
- Si la IA detecta discrepancia de titular, moneda o monto, la propuesta de acción (retener, contactar, abrir ticket) queda pendiente de aprobación del revisor.
- Los números de cuenta se muestran enmascarados (últimos 4 dígitos) salvo que el revisor necesite el número completo para la verificación.

La decisión final y su justificación quedan registradas a nombre del revisor.

## 6. Crédito pyme: requisitos

Para una solicitud de crédito pyme se requiere:

1. Expediente KYC de la empresa vigente, con escritura constitutiva y nombramiento vigente del representante legal (POL-KYC-002).
2. Solicitud de crédito firmada por el representante legal o un apoderado registrado.
3. Estados financieros de los últimos 2 ejercicios y estados de cuenta de los últimos 6 meses.
4. Facturas o contratos que respalden el destino del crédito cuando sea para capital de trabajo o compra de activos.
5. Antigüedad mínima de la empresa de 12 meses de operación.
6. Garantía según el monto: créditos mayores a Q 500,000 (aprox. USD 65,000) requieren garantía real o fiduciaria evaluada por Crédito.

Las facturas presentadas se validan con las verificaciones de la sección 2 aplicables: emisor, receptor (debe ser la empresa solicitante), monto y moneda. La aprobación del crédito siempre la decide un analista o comité de crédito, nunca un sistema automatizado (POL-IAD-006).

## 7. Cuenta de desembolso

**La cuenta de desembolso de cualquier crédito debe pertenecer al solicitante.** Para créditos pyme, la cuenta debe estar a nombre de la razón social exacta de la empresa solicitante, no del representante legal, de un socio ni de un proveedor.

Antes de desembolsar, el analista verifica:

- Que la cuenta existe, está activa y está en la moneda del crédito.
- Que el titular de la cuenta coincide exactamente con el solicitante del crédito.
- Que la cuenta no tiene bloqueos ni alertas de fraude abiertas.

Si la solicitud indica una cuenta de desembolso a nombre de un tercero, **no se desembolsa**, se trata como discrepancia de titular (sección 4) y se contacta al representante legal registrado por canal registrado. Los pagos a proveedores con fondos del crédito se realizan desde la cuenta de la empresa, después del desembolso, sujetos a los límites de POL-LIM-003.

## 8. Registro y conservación

Por cada documento validado, rechazado o retenido se registra:

- Tipo de documento, fecha de recepción y canal (agencia, app o correo registrado).
- Resultado de cada verificación de la sección 2 y, si aplica, de la comparación de saldo de la sección 3.
- Resultado de la lectura automática y correcciones hechas por el revisor.
- Decisión final, nombre del revisor y, si aplica, número de ticket de fraude.

Los documentos retenidos por discrepancia se conservan en custodia de Operaciones durante un mínimo de 5 años y solo se entregan a Prevención de Fraude, Cumplimiento, Auditoría Interna o a requerimiento de autoridad competente. Auditoría Interna revisa semestralmente una muestra de al menos 30 documentos validados.
