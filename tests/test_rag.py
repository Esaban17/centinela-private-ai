"""RAG de políticas: fragmentación por secciones, ingesta y búsqueda híbrida con Ollama simulado."""

from pathlib import Path

import pytest
from mcp.client.client import Client

from comun import config
from mcp_server import rag
from mcp_server.server import crear_servidor
from scripts.ingest import construir, fragmentar

POLITICAS = config.RAIZ / "data" / "politicas"


@pytest.fixture
def indice(cfg, ollama_falso) -> Path:
    destino = config.ruta("vectores", cfg)
    construir(POLITICAS, destino, cfg, ollama_falso.cliente)
    return destino


@pytest.fixture
def servidor_rag(db, cfg, indice, ollama_falso):
    return crear_servidor(db, cfg, ollama=ollama_falso.cliente)


async def buscar(servidor, consulta, k=4):
    async with Client(servidor) as c:
        r = await c.call_tool("buscar_politicas", {"consulta": consulta, "k": k})
    assert not r.is_error, r.content
    return r.structured_content


def test_hay_entre_6_y_8_politicas_marcadas_demo():
    docs = sorted(POLITICAS.glob("*.md"))
    assert 6 <= len(docs) <= 8
    for d in docs:
        texto = d.read_text(encoding="utf-8")
        assert texto.startswith("# ") and "DEMO / DATOS FICTICIOS" in texto.splitlines()[2]


def test_fragmentos_por_seccion_con_documento_y_codigo():
    frags = fragmentar(POLITICAS / "politica_prevencion_fraude_bloqueo_tarjetas.md")
    assert {f.documento for f in frags} == {"Política de prevención de fraude y bloqueo preventivo de tarjetas"}
    assert {f.codigo for f in frags} == {"POL-FRA-001"}
    secciones = [f.seccion for f in frags]
    assert "3. Criterios de bloqueo preventivo" in secciones
    assert any(" › " in s for s in secciones)  # subsecciones ###
    assert all("DEMO / DATOS FICTICIOS —" not in f.texto for f in frags)  # el aviso no ensucia los fragmentos
    assert all(len(f.texto) <= 1500 for f in frags)


def test_seccion_larga_se_parte_por_parrafos(tmp_path):
    md = tmp_path / "larga.md"
    md.write_text("# Doc\n\n## 1. Larga\n\n" + "\n\n".join("párrafo " * 40 for _ in range(10)), encoding="utf-8")
    frags = fragmentar(md, maximo=800)
    assert len(frags) > 1 and frags[1].seccion == "1. Larga (cont.)"


def test_ingesta_guarda_meta(indice):
    import sqlite3

    con = sqlite3.connect(indice)
    meta = dict(con.execute("SELECT clave, valor FROM meta"))
    assert meta["embeddings"] == "1" and meta["modelo_embeddings"] == "bge-m3"
    assert meta["sqlite_vec"] == "1" and int(meta["documentos"]) == len(list(POLITICAS.glob("*.md")))
    assert int(meta["fragmentos"]) > 40


@pytest.mark.parametrize("consulta,archivo,seccion", [
    ("¿Qué dice la política sobre bloqueo preventivo de tarjetas por compras en el extranjero de madrugada?",
     "politica_prevencion_fraude_bloqueo_tarjetas.md", "Criterios de bloqueo preventivo"),
    ("qué hacer si el titular de la boleta no coincide con el de la cuenta", "manual_validacion_documental.md",
     "titular"),
    ("¿podemos mandar datos de clientes a un servicio de IA en la nube?",
     "politica_uso_ia_clasificacion_datos.md", None),
])
async def test_busqueda_hibrida_cita_documento_y_seccion(servidor_rag, consulta, archivo, seccion):
    r = await buscar(servidor_rag, consulta)
    assert r["disponible"] and r["modo"] == "hibrida" and r["motor_vectorial"] == "sqlite-vec"
    top = r["fragmentos"][0]
    assert top["archivo"] == archivo, [f["cita"] for f in r["fragmentos"]]
    if seccion:  # con embeddings simulados basta que la sección esté entre los fragmentos devueltos
        assert any(seccion.lower() in f["seccion"].lower() for f in r["fragmentos"] if f["archivo"] == archivo)
    assert set(top) >= {"documento", "seccion", "fragmento", "puntaje", "cita"}
    assert 0 < top["puntaje"] <= 1 and top["cita"].startswith(top["documento"])


async def test_sin_ollama_sigue_con_busqueda_lexica(servidor_rag, ollama_falso):
    ollama_falso.caido = True
    r = await buscar(servidor_rag, "bloqueo preventivo de tarjetas")
    assert r["modo"] == "lexica" and r["fragmentos"]
    assert r["fragmentos"][0]["archivo"] == "politica_prevencion_fraude_bloqueo_tarjetas.md"
    assert any("léxica" in a for a in r["avisos"])


async def test_indice_sin_embeddings(db, cfg, ollama_falso):
    construir(POLITICAS, config.ruta("vectores", cfg), cfg, con_embeddings=False)
    r = await buscar(crear_servidor(db, cfg, ollama=ollama_falso.cliente), "titular de la boleta no coincide")
    assert r["modo"] == "lexica" and r["fragmentos"][0]["archivo"] == "manual_validacion_documental.md"
    assert not [p for p, _ in ollama_falso.peticiones if p == "/api/embed"]


async def test_modelo_distinto_al_del_indice_no_mezcla_vectores(db, cfg, indice, ollama_falso):
    cfg["modelos"]["embeddings"]["nombre"] = "qwen3-embedding:0.6b"
    r = await buscar(crear_servidor(db, cfg, ollama=ollama_falso.cliente), "bloqueo preventivo")
    assert r["modo"] == "lexica" and "make ingest" in r["avisos"][0]


def test_similitud_en_python_si_no_carga_sqlite_vec(cfg, indice, ollama_falso, monkeypatch):
    monkeypatch.setattr(rag, "cargar_sqlite_vec", lambda con: False)
    idx = rag.IndicePoliticas(indice, cfg, ollama_falso.cliente)
    r = idx.buscar("bloqueo preventivo de tarjetas en el extranjero de madrugada", 3)
    assert r["motor_vectorial"] == "python" and r["modo"] == "hibrida"
    assert r["fragmentos"][0]["archivo"] == "politica_prevencion_fraude_bloqueo_tarjetas.md"


def test_reabre_el_indice_si_se_reconstruye(cfg, indice, ollama_falso):
    import os

    idx = rag.IndicePoliticas(indice, cfg, ollama_falso.cliente)
    assert idx.buscar("bloqueo", 1)["modo"] == "hibrida"
    construir(POLITICAS, indice, cfg, con_embeddings=False)
    os.utime(indice, (1, 1))  # fuerza otra mtime aunque el sistema de archivos tenga baja resolución
    assert idx.buscar("bloqueo", 1)["modo"] == "lexica"


def test_consulta_fts_es_segura():
    q = rag.consulta_fts('bloqueo "preventivo" NEAR( tarjetas*')
    assert '"bloque' in q and "NEAR(" not in q
    assert rag.consulta_fts("¿qué dice?") is None


async def test_recurso_indice_lista_secciones(servidor_rag):
    async with Client(servidor_rag) as c:
        r = await c.read_resource("politicas://indice")
    texto = r.contents[0].text
    assert "manual_validacion_documental.md" in texto and "3. Criterios de bloqueo preventivo" in texto
