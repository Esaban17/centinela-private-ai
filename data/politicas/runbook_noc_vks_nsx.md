# Runbook del NOC: alertas de VKS, NSX y plataforma VCF

> DEMO / DATOS FICTICIOS — Documento ficticio del Banco Atitlán (banco inexistente) para una demostración. No es una política real ni de ninguna institución real.
> Código: POL-NOC-005 · Versión 1.2 · Vigente desde 2026-03-01 · Dueño: Gerencia de Infraestructura y Operaciones TI (NOC)

## 1. Objetivo y alcance

Este runbook guía al personal del Centro de Operaciones de Red (NOC) del Banco Atitlán en la atención de alertas de la plataforma de nube privada del banco. Aplica a los clústeres de Kubernetes administrados (VKS), la red virtual (NSX), el almacenamiento vSAN, vCenter y los hosts ESXi del dominio de carga productivo.

Los nombres usados son de ejemplo: clúster `vks-prod-01` (aloja la banca en línea y la app móvil), clúster de edges `edge-cl-01` con los nodos `edge-01` y `edge-02`, datastore `vsan-prod` y host `esx-07`.

Reglas generales para todo el runbook:

- Antes de cualquier acción correctiva, abrir ticket con la prioridad indicada (sección 7).
- No ejecutar cambios que no estén descritos aquí sin autorización del nivel 2.
- Registrar en el ticket cada comando ejecutado y su resultado.
- El asistente de IA puede sugerir diagnósticos, pero toda acción de escritura la confirma el operador.

## 2. Clúster VKS: plano de control NotReady o etcd sin quórum

**Prioridad: CRÍTICA.** Impacto directo en banca en línea y app móvil. Escalar a nivel 2 en un máximo de **15 minutos** desde la alerta.

### 2.1 Diagnóstico
1. Confirmar el estado de los nodos: `kubectl get nodes -o wide` y ubicar los nodos del plano de control en estado `NotReady`.
2. Revisar los pods del sistema: `kubectl get pods -n kube-system` y los eventos recientes: `kubectl get events -A --sort-by=.lastTimestamp`.
3. Verificar la salud de etcd con `kubectl describe` del pod de etcd de cada nodo de control y revisar si hay quórum (se requieren al menos 2 de 3 miembros sanos).
4. En vCenter, revisar las VMs del plano de control de `vks-prod-01`: encendidas, sin alarmas de CPU, memoria o almacenamiento, y con conectividad de red.

### 2.2 Recuperación
1. Recuperar **un nodo a la vez**. **Nunca reiniciar los 3 nodos del plano de control al mismo tiempo**, porque se pierde el quórum de etcd por completo.
2. Si un solo nodo está afectado, reiniciar solo su VM y esperar a que vuelva a `Ready` antes de tocar otro.
3. Si dos nodos están afectados (sin quórum), recuperar primero el nodo con datos de etcd más recientes, con acompañamiento del nivel 2, hasta restaurar el quórum de 2 de 3.
4. Confirmar con `kubectl get nodes` y validar que la banca en línea responde.

## 3. NSX Edge: failover de HA y sesiones BGP caídas

**Prioridad: ALTA** si el servicio continúa por el edge restante; **CRÍTICA** si ambos uplinks o ambos edges están caídos.

### 3.1 Diagnóstico
1. En NSX Manager, verificar el estado de HA del clúster `edge-cl-01`: identificar cuál nodo está **activo** y cuál **standby**. Una alerta típica es que `edge-02` pasó a activo tras un failover.
2. Revisar los vecinos BGP del router de nivel 0: estado `Established` en cada sesión con los switches ToR. Identificar qué sesión está caída y en qué uplink.
3. Verificar el **uplink físico**: estado del puerto en el host y en el ToR, errores de interfaz, y VLAN del uplink.
4. Confirmar que las rutas por defecto y las rutas hacia la red de la banca en línea siguen anunciadas por la sesión BGP restante.

### 3.2 Acciones
1. Si el tráfico fluye por el edge activo y la otra sesión BGP sigue `Established`, mantener la operación y abrir ticket con el equipo de red física para el uplink afectado.
2. **No forzar el failback** al edge original **en horario hábil** (lunes a sábado de 07:00 a 20:00). El failback se programa en ventana de mantenimiento aprobada.
3. Si ambas sesiones BGP están caídas, escalar de inmediato a nivel 2 y al equipo de red física; prioridad crítica.

## 4. Almacenamiento vSAN: capacidad y latencia

### 4.1 Capacidad
- **Más de 80 % de uso** en `vsan-prod`: **prioridad media**. Notificar a Capacidad y Almacenamiento, identificar instantáneas (snapshots) antiguas de más de 7 días y VMs apagadas candidatas a depuración. No borrar nada sin aprobación del dueño de la VM.
- **Más de 85 % de uso**: **prioridad alta**. Por encima de este nivel vSAN puede no tener espacio para reconstruir datos si falla un host. Congelar el aprovisionamiento de nuevas VMs en el clúster y escalar a nivel 2 en 1 hora.

### 4.2 Latencia
- **Latencia de escritura mayor a 20 ms** sostenida por más de 10 minutos: **prioridad alta**.
1. En vCenter, revisar el rendimiento de vSAN por host y por disco para identificar si la latencia está concentrada en un host (por ejemplo, `esx-07`) o en un grupo de discos.
2. Verificar si hay tareas de resincronización o reconstrucción en curso.
3. Revisar la red de vSAN (errores, pérdida de paquetes, MTU).
4. Si la latencia afecta a las VMs de `vks-prod-01` o bases de datos de banca en línea, elevar a **crítica**.

## 5. vCenter: servicio vpxd reiniciado y certificados

### 5.1 Servicio vpxd reiniciado
**Prioridad: alta** si el servicio no vuelve en 10 minutos; **media** si se recuperó solo.
1. Confirmar el estado de los servicios en la interfaz de administración de vCenter y verificar que vpxd esté en ejecución.
2. Revisar el uso de disco de las particiones de vCenter: una partición de registros llena (más de 90 %) es causa frecuente de reinicios.
3. Revisar los registros del servicio para identificar el error previo al reinicio.
4. Recordar que la caída de vCenter no detiene las VMs en ejecución, pero impide operaciones de administración y de vSphere HA avanzadas.
5. Si se reinició más de 2 veces en 24 horas, escalar a nivel 2.

### 5.2 Certificados por vencer
- **Vencen en 30 días o menos:** prioridad media; programar la renovación en ventana de mantenimiento.
- **Vencen en 7 días o menos:** prioridad alta; escalar a nivel 2 para renovación urgente.
- Nunca renovar certificados de vCenter en horario hábil sin ventana aprobada.

## 6. Host ESXi: CPU, memoria y modo mantenimiento

### 6.1 CPU mayor a 85 %
**Prioridad: media**; **alta** si persiste más de 30 minutos.
1. Identificar en vCenter las VMs con mayor consumo en el host (por ejemplo, `esx-07`).
2. Verificar que DRS esté activo y si ha propuesto migraciones.
3. Con aprobación del nivel 2, migrar VMs no críticas a otros hosts.

### 6.2 Memoria degradada
**Prioridad: alta.** Alertas de módulos de memoria con errores corregibles repetidos o no corregibles.
1. Revisar el estado de hardware del host en vCenter.
2. Migrar las VMs del host afectado y colocarlo en modo mantenimiento.
3. Abrir caso con el proveedor de hardware.

### 6.3 Modo mantenimiento
1. Antes de entrar en modo mantenimiento, confirmar que vsan-prod tenga capacidad suficiente (uso menor a 80 %) para evacuar datos.
2. No colocar más de **un host a la vez** en mantenimiento en el clúster productivo.
3. No colocar en mantenimiento hosts que alojen nodos del plano de control de `vks-prod-01` sin verificar que los otros 2 nodos estén `Ready`.

## 7. Matriz de prioridades

Toda alerta se clasifica en una de cuatro prioridades, que determinan el tiempo máximo de atención (inicio de trabajo sobre el ticket):

- **Crítica — atención en 15 minutos.** Servicio de cara al cliente caído o en riesgo inmediato: plano de control de `vks-prod-01` sin quórum, ambas sesiones BGP caídas, latencia de vSAN que afecta la banca en línea. Escalamiento a nivel 2 en 15 minutos y aviso a la Gerencia de TI.
- **Alta — atención en 1 hora.** Pérdida de redundancia sin impacto al cliente: failover de NSX Edge con un uplink caído, vSAN mayor a 85 %, latencia de escritura mayor a 20 ms, memoria degradada, certificados que vencen en 7 días o menos.
- **Media — atención en 4 horas.** Degradación sin pérdida de redundancia: vSAN mayor a 80 %, CPU de host mayor a 85 %, vpxd recuperado solo, certificados que vencen en 30 días.
- **Baja — atención en 1 día hábil.** Avisos informativos, alarmas de configuración y recomendaciones de mejora.

Una alerta puede elevar su prioridad en cualquier momento si aumenta su impacto; nunca se reduce sin aprobación del nivel 2.

## 8. Comunicación y escalamiento

### 8.1 Niveles de escalamiento
- **Nivel 1 – Operador NOC (24x7):** recibe la alerta, abre ticket, ejecuta diagnóstico y acciones descritas en este runbook.
- **Nivel 2 – Especialista de plataforma** (Kubernetes, red virtual o almacenamiento): decisiones de recuperación, cambios fuera del runbook.
- **Nivel 3 – Arquitecto de plataforma y proveedor:** fallas mayores, casos de soporte con el fabricante.

### 8.2 Comunicación
1. Incidentes críticos: aviso inicial en el canal de incidentes en un máximo de 15 minutos, con actualizaciones cada 30 minutos hasta la resolución.
2. Incidentes altos: actualización cada 2 horas.
3. Si un incidente afecta la banca en línea o la app, notificar a la Mesa de Atención al Cliente para que informe a los clientes que llamen.
4. Al cerrar un incidente crítico o alto, se elabora un informe posterior al incidente en un máximo de 5 días hábiles, con causa raíz y acciones preventivas.
