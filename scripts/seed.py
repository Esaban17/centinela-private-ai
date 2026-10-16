"""Genera data/banco.db con datos FICTICIOS y deterministas del Banco Atitlán.

    python -m scripts.seed [--db RUTA] [--fecha-referencia AAAA-MM-DD]

Misma semilla + misma fecha de referencia ⇒ exactamente la misma base de datos.
Las fechas se anclan a la fecha de referencia (por defecto, hoy) para que los casos
del guion ("este mes", "últimas 24 horas") siempre caigan dentro de las ventanas.

DEMO / DATOS FICTICIOS: ningún nombre, cuenta ni comercio corresponde a una
persona o institución real.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from comun import config

# ─────────────────────────── Esquema ───────────────────────────

ESQUEMA = """
CREATE TABLE meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE clientes (
    id            TEXT PRIMARY KEY,
    nombre        TEXT NOT NULL,
    segmento      TEXT NOT NULL CHECK (segmento IN ('personal','preferente','pyme','corporativo')),
    departamento  TEXT NOT NULL,
    fecha_alta    TEXT NOT NULL,
    riesgo_kyc    TEXT NOT NULL CHECK (riesgo_kyc IN ('bajo','medio','alto')),
    ejecutivo     TEXT NOT NULL
);

CREATE TABLE cuentas (
    id          TEXT PRIMARY KEY,
    cliente_id  TEXT NOT NULL REFERENCES clientes(id),
    tipo        TEXT NOT NULL CHECK (tipo IN ('monetaria','ahorro','tarjeta','préstamo')),
    moneda      TEXT NOT NULL CHECK (moneda IN ('GTQ','USD')),
    saldo       REAL NOT NULL,
    estado      TEXT NOT NULL CHECK (estado IN ('activa','bloqueada','cerrada'))
);

CREATE TABLE transacciones (
    id           INTEGER PRIMARY KEY,
    cuenta_id    TEXT NOT NULL REFERENCES cuentas(id),
    fecha_hora   TEXT NOT NULL,
    tipo         TEXT NOT NULL CHECK (tipo IN ('deposito','retiro','compra','transferencia_enviada',
                                               'transferencia_recibida','pago_servicio','pago_prestamo')),
    monto        REAL NOT NULL CHECK (monto > 0),
    canal        TEXT NOT NULL CHECK (canal IN ('app','agencia','ATM','ACH','POS')),
    comercio     TEXT,
    ubicacion    TEXT NOT NULL,
    descripcion  TEXT NOT NULL
);
CREATE INDEX ix_trx_cuenta_fecha ON transacciones(cuenta_id, fecha_hora);

CREATE TABLE tickets (
    id          INTEGER PRIMARY KEY,
    tipo        TEXT NOT NULL CHECK (tipo IN ('fraude','servicio','infraestructura')),
    prioridad   TEXT NOT NULL CHECK (prioridad IN ('baja','media','alta','critica')),
    estado      TEXT NOT NULL CHECK (estado IN ('abierto','en_progreso','resuelto','cerrado')),
    titulo      TEXT NOT NULL,
    detalle     TEXT NOT NULL,
    cliente_id  TEXT REFERENCES clientes(id),
    creado_por  TEXT NOT NULL,
    fecha       TEXT NOT NULL
);

CREATE TABLE tickets_notas (
    id         INTEGER PRIMARY KEY,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
    fecha      TEXT NOT NULL,
    autor      TEXT NOT NULL,
    estado     TEXT NOT NULL,
    nota       TEXT NOT NULL
);

CREATE TABLE alertas_infra (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    severidad   TEXT NOT NULL CHECK (severidad IN ('info','advertencia','critica')),
    componente  TEXT NOT NULL CHECK (componente IN ('vCenter','host ESXi','NSX Edge','clúster VKS','almacenamiento')),
    mensaje     TEXT NOT NULL,
    estado      TEXT NOT NULL CHECK (estado IN ('abierta','reconocida','resuelta'))
);

CREATE TABLE auditoria (
    id                 INTEGER PRIMARY KEY,
    fecha              TEXT NOT NULL,
    usuario            TEXT NOT NULL,
    herramienta        TEXT NOT NULL,
    parametros         TEXT NOT NULL,
    resultado_resumen  TEXT NOT NULL,
    modelo             TEXT NOT NULL,
    estado             TEXT NOT NULL CHECK (estado IN ('ok','error','rechazado'))
);

CREATE TABLE diccionario (
    tabla        TEXT NOT NULL,
    columna      TEXT NOT NULL,
    descripcion  TEXT NOT NULL,
    PRIMARY KEY (tabla, columna)
);

CREATE VIEW v_esquema AS
    SELECT tabla, columna, descripcion FROM diccionario ORDER BY tabla, rowid;
"""

DICCIONARIO: dict[str, dict[str, str]] = {
    "clientes": {
        "id": "Identificador del cliente, formato CLI-0000",
        "nombre": "Nombre completo (ficticio)",
        "segmento": "personal | preferente | pyme | corporativo",
        "departamento": "Departamento de Guatemala de residencia",
        "fecha_alta": "Fecha de vinculación al banco (AAAA-MM-DD)",
        "riesgo_kyc": "Calificación de debida diligencia: bajo | medio | alto",
        "ejecutivo": "Ejecutivo de cuenta asignado",
    },
    "cuentas": {
        "id": "Número de cuenta, formato AT-00000-00",
        "cliente_id": "Cliente titular (clientes.id)",
        "tipo": "monetaria | ahorro | tarjeta | préstamo",
        "moneda": "GTQ (quetzales) o USD (dólares)",
        "saldo": "Saldo actual; en tarjeta y préstamo es el monto adeudado",
        "estado": "activa | bloqueada | cerrada",
    },
    "transacciones": {
        "id": "Identificador de la transacción",
        "cuenta_id": "Cuenta afectada (cuentas.id)",
        "fecha_hora": "Fecha y hora local (AAAA-MM-DD HH:MM:SS, America/Guatemala)",
        "tipo": "deposito | retiro | compra | transferencia_enviada | transferencia_recibida | pago_servicio | pago_prestamo",
        "monto": "Monto positivo en la moneda de la cuenta; el tipo indica la dirección",
        "canal": "app | agencia | ATM | ACH | POS (terminal punto de venta)",
        "comercio": "Comercio o contraparte, si aplica",
        "ubicacion": "Ciudad donde se originó la operación",
        "descripcion": "Texto libre de la operación",
    },
    "tickets": {
        "id": "Número de ticket",
        "tipo": "fraude | servicio | infraestructura",
        "prioridad": "baja | media | alta | critica",
        "estado": "abierto | en_progreso | resuelto | cerrado",
        "titulo": "Título corto",
        "detalle": "Descripción del caso",
        "cliente_id": "Cliente relacionado (puede ser nulo)",
        "creado_por": "Usuario o sistema que lo creó",
        "fecha": "Fecha y hora de creación",
    },
    "tickets_notas": {
        "id": "Identificador de la nota",
        "ticket_id": "Ticket (tickets.id)",
        "fecha": "Fecha y hora de la nota",
        "autor": "Quién la registró",
        "estado": "Estado del ticket después de la nota",
        "nota": "Texto de la nota",
    },
    "alertas_infra": {
        "id": "Identificador de la alerta del NOC",
        "fecha": "Fecha y hora de la alerta",
        "severidad": "info | advertencia | critica",
        "componente": "vCenter | host ESXi | NSX Edge | clúster VKS | almacenamiento",
        "mensaje": "Detalle técnico",
        "estado": "abierta | reconocida | resuelta",
    },
    "auditoria": {
        "id": "Identificador del registro",
        "fecha": "Fecha y hora de la llamada",
        "usuario": "Usuario que originó la llamada",
        "herramienta": "Herramienta MCP invocada",
        "parametros": "Parámetros (JSON, con cuentas enmascaradas)",
        "resultado_resumen": "Resumen del resultado",
        "modelo": "Modelo de IA que decidió la llamada",
        "estado": "ok | error | rechazado",
    },
}

# ─────────────────────────── Catálogos ficticios ───────────────────────────

NOMBRES_F = ["María José", "Ana Lucía", "Andrea", "Gabriela", "Sofía", "Daniela", "Carmen", "Rosa Elena",
             "Lourdes", "Paola", "Mónica", "Claudia", "Silvia", "Beatriz", "Marta Julia", "Alejandra",
             "Isabel", "Verónica", "Karla", "Ixchel", "Nancy", "Brenda", "Glenda", "Wendy"]
NOMBRES_M = ["José Luis", "Juan Carlos", "Luis Fernando", "Carlos Roberto", "Mario", "Edgar", "Byron",
             "Otto", "Erick", "Jorge Mario", "Diego", "Pablo", "Sergio", "Héctor", "Rolando", "Marvin",
             "Francisco", "Óscar", "Kevin", "Julio César", "Estuardo", "Gerson", "Alfredo", "Rodrigo"]
APELLIDOS = ["López", "García", "Pérez", "Hernández", "Morales", "Castillo", "Monterroso", "Barrios",
             "Orellana", "Estrada", "Archila", "Recinos", "Sandoval", "Cifuentes", "Batres", "Cojtí",
             "Xicará", "Tzul", "Ajú", "Chávez", "Gómez", "Ramírez", "Méndez", "Solórzano", "Quiñónez",
             "Arévalo", "Girón", "Velásquez", "Ordóñez", "Cabrera", "Mazariegos", "Son", "Coyoy",
             "Tecún", "Bran", "Lemus", "Alvarado", "Fuentes", "Marroquín", "Rivas"]

# Departamento → ciudad principal (usada como ubicación de las operaciones)
DEPARTAMENTOS = {
    "Guatemala": "Ciudad de Guatemala", "Sacatepéquez": "Antigua Guatemala",
    "Quetzaltenango": "Quetzaltenango", "Escuintla": "Escuintla", "Alta Verapaz": "Cobán",
    "Huehuetenango": "Huehuetenango", "San Marcos": "San Marcos", "Petén": "Flores",
    "Izabal": "Puerto Barrios", "Chimaltenango": "Chimaltenango", "Sololá": "Panajachel",
    "Zacapa": "Zacapa", "Retalhuleu": "Retalhuleu", "Suchitepéquez": "Mazatenango",
    "Jutiapa": "Jutiapa", "Totonicapán": "Totonicapán", "Quiché": "Santa Cruz del Quiché",
    "Chiquimula": "Chiquimula", "Jalapa": "Jalapa", "Santa Rosa": "Cuilapa",
    "Baja Verapaz": "Salamá", "El Progreso": "Guastatoya",
}
PESO_DEPTO = {"Guatemala": 30, "Quetzaltenango": 8, "Sacatepéquez": 6, "Escuintla": 5, "Alta Verapaz": 4,
              "Huehuetenango": 4, "San Marcos": 3, "Petén": 3, "Izabal": 3, "Chimaltenango": 3}

EJECUTIVOS = ["Andrea Solórzano", "Luis Mazariegos", "Paola Recinos", "Héctor Girón",
              "Mónica Arévalo", "Sergio Batres", "Karla Velásquez", "Rolando Tecún"]

COMERCIOS = ["Supermercado La Ceiba", "Farmacia San Rafael", "Gasolinera El Volcán", "Restaurante El Fogón",
             "Ferretería El Progreso", "Librería Los Cuchumatanes", "Panadería La Espiga",
             "Óptica Visión Clara", "Almacén Textiles Atitlán", "Cafetería Grano de Oro",
             "Tienda de Electrónica Quetzal", "Agroservicio La Milpa", "Clínica Dental Sonrisa",
             "Mercado Digital Chapín", "Hotel Lago Azul"]
SERVICIOS = ["Energía eléctrica (ficticia)", "Agua municipal (ficticia)", "Telefonía móvil (ficticia)",
             "Internet residencial (ficticio)", "Colegio Bilingüe Mirador (ficticio)"]

SEGMENTOS = {  # peso, ticket promedio en GTQ, rango de saldo monetaria
    "personal": (60, 350, (2_000, 35_000)),
    "preferente": (20, 900, (25_000, 250_000)),
    "pyme": (15, 2_500, (40_000, 600_000)),
    "corporativo": (5, 9_000, (300_000, 5_000_000)),
}

# Clientes fijos usados por el guion (ids estables).
CLIENTE_GUION = {
    "id": "CLI-0042", "nombre": "Lucía Fernanda Cojtí Barrios", "segmento": "preferente",
    "departamento": "Quetzaltenango", "riesgo_kyc": "bajo", "ejecutivo": "Andrea Solórzano",
}
CLIENTE_VISION = {  # usado en Fase 3 (documento con discrepancia de titular)
    "id": "CLI-0107", "nombre": "Carlos Enrique Xicará Morales", "segmento": "pyme",
    "departamento": "Sacatepéquez", "riesgo_kyc": "medio", "ejecutivo": "Luis Mazariegos",
}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _nombre(rng: random.Random, usados: set[str]) -> str:
    while True:
        pila = rng.choice(NOMBRES_F if rng.random() < 0.5 else NOMBRES_M)
        a1, a2 = rng.sample(APELLIDOS, 2)
        nombre = f"{pila} {a1} {a2}"
        if nombre not in usados:
            usados.add(nombre)
            return nombre


def _hora_diurna(rng: random.Random) -> tuple[int, int, int]:
    h = min(21, max(7, int(rng.gauss(13, 3))))
    return h, rng.randrange(60), rng.randrange(60)


# ─────────────────────────── Generación ───────────────────────────

def generar(db_path: Path, fecha_ref: date, semilla: int, n_clientes: int, n_trx: int, dias: int) -> dict:
    rng = random.Random(semilla)
    ahora = datetime.combine(fecha_ref, datetime.min.time()).replace(hour=9)
    inicio = ahora - timedelta(days=dias)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(ESQUEMA)

    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("banco", "Banco Atitlán (ficticio)"),
        ("aviso", "DEMO / DATOS FICTICIOS"),
        ("semilla", str(semilla)),
        ("fecha_referencia", _fmt(ahora)),
        ("cliente_guion", CLIENTE_GUION["id"]),
        ("cliente_vision", CLIENTE_VISION["id"]),
    ])
    for tabla, cols in DICCIONARIO.items():
        con.executemany("INSERT INTO diccionario VALUES (?,?,?)", [(tabla, c, d) for c, d in cols.items()])

    # Clientes
    deptos = list(DEPARTAMENTOS)
    pesos_depto = [PESO_DEPTO.get(d, 1) for d in deptos]
    segs = list(SEGMENTOS)
    usados = {CLIENTE_GUION["nombre"], CLIENTE_VISION["nombre"]}
    clientes = []
    for i in range(1, n_clientes + 1):
        cid = f"CLI-{i:04d}"
        fijo = {CLIENTE_GUION["id"]: CLIENTE_GUION, CLIENTE_VISION["id"]: CLIENTE_VISION}.get(cid)
        if fijo:
            c = dict(fijo)
        else:
            c = {
                "id": cid, "nombre": _nombre(rng, usados),
                "segmento": rng.choices(segs, [SEGMENTOS[s][0] for s in segs])[0],
                "departamento": rng.choices(deptos, pesos_depto)[0],
                "riesgo_kyc": rng.choices(["bajo", "medio", "alto"], [70, 24, 6])[0],
                "ejecutivo": rng.choice(EJECUTIVOS),
            }
        c["fecha_alta"] = (fecha_ref - timedelta(days=rng.randint(dias + 30, 365 * 12))).isoformat()
        clientes.append(c)
    con.executemany(
        "INSERT INTO clientes VALUES (:id,:nombre,:segmento,:departamento,:fecha_alta,:riesgo_kyc,:ejecutivo)",
        clientes)

    # Cuentas
    cuentas = []
    for c in clientes:
        n = int(c["id"][4:])
        _, ticket, (smin, smax) = SEGMENTOS[c["segmento"]]
        productos = [("monetaria", "GTQ")]
        if rng.random() < 0.6:
            productos.append(("ahorro", "USD" if rng.random() < 0.3 else "GTQ"))
        if rng.random() < 0.55:
            productos.append(("tarjeta", "GTQ"))
        if rng.random() < 0.2:
            productos.append(("préstamo", "GTQ"))
        if c["id"] == CLIENTE_GUION["id"]:
            productos = [("monetaria", "GTQ"), ("ahorro", "USD"), ("tarjeta", "GTQ")]
        if c["id"] == CLIENTE_VISION["id"]:
            productos = [("monetaria", "GTQ"), ("ahorro", "GTQ")]
        for j, (tipo, moneda) in enumerate(productos, start=1):
            saldo = rng.uniform(smin, smax)
            if tipo == "ahorro":
                saldo *= 0.6
            if moneda == "USD":
                saldo /= 7.7
            if tipo == "tarjeta":
                saldo = rng.uniform(0, ticket * 15)
            if tipo == "préstamo":
                saldo = rng.uniform(smin, smax) * 2
            cuentas.append({"id": f"AT-{n:05d}-{j:02d}", "cliente_id": c["id"], "tipo": tipo,
                            "moneda": moneda, "saldo": round(saldo, 2),
                            "estado": "bloqueada" if rng.random() < 0.01 else "activa"})
    # Saldos fijos para el guion (fáciles de decir en voz alta)
    fijos = {"AT-00042-01": 48_350.75, "AT-00042-02": 12_800.00, "AT-00042-03": 6_240.30,
             "AT-00107-01": 182_400.00, "AT-00107-02": 95_000.00}
    for cu in cuentas:
        if cu["id"] in fijos:
            cu["saldo"], cu["estado"] = fijos[cu["id"]], "activa"
    con.executemany("INSERT INTO cuentas VALUES (:id,:cliente_id,:tipo,:moneda,:saldo,:estado)", cuentas)

    # Transacciones normales
    cli_por_id = {c["id"]: c for c in clientes}
    # La clienta del guion recibe un historial controlado más abajo.
    operables = [cu for cu in cuentas if cu["tipo"] != "préstamo" and cu["cliente_id"] != CLIENTE_GUION["id"]]
    trx: list[tuple] = []

    def agregar(cuenta, dt, tipo, monto, canal, comercio, ubicacion, descripcion):
        trx.append((cuenta["id"], _fmt(dt), tipo, round(monto, 2), canal, comercio, ubicacion, descripcion))

    for cu in cuentas:  # cuotas mensuales de préstamos
        if cu["tipo"] != "préstamo":
            continue
        ciudad = DEPARTAMENTOS[cli_por_id[cu["cliente_id"]]["departamento"]]
        cuota = round(cu["saldo"] / 48, 2)
        for k in range(dias // 30):
            dt = (inicio + timedelta(days=30 * k + 5)).replace(hour=10)
            agregar(cu, dt, "pago_prestamo", cuota, "app", None, ciudad, "Pago de cuota mensual")

    restantes = n_trx - len(trx)
    pesos = [3 if cu["tipo"] == "monetaria" else 2 if cu["tipo"] == "tarjeta" else 1 for cu in operables]
    for _ in range(restantes):
        cu = rng.choices(operables, pesos)[0]
        cli = cli_por_id[cu["cliente_id"]]
        ticket = SEGMENTOS[cli["segmento"]][1]
        ciudad = DEPARTAMENTOS[cli["departamento"]]
        if rng.random() < 0.12:
            ciudad = rng.choice(["Ciudad de Guatemala", "Antigua Guatemala", ciudad])
        dia = inicio + timedelta(days=rng.randrange(dias))
        h, m, s = _hora_diurna(rng)
        dt = dia.replace(hour=h, minute=m, second=s)
        if dt >= ahora:
            dt -= timedelta(days=1)
        monto = max(25.0, rng.lognormvariate(0, 0.6) * ticket)
        if cu["moneda"] == "USD":
            monto /= 7.7
        if cu["tipo"] == "tarjeta":
            comercio = rng.choice(COMERCIOS)
            agregar(cu, dt, "compra", monto, rng.choice(["POS", "POS", "app"]), comercio, ciudad,
                    f"Compra en {comercio}")
            continue
        tipo = rng.choices(["deposito", "retiro", "transferencia_enviada", "transferencia_recibida",
                            "pago_servicio"], [18, 22, 22, 20, 18])[0]
        if tipo == "deposito":
            agregar(cu, dt, tipo, monto * 2, "agencia", None, ciudad, "Depósito en agencia")
        elif tipo == "retiro":
            agregar(cu, dt, tipo, min(monto, 5000), rng.choice(["ATM", "ATM", "agencia"]), None, ciudad,
                    "Retiro de efectivo")
        elif tipo == "transferencia_enviada":
            agregar(cu, dt, tipo, monto, rng.choice(["app", "ACH"]), "Cuenta de tercero", ciudad,
                    "Transferencia a tercero")
        elif tipo == "transferencia_recibida":
            agregar(cu, dt, tipo, monto * 1.5, rng.choice(["app", "ACH"]), "Cuenta de tercero", ciudad,
                    "Transferencia recibida")
        else:
            servicio = rng.choice(SERVICIOS)
            agregar(cu, dt, tipo, monto * 0.5, "app", servicio, ciudad, f"Pago de {servicio}")

    # Casos sembrados para el guion: CLI-0042. Primero un historial normal y estable
    # (compras diurnas en Quetzaltenango, ~Q480) para que la línea base sea clara.
    cta = {cu["id"]: cu for cu in cuentas}
    tarjeta, monetaria = cta["AT-00042-03"], cta["AT-00042-01"]
    for _ in range(60):
        dt = (inicio + timedelta(days=rng.randrange(dias - 1))).replace(
            hour=rng.randint(8, 19), minute=rng.randrange(60), second=rng.randrange(60))
        monto = max(60.0, rng.gauss(480, 160))
        if rng.random() < 0.6:
            comercio = rng.choice(COMERCIOS[:10])
            agregar(tarjeta, dt, "compra", monto, "POS", comercio, "Quetzaltenango", f"Compra en {comercio}")
        else:
            servicio = rng.choice(SERVICIOS)
            agregar(monetaria, dt, "pago_servicio", monto, "app", servicio, "Quetzaltenango",
                    f"Pago de {servicio}")
    for k in range(dias // 30):  # salario mensual
        dt = (inicio + timedelta(days=30 * k + 14)).replace(hour=8, minute=5)
        agregar(monetaria, dt, "transferencia_recibida", 14_500.00, "ACH", "Planilla Cooperativa Altiplano (ficticia)",
                "Quetzaltenango", "Acreditamiento de salario")

    # Anomalías de este mes.
    d6 = (ahora - timedelta(days=6)).replace(hour=2, minute=47, second=12)
    agregar(tarjeta, d6, "compra", 18_750.00, "POS", "Electrónica Costa Azul (ficticio)",
            "Ciudad de Panamá, Panamá", "Compra en Electrónica Costa Azul")
    agregar(tarjeta, d6 + timedelta(minutes=25, seconds=31), "compra", 12_480.00, "POS",
            "Joyería Puerto Libre (ficticio)", "Ciudad de Panamá, Panamá", "Compra en Joyería Puerto Libre")
    d4 = (ahora - timedelta(days=4)).replace(hour=1, minute=31, second=5)
    for k, minutos in enumerate([0, 4, 9, 15]):
        agregar(monetaria, d4 + timedelta(minutes=minutos, seconds=7 * k), "transferencia_enviada", 4_950.00,
                "ACH", "Cuenta externa ••••7731", "Quetzaltenango",
                "Transferencia ACH a cuenta de otro banco")

    trx.sort(key=lambda t: t[1])
    con.executemany(
        "INSERT INTO transacciones (cuenta_id,fecha_hora,tipo,monto,canal,comercio,ubicacion,descripcion) "
        "VALUES (?,?,?,?,?,?,?,?)", trx)

    # Tickets
    tickets = [
        ("fraude", "alta", "abierto", "Cargos no reconocidos en tarjeta",
         "Cliente reporta dos compras en línea que no reconoce.", rng.choice(clientes)["id"], "agencia.zona10", 2),
        ("fraude", "media", "en_progreso", "Posible suplantación en app",
         "Cambio de dispositivo seguido de transferencia inusual.", rng.choice(clientes)["id"], "motor.reglas", 5),
        ("fraude", "baja", "cerrado", "Retiro ATM disputado",
         "Retiro reconocido por el cliente tras revisión de video.", rng.choice(clientes)["id"], "call.center", 40),
        ("servicio", "media", "abierto", "Actualización de datos KYC",
         "Cliente pyme debe actualizar escritura constitutiva.", CLIENTE_VISION["id"], "ejecutivo.mazariegos", 3),
        ("servicio", "baja", "abierto", "Reposición de tarjeta",
         "Tarjeta deteriorada, solicita reposición.", rng.choice(clientes)["id"], "agencia.xela", 1),
        ("servicio", "media", "en_progreso", "Reclamo por cobro de comisión",
         "Cliente indica cobro doble de comisión por transferencia ACH.", rng.choice(clientes)["id"],
         "call.center", 6),
        ("servicio", "alta", "abierto", "Acceso bloqueado a banca en línea",
         "Cliente corporativo sin acceso desde hace 2 días.", rng.choice(clientes)["id"], "ejecutivo.giron", 2),
        ("servicio", "baja", "resuelto", "Constancia de saldo",
         "Emisión de constancia para trámite migratorio.", rng.choice(clientes)["id"], "agencia.zona10", 12),
        ("infraestructura", "media", "en_progreso", "Renovar certificados de vCenter",
         "Certificados de máquina vencen en 21 días.", None, "noc.turno.b", 4),
        ("infraestructura", "baja", "abierto", "Capacidad de datastore vsan-prod",
         "Uso sostenido por encima del 80 %; planificar expansión.", None, "noc.turno.a", 9),
        ("infraestructura", "alta", "resuelto", "Host ESXi esx-07 con memoria degradada",
         "DIMM reemplazado; host de vuelta en el clúster.", None, "noc.turno.c", 15),
        ("servicio", "media", "abierto", "Solicitud de aumento de límite ACH",
         "Pyme solicita subir límite diario de transferencias.", rng.choice(clientes)["id"], "app.formulario", 1),
    ]
    con.executemany(
        "INSERT INTO tickets (tipo,prioridad,estado,titulo,detalle,cliente_id,creado_por,fecha) VALUES (?,?,?,?,?,?,?,?)",
        [(t, p, e, ti, de, cl, cr, _fmt(ahora - timedelta(days=d, hours=rng.randrange(1, 8))))
         for t, p, e, ti, de, cl, cr, d in tickets])

    # Alertas de infraestructura: ruido de 7 días + 2 críticas abiertas recientes.
    ruido = [
        ("info", "vCenter", "Respaldo de configuración de vCenter completado."),
        ("info", "host ESXi", "Host {h} salió de modo mantenimiento."),
        ("advertencia", "host ESXi", "Host {h}: uso de CPU por encima de 85 % durante 15 minutos."),
        ("advertencia", "almacenamiento", "Latencia de escritura en vsan-prod por encima de 20 ms."),
        ("info", "clúster VKS", "Clúster vks-dev-01 escalado a 5 nodos de trabajo."),
        ("advertencia", "NSX Edge", "Edge {e}: uso de CPU del datapath por encima de 70 %."),
        ("info", "NSX Edge", "Sesión BGP restablecida en {e}."),
        ("advertencia", "vCenter", "Servicio vpxd reiniciado automáticamente."),
    ]
    alertas = []
    for _ in range(38):
        sev, comp, msg = rng.choice(ruido)
        dt = ahora - timedelta(hours=rng.uniform(6, 24 * 7))
        msg = msg.format(h=f"esx-{rng.randint(1, 12):02d}", e=f"edge-0{rng.randint(1, 4)}")
        alertas.append((_fmt(dt), sev, comp, msg, rng.choice(["resuelta", "resuelta", "reconocida"])))
    alertas += [
        (_fmt(ahora - timedelta(hours=1, minutes=40)), "critica", "clúster VKS",
         "Clúster vks-prod-01: 2 de 3 nodos del plano de control en NotReady; etcd sin quórum. "
         "Servicios de banca en línea degradados.", "abierta"),
        (_fmt(ahora - timedelta(hours=3, minutes=5)), "critica", "NSX Edge",
         "Edge edge-02 (clúster edge-cl-01): failover de HA; sesiones BGP con ToR caídas en uplink-2.",
         "abierta"),
        (_fmt(ahora - timedelta(hours=5, minutes=20)), "advertencia", "almacenamiento",
         "Datastore vsan-prod al 86 % de capacidad.", "abierta"),
    ]
    alertas.sort()
    con.executemany("INSERT INTO alertas_infra (fecha,severidad,componente,mensaje,estado) VALUES (?,?,?,?,?)",
                    alertas)

    con.commit()
    resumen = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ["clientes", "cuentas", "transacciones", "tickets", "alertas_infra"]}
    resumen["fecha_referencia"] = _fmt(ahora)
    con.close()
    return resumen


def main() -> None:
    cfg = config.cargar()["seed"]
    p = argparse.ArgumentParser(description="Genera la base ficticia del Banco Atitlán.")
    p.add_argument("--db", type=Path, default=config.ruta("db"))
    p.add_argument("--fecha-referencia", default=cfg.get("fecha_referencia") or date.today().isoformat())
    a = p.parse_args()
    resumen = generar(a.db, date.fromisoformat(a.fecha_referencia), int(cfg["semilla"]),
                      int(cfg["clientes"]), int(cfg["transacciones_objetivo"]), int(cfg["dias_historia"]))
    print(f"Base generada en {a.db}")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
