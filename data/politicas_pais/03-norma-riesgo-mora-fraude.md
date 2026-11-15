# Banco Demo GQ: Norma de Riesgo de Mora, Fraude y Uso de Tarjetas (NR-005)

> DEMO / DATOS FICTICIOS. Convertido de pais-3.0/rag-banco/ (la misma base de conocimientos politicas-banco de PAIS).

> Código: NR-005, versión 1.8, vigente desde el 1 de julio de 2026.
Aprobada por: Gerencia de Riesgos y Prevención de Fraude de Banco Demo GQ.
Nota: documento ficticio creado para el laboratorio GQ LAB. Banco Demo GQ no existe.
Esta norma aplica a todas las solicitudes de crédito (consumo y empresarial) y tiene prioridad sobre
las políticas de producto POL-CR-001 y POL-CR-002.

## 1. Alertas de fraude

Si el cliente tiene una o más alertas de fraude de nivel Alto en estado Abierta o En revisión, toda
solicitud de crédito queda SUSPENDIDA hasta que Prevención de Fraude cierre la alerta. No se
rechaza la solicitud. El analista no debe informar al cliente el motivo de la suspensión; solo indica
que la solicitud está en revisión. Esta regla aplica aunque el cliente tenga un score crediticio
excelente.

## 2. Mora (días de atraso en préstamos vigentes)

Se toma el máximo de días de mora entre todos los préstamos vigentes del cliente:

Días de mora             Resultado
máximos
0 días                   Sin restricción por mora
1 a 30 días              Pasa a COMITÉ DE CRÉDITO; si se aprueba, la tasa aumenta 1 punto
31 a 90 días             RECHAZADO; puede volver a aplicar después de 6 meses al día
Más de 90 días           RECHAZADO automáticamente; cartera vencida, no puede aplicar en 12 meses y se
reporta al buró

## 3. Uso de tarjetas de crédito

El uso de tarjetas es el saldo usado dividido entre el límite total de las tarjetas de crédito del cliente.

Uso de         Resultado
tarjetas
Menos de       Sin restricción
70 %
70 % a 90      Advertencia: el analista documenta el motivo, sin cambio en el monto
%
Más de 90      APROBADO CON CONDICIONES: el monto máximo se reduce al 50 % del monto máximo del
%              producto y el cliente debe consolidar el saldo de tarjetas en el nuevo préstamo

## 4. Tarjetas bloqueadas

Si el cliente tiene una o más tarjetas en estado Bloqueada, la solicitud pasa a COMITÉ DE CRÉDITO.

## 5. Comité de crédito

El comité de crédito está integrado por el gerente regional y un analista de riesgos. Resuelve en un
máximo de 5 días hábiles.
