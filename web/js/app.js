// Centinela · arranque de la UI. Une la conexión con las tres zonas.
import { Conexion, pedir, subirDocumento } from './conexion.js';
import { Conversacion } from './conversacion.js';
import { Visor } from './visor.js';
import { Panel } from './panel.js';
import { PushToTalk, Reproductor } from './voz.js';
import { el, almacenar } from './util.js';

const $ = (id) => document.getElementById(id);

const nuevoId = () => `ui-${(crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9]/g, '').slice(0, 24)}`;
let sesionId = almacenar('centinela.sesion') || nuevoId();
almacenar('centinela.sesion', sesionId);

const conversacion = new Conversacion($('mensajes'), {
  alAprobar: (aprobacion_id, aprobar) => {
    if (!conexion.enviar({ tipo: 'aprobacion', aprobacion_id, aprobar, comentario: '' })) {
      pedir(`/api/aprobaciones/${encodeURIComponent(aprobacion_id)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ aprobar, comentario: '' }),
      }).catch((e) => conversacion.nota(`No se pudo enviar la aprobación: ${e.message}`, 'error'));
    }
  },
});
const visor = new Visor($('visor'), $('historial-visor'));
const panel = new Panel();
const reproductor = new Reproductor();
let enCurso = false;
let conexion;

function marcarEnCurso(v) {
  enCurso = v;
  $('btn-cancelar').classList.toggle('oculto', !v);
  $('btn-enviar').disabled = v;
  document.querySelectorAll('.atajo').forEach((b) => { b.disabled = v; });
}

function alEvento(ev) {
  const d = ev.datos || {};
  switch (ev.tipo) {
    case 'sesion.lista':
      if (d.banco) $('banco').textContent = `${d.banco} · Asistente de operaciones con IA privada${d.simulador ? ' · SIMULADOR' : ''}`;
      if (d.marca_demo) $('marca-demo').textContent = d.marca_demo;
      break;
    case 'turno.inicio':
    case 'transcripcion':
      marcarEnCurso(true);
      reproductor.detener();
      break;
    case 'turno.fin':
      marcarEnCurso(false);
      break;
    case 'voz.audio':
      reproductor.agregar(ev.turno_id, d);
      break;
    default:
      break;
  }
  conversacion.evento(ev);
  visor.evento(ev);
  panel.evento(ev);
}

function conectar() {
  conexion?.cerrar();
  conexion = new Conexion(sesionId, {
    alEvento,
    alEstado: (estado) => {
      const n = $('estado-ws');
      n.dataset.estado = { conectado: 'ok', conectando: 'aviso', desconectado: 'alerta' }[estado];
      n.textContent = { conectado: 'Orquestador local ✓', conectando: 'Conectando…', desconectado: 'Reconectando…' }[estado];
      if (estado === 'conectado' && silenciado) enviarPreferencias();
    },
  });
  conexion.conectar();
}

function enviarTexto(texto, documento_id = null) {
  texto = (texto || '').trim();
  if ((!texto && !documento_id) || enCurso) return;
  if (!conexion.enviar({ tipo: 'mensaje', texto, documento_id })) {
    pedir(`/api/sesiones/${encodeURIComponent(sesionId)}/mensajes`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ texto, documento_id }),
    }).catch((e) => conversacion.nota(`No se pudo enviar: ${e.message}`, 'error'));
  }
}

// ─── Entrada de texto ───
const texto = $('texto');
$('form-entrada').addEventListener('submit', (e) => {
  e.preventDefault();
  enviarTexto(texto.value);
  texto.value = '';
  texto.style.height = '';
});
texto.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('form-entrada').requestSubmit(); }
});
texto.addEventListener('input', () => { texto.style.height = ''; texto.style.height = `${texto.scrollHeight}px`; });
$('btn-cancelar').addEventListener('click', () => conexion.enviar({ tipo: 'cancelar' }));

// ─── Atajos del guion (por si la voz falla). Teclas 1-9 fuera del cuadro de texto. ───
let guion = [];
async function cargarGuion() {
  try {
    // Acepta cadenas, {texto, titulo} (simulador) o {id, pregunta} (guion de config.yaml del orquestador).
    const NOMBRES = { politica: 'Política', noc: 'NOC', vision: 'Visión', documento: 'Documento' };
    const titulo = (id) => (id ? NOMBRES[id] || id.charAt(0).toUpperCase() + id.slice(1).replaceAll('_', ' ') : null);
    guion = (await pedir('/api/guion')).map((g, i) => (typeof g === 'string'
      ? { texto: g, titulo: `Pregunta ${i + 1}` }
      : { ...g, texto: g.texto || g.pregunta, titulo: g.titulo || titulo(g.id) || `Pregunta ${i + 1}` }));
  } catch {
    guion = [];
  }
  $('atajos').replaceChildren(...guion.map((g, i) => el('button', {
    type: 'button', class: 'atajo', title: g.texto, onclick: () => enviarTexto(g.texto, g.documento_id || null),
  }, el('b', {}, i + 1), g.titulo || g.texto)));
}
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey || e.altKey) return;
  const n = Number(e.key);
  if (n >= 1 && n <= guion.length) enviarTexto(guion[n - 1].texto, guion[n - 1].documento_id || null);
  if (e.key === 'Escape' && enCurso) conexion.enviar({ tipo: 'cancelar' });
});

// ─── Documentos ───
const menu = $('menu-doc');
$('btn-doc').addEventListener('click', async () => {
  menu.classList.toggle('oculto');
  if (menu.classList.contains('oculto')) return;
  try {
    const docs = await pedir('/api/documentos');
    $('lista-docs').replaceChildren(...(docs.length ? docs.map((d) => el('button', {
      type: 'button', class: 'boton-secundario',
      onclick: () => { menu.classList.add('oculto'); enviarTexto('', d.documento_id); },
    }, `📄 ${d.nombre}`)) : [el('p', {}, 'No hay documentos de muestra.')]));
  } catch (e) {
    $('lista-docs').replaceChildren(el('p', {}, `No se pudo listar: ${e.message}`));
  }
});
$('btn-subir').addEventListener('click', () => $('archivo').click());
$('archivo').addEventListener('change', async (e) => {
  const archivo = e.target.files[0];
  e.target.value = '';
  menu.classList.add('oculto');
  if (!archivo) return;
  try {
    const r = await subirDocumento(archivo);
    enviarTexto('', r.documento_id);
  } catch (err) {
    conversacion.nota(`No se pudo subir el documento: ${err.message}`, 'error');
  }
});

// ─── Voz ───
new PushToTalk({
  boton: $('btn-mic'),
  aviso: $('grabando'),
  alAudio: async (blob) => {
    if (enCurso) return conversacion.nota('Espere a que termine la respuesta en curso.', 'aviso');
    try { await conexion.subirAudio(blob); } catch (e) { conversacion.nota(`No se pudo enviar el audio: ${e.message}`, 'error'); }
  },
  alError: (m) => conversacion.nota(m, 'aviso'),
}).enlazar();

const btnSilencio = $('btn-silencio');
let silenciado = false;
/** Silenciar corta lo que suena y le pide al orquestador que no sintetice (mensaje `preferencias`, contrato 1.1). */
function enviarPreferencias() {
  if (!conexion.enviar({ tipo: 'preferencias', hablar: !silenciado })) {
    pedir(`/api/sesiones/${encodeURIComponent(sesionId)}/preferencias`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hablar: !silenciado }),
    }).catch(() => { /* se reintenta al reconectar */ });
  }
}
function aplicarSilencio(on) {
  silenciado = on;
  reproductor.silenciar(on);
  if (conexion) enviarPreferencias();
  btnSilencio.setAttribute('aria-pressed', String(on));
  btnSilencio.textContent = on ? '🔇 Voz' : '🔊 Voz';
  almacenar('centinela.silencio', on ? '1' : null);
}
btnSilencio.addEventListener('click', () => aplicarSilencio(btnSilencio.getAttribute('aria-pressed') !== 'true'));
aplicarSilencio(almacenar('centinela.silencio') === '1');

// ─── Sesión nueva ───
$('btn-nueva').addEventListener('click', () => {
  sesionId = nuevoId();
  almacenar('centinela.sesion', sesionId);
  conversacion.limpiar();
  visor.limpiar();
  panel.reiniciarSesion();
  marcarEnCurso(false);
  conectar();
});

conectar();
cargarGuion();
panel.iniciar();
