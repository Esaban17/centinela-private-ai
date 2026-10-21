// Selector de modelos y alerta de RAM (GET/POST /api/modelos, ver orchestrator/modelos_api.py).
// - Píldora «Modelos» en la barra: verde si la selección cabe, ámbar si va justa, roja si se pasa de la RAM.
// - Franja de alerta bajo la barra cuando no está en verde.
// - Diálogo: perfil (48gb, 24gb…), un modelo por rol y la estimación al vuelo antes de aplicar.
import { el, ROLES } from './util.js';

const $ = (id) => document.getElementById(id);
const ROLES_ELEGIBLES = ['agente', 'rapido', 'vision', 'embeddings'];
const PILDORA = { ok: 'ok', justo: 'aviso', excede: 'alerta' };
const MANUAL = 'manual';

async function api(url, cuerpo) {
  const r = await fetch(url, cuerpo === undefined ? { cache: 'no-store' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo),
  });
  let datos = null;
  try { datos = await r.json(); } catch { /* sin cuerpo */ }
  if (!r.ok) {
    const d = datos?.detail;
    const e = new Error(typeof d === 'string' ? d : d?.mensaje || `Error ${r.status}`);
    e.detalle = d;
    throw e;
  }
  return datos;
}

const gb = (v) => (v === null || v === undefined ? '¿? GB' : `${Number(v).toFixed(1)} GB`);

export class SelectorModelos {
  constructor({ alAplicar } = {}) {
    this.alAplicar = alAplicar;
    this.datos = null;
    this.perfilBase = null;
    this.pedido = 0;
  }

  iniciar() {
    const abrir = () => this.abrir();
    $('estado-ram').addEventListener('click', abrir);
    $('aviso-ram-boton').addEventListener('click', abrir);
    $('dm-cerrar').addEventListener('click', () => $('dialogo-modelos').close());
    $('dm-cancelar').addEventListener('click', () => $('dialogo-modelos').close());
    $('dm-perfil').addEventListener('change', () => this._elegirPerfil($('dm-perfil').value));
    $('dm-aplicar').addEventListener('click', () => this._aplicar(false));
    this.refrescar();
  }

  async refrescar() {
    try {
      this.datos = await api('/api/modelos');
    } catch {
      $('estado-ram').classList.add('oculto');  // orquestador sin el selector (o caído): no se muestra
      return null;
    }
    $('estado-ram').classList.remove('oculto');
    this._pintarAlerta(this.datos.estimacion);
    return this.datos;
  }

  _pintarAlerta(est) {
    const p = $('estado-ram');
    p.dataset.estado = PILDORA[est.nivel] || '';
    p.textContent = `⚙ Modelos · ${gb(est.total_gb).replace(' GB', '')} de ${est.ram_gb} GB`;
    p.title = `${est.mensaje}${est.avisos.length ? `\n${est.avisos.join('\n')}` : ''}\nClic para elegir los modelos.`;
    const aviso = $('aviso-ram');
    const mostrar = est.nivel !== 'ok';
    aviso.classList.toggle('oculto', !mostrar);
    aviso.dataset.nivel = est.nivel;
    $('aviso-ram-texto').textContent = mostrar ? `⚠ Memoria: ${est.mensaje}` : '';
  }

  async abrir() {
    const d = await this.refrescar();
    if (!d) return;
    $('dm-resultado').textContent = '';
    const r = d.estimacion;
    $('dm-equipo').textContent = `Este equipo: ${r.ram_gb} GB de RAM · GPU hasta ~${r.gpu.limite_gb} GB (${r.gpu.fuente}) · `
      + `perfil vigente: ${d.perfil} (${d.motivo})${d.ollama_disponible ? '' : ' · Ollama no responde: no se puede ver qué está descargado'}`;
    const sel = $('dm-perfil');
    sel.replaceChildren(...d.perfiles.map((p) => el('option', { value: p.id },
      `${p.id} · ~${p.estimacion.total_gb} GB${p.estimacion.nivel === 'excede' ? ' · no cabe aquí' : ''}`)),
    el('option', { value: MANUAL }, 'Personalizado'));
    this.perfilBase = d.perfiles.some((p) => p.id === d.perfil) ? d.perfil : MANUAL;
    sel.value = this.perfilBase;
    this._pintarRoles(d.seleccion);
    this._pintarDescripcion();
    this._pintarEstimacion(r);
    if (!$('dialogo-modelos').open) $('dialogo-modelos').showModal();
  }

  _pintarDescripcion() {
    const p = this.datos.perfiles.find((x) => x.id === $('dm-perfil').value);
    $('dm-perfil-desc').textContent = p
      ? `${p.descripcion}${p.ram_minima_gb ? ` · «auto» lo elige con ${p.ram_minima_gb} GB o más` : ''}`
      : `Modelos elegidos a mano${this.perfilBase && this.perfilBase !== MANUAL ? ` (a partir de ${this.perfilBase})` : ''}.`;
  }

  _pintarRoles(seleccion) {
    const filas = ROLES_ELEGIBLES.filter((rol) => this.datos.roles[rol]).map((rol) => {
      const s = el('select', { id: `dm-rol-${rol}`, onchange: () => this._cambioRol() },
        ...this.datos.roles[rol].map((o) => el('option', { value: o.nombre },
          `${o.nombre}${o.gb ? ` · ${gb(o.gb)}` : ''}${o.descargado === false ? ' · ⬇ no descargado' : ''}`)));
      s.value = seleccion[rol];
      return el('label', { class: 'dm-campo' }, el('span', {}, ROLES[rol] || rol), s);
    });
    $('dm-roles').replaceChildren(...filas);
  }

  _nombres() {
    return Object.fromEntries(ROLES_ELEGIBLES.filter((r) => $(`dm-rol-${r}`)).map((r) => [r, $(`dm-rol-${r}`).value]));
  }

  _elegirPerfil(id) {
    if (id !== MANUAL) {
      this.perfilBase = id;
      const p = this.datos.perfiles.find((x) => x.id === id);
      this._pintarRoles(p.modelos);
    }
    this._pintarDescripcion();
    this._estimar();
  }

  _cambioRol() {
    const p = this.datos.perfiles.find((x) => x.id === this.perfilBase);
    const nombres = this._nombres();
    const igual = p && ROLES_ELEGIBLES.every((r) => !nombres[r] || nombres[r] === p.modelos[r]);
    $('dm-perfil').value = igual ? this.perfilBase : MANUAL;
    this._pintarDescripcion();
    this._estimar();
  }

  _cuerpo(forzar) {
    return { perfil: this.perfilBase || MANUAL, modelos: this._nombres(), forzar };
  }

  async _estimar() {
    const n = ++this.pedido;
    try {
      const est = await api('/api/modelos/estimar', this._cuerpo(false));
      if (n === this.pedido) this._pintarEstimacion(est);
    } catch (e) {
      $('dm-mensaje').textContent = `No se pudo estimar: ${e.message}`;
    }
  }

  _pintarEstimacion(est) {
    $('dm-alerta').dataset.nivel = est.nivel;
    $('dm-mensaje').textContent = est.mensaje;
    $('dm-avisos').replaceChildren(...est.avisos.map((a) => el('li', {}, a)));
    // Barra apilada: cada modelo, la reserva del sistema y una marca roja en el total de RAM.
    const escala = Math.max(est.total_gb, est.ram_gb) || 1;
    const partes = est.filas.filter((f) => f.gb).map((f) => ({ rol: f.rol, gb: f.gb, nombre: f.nombre }));
    partes.push({ rol: 'sistema', gb: est.reserva_gb, nombre: 'macOS, navegador y Python' });
    const segs = partes.map((p) => {
      const s = el('div', { class: 'seg', dataset: { rol: p.rol }, title: `${ROLES[p.rol] || 'Sistema'}: ${p.nombre} · ${gb(p.gb)}` });
      s.style.width = `${(100 * p.gb) / escala}%`;
      return s;
    });
    const tope = el('div', { class: 'tope', title: `RAM del equipo: ${est.ram_gb} GB` });
    tope.style.left = `calc(${Math.min(100, (100 * est.ram_gb) / escala)}% - 2px)`;
    $('dm-barra').replaceChildren(...segs, tope);
    const compartidos = est.filas.filter((f) => f.comparte_con)
      .map((f) => el('span', {}, `${ROLES[f.rol]} = ${ROLES[f.comparte_con]} (una sola copia en memoria)`));
    const leyenda = el('div', { class: 'dm-leyenda' }, ...partes.map((p) => {
      const i = el('i', { class: 'seg', dataset: { rol: p.rol } });
      return el('span', {}, i, `${ROLES[p.rol] || 'Sistema'} ${gb(p.gb)}`);
    }), ...compartidos, el('span', {}, el('i', { class: 'tope-ley' }), `RAM del equipo ${est.ram_gb} GB`));
    $('dm-alerta').querySelector('.dm-leyenda')?.remove();
    $('dm-barra').after(leyenda);
  }

  async _aplicar(forzar) {
    const boton = $('dm-aplicar');
    boton.disabled = true;
    $('dm-resultado').textContent = 'Aplicando…';
    try {
      const r = await api('/api/modelos', this._cuerpo(forzar));
      const extra = r.pendiente_reinicio.length ? ` Pendiente: ${r.pendiente_reinicio.join(' · ')}.` : '';
      await this.abrir();
      $('dm-resultado').textContent = `Aplicado (perfil ${r.perfil}, guardado en ${r.archivo}). Ollama está cargando `
        + `los modelos nuevos; antes de la demo corra make warmup SIN_GUION=1.${extra}`;
      this.alAplicar?.(r);
    } catch (e) {
      if (e.detalle?.codigo === 'excede' && !forzar) {
        $('dm-resultado').textContent = '';
        if (window.confirm(`${e.message}\n\n¿Aplicar de todos modos?`)) return this._aplicar(true);
      } else {
        $('dm-resultado').textContent = `No se aplicó: ${e.message}`;
      }
    } finally {
      boton.disabled = false;
    }
    return null;
  }

  evento(ev) {
    // Otro navegador (o `make warmup`) pudo cambiar la selección: se refresca la píldora.
    if (ev.tipo === 'sesion.lista') this.refrescar();
  }
}
