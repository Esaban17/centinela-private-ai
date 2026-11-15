# Banco Demo GQ: Guía de Evaluación de Solicitudes de Crédito (GE-010)

> DEMO / DATOS FICTICIOS. Convertido de pais-3.0/rag-banco/ (la misma base de conocimientos politicas-banco de PAIS).

> Código: GE-010, versión 1.1, vigente desde el 1 de julio de 2026.
Uso: analistas de crédito y asistente de IA de Banco Demo GQ.
Nota: documento ficticio creado para el laboratorio GQ LAB. Banco Demo GQ no existe.

## 1. Datos que se revisan del cliente

Para evaluar una solicitud se consulta el perfil crediticio del cliente: segmento, score crediticio,
antigüedad como cliente en meses, saldo en cuentas activas, préstamos vigentes, días de mora
máximos, tarjetas bloqueadas, porcentaje de uso de tarjetas de crédito y alertas de fraude de nivel
Alto abiertas o en revisión.

## 2. Orden de evaluación

Las reglas se aplican en este orden y la primera que bloquea determina el dictamen:
1. Alertas de fraude de nivel Alto abiertas o en revisión: SUSPENDIDO (NR-005 sección 1).
2. Días de mora máximos: más de 30 días es RECHAZADO; de 1 a 30 días pasa a COMITÉ (NR-
005 sección 2).
3. Segmento permitido para el producto (POL-CR-001 y POL-CR-002).
4. Score crediticio mínimo del producto: si no lo cumple, RECHAZADO.
5. Antigüedad mínima como cliente: si no la cumple, RECHAZADO.
6. Tarjetas bloqueadas: COMITÉ (NR-005 sección 4).
7. Uso de tarjetas mayor a 90 %: APROBADO CON CONDICIONES con monto al 50 % (NR-005
sección 3).
8. Monto solicitado contra el monto máximo según banda de score (POL-CR-001 sección 3). Si el
monto excede el máximo, se ofrece el monto máximo.

## 3. Dictámenes posibles

Dictamen                      Significado
APROBADO                      Cumple todas las reglas; se indica monto aprobado y tasa referencial
APROBADO CON                  Cumple, pero con monto reducido o requisito adicional
CONDICIONES
COMITÉ DE CRÉDITO             Requiere decisión del comité en 5 días hábiles
SUSPENDIDO                    Detenido por alerta de fraude; no se informa el motivo al cliente
RECHAZADO                     No cumple una regla obligatoria; se indica la regla y cuándo puede volver a
aplicar

## 4. Formato de respuesta del dictamen

El dictamen debe indicar: nombre y código del cliente, producto y monto solicitado, dictamen, los
datos del cliente usados (score, mora, antigüedad, uso de tarjetas, alertas) y el código de la política
o norma aplicada (por ejemplo POL-CR-001 o NR-005).
