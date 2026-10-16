// Zona 1: conversación con streaming, chips de modelo, herramientas, aprobaciones y citas.
import { el, json, ms, textoConFormato, ROLES } from './util.js';

export class Conversacion {
  constructor(contenedor, { alAprobar }) {
    this.c = contenedor;
    this.alAprobar = alAprobar;
    this.turnos = new Map();
  }

  limpiar() {
    this.c.replaceChildren();
    this.turnos.clear();
  }

  _abajo(fn) {
    const pegado = this.c.scrollHeight - this.c.scrollTop - this.c.clientHeight < 80;
    fn();
    if (pegado) this.c.scrollTop = this.c.scrollHeight;
  }

  _turno(id) {
    let t = this.turnos.get(id);
    if (!t) {
      t = { id, usuario: null, nodo: el('div', { class: 'turno' }), chips: el('div', { class: 'chips' }),
            burbuja: null, texto: '', herr: new Map(), aprob: new Map(), citas: false };
      t.nodo.append(t.chips);
      this.turnos.set(id, t);
    }
    return t;
  }

  _usuario(t, texto, origen) {
    if (t.usuario) return;
    const etiqueta = { voz: '🎙 Transcripción (voz local)', documento: '📄 Documento', texto: null }[origen];
    t.usuario = el('div', { class: 'msg usuario' }, etiqueta && el('span', { class: 'origen' }, etiqueta), texto || '');
    this._abajo(() => this.c.append(t.usuario, t.nodo));
  }

  nota(texto, tipo = '', turnoId = null) {
    const n = el('div', { class: `nota ${tipo}` }, texto);
    const t = turnoId && this.turnos.get(turnoId);
    this._abajo(() => (t ? t.nodo.append(n) : this.c.append(n)));
  }

  evento(ev) {
    const d = ev.datos || {};
    const tid = ev.turno_id;
    switch (ev.tipo) {
      case 'transcripcion':
        this._usuario(this._turno(tid), d.texto, 'voz');
        break;
      case 'turno.inicio': {
        const t = this._turno(tid);
        this._usuario(t, d.entrada === 'documento' && !d.texto ? 'Valide este documento contra la base' : d.texto, d.entrada);
        break;
      }
      case 'modelo.uso': {
        const t = this._turno(tid);
        this._abajo(() => t.chips.append(el('span', { class: 'chip', dataset: { rol: d.rol }, title: d.modelo },
          el('b', {}, ROLES[d.rol] || d.rol), ` · ${d.tarea || ''}`)));
        break;
      }
      case 'llm.delta': {
        const t = this._turno(tid);
        if (!t.burbuja) {
          t.burbuja = el('div', { class: 'msg asistente escribiendo' });
          t.nodo.append(t.burbuja);
        }
        t.texto += d.texto || '';
        this._abajo(() => { t.burbuja.textContent = t.texto; });
        break;
      }
      case 'herramienta.inicio': {
        const t = this._turno(tid);
        const estado = el('span', { class: 'herr-estado' }, d.escritura ? 'esperando aprobación…' : 'ejecutando…');
        const resumen = el('span', { class: 'herr-resumen' }, '');
        const res = el('pre', {}, '…');
        const det = el('details', { class: `herr${d.escritura ? ' escritura' : ''}` },
          el('summary', {}, el('span', {}, '🔧'), el('span', { class: 'herr-nombre' }, d.nombre), resumen, estado),
          el('h4', {}, 'Parámetros'), el('pre', {}, json(d.argumentos)),
          el('h4', {}, 'Resultado (MCP)'), res);
        t.herr.set(d.llamada_id, { det, estado, resumen, res });
        this._abajo(() => t.nodo.insertBefore(det, t.burbuja || null));
        break;
      }
      case 'herramienta.fin': {
        const h = this._turno(tid).herr.get(d.llamada_id);
        if (!h) break;
        h.estado.textContent = `${d.ok ? '✓' : '✗'} ${ms(d.duracion_ms)}`;
        h.estado.className = `herr-estado ${d.ok ? 'ok' : 'fallo'}`;
        h.resumen.textContent = d.resumen || '';
        h.res.textContent = json(d.resultado);
        break;
      }
      case 'aprobacion.solicitada':
        this._aprobacion(this._turno(tid), d);
        break;
      case 'aprobacion.resuelta': {
        const a = this._turno(tid).aprob.get(d.aprobacion_id);
        if (!a) break;
        a.nodo.dataset.estado = d.aprobada ? 'aprobada' : 'rechazada';
        a.botones.remove();
        const motivo = { tiempo_agotado: ' (tiempo agotado)', cancelado: ' (cancelado)' }[d.origen] || '';
        a.nodo.append(el('div', { class: 'resultado-ap' }, d.aprobada ? '✓ Aprobado por la persona' : `✗ Rechazado${motivo}`));
        break;
      }
      case 'citas':
        // Se muestran debajo de la respuesta, cuando llegue respuesta.final.
        this._turno(tid).citasPend = d.citas;
        break;
      case 'documento.extraido':
        this.nota(`📄 ${d.nombre}: ${Object.keys(d.campos || {}).length} campos extraídos (ver visor).`, '', tid);
        break;
      case 'respuesta.final': {
        const t = this._turno(tid);
        if (!t.burbuja) { t.burbuja = el('div', { class: 'msg asistente' }); t.nodo.append(t.burbuja); }
        t.burbuja.classList.remove('escribiendo');
        t.burbuja.replaceChildren(textoConFormato(d.texto || t.texto));
        this._citas(t, d.citas?.length ? d.citas : t.citasPend);
        this._abajo(() => {});
        break;
      }
      case 'aviso':
        this.nota(`⚠ ${d.mensaje}`, 'aviso', tid);
        break;
      case 'error':
        this.nota(`✗ ${d.mensaje}`, 'error', tid);
        break;
      case 'turno.fin': {
        const t = this.turnos.get(tid);
        if (t?.burbuja) t.burbuja.classList.remove('escribiendo');
        if (t?.citasPend) this._citas(t, t.citasPend);
        if (t && d.cancelado) this.nota('Turno detenido.', 'aviso', tid);
        break;
      }
      default:
        break; // tipos desconocidos: se ignoran (compatibilidad hacia adelante)
    }
  }

  _aprobacion(t, d) {
    const decidir = (aprobar) => {
      botones.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      this.alAprobar(d.aprobacion_id, aprobar);
    };
    const botones = el('div', { class: 'botones' },
      el('button', { type: 'button', class: 'aprobar', onclick: () => decidir(true) }, 'Aprobar'),
      el('button', { type: 'button', class: 'rechazar', onclick: () => decidir(false) }, 'Rechazar'));
    const nodo = el('div', { class: 'aprobacion', role: 'group', 'aria-label': 'Aprobación requerida' },
      el('h3', {}, '✋ Requiere su aprobación'),
      el('div', { class: 'mensaje-ap' }, d.mensaje || `Centinela quiere ejecutar ${d.herramienta}.`),
      botones);
    t.aprob.set(d.aprobacion_id, { nodo, botones });
    this._abajo(() => t.nodo.insertBefore(nodo, t.burbuja || null));
    botones.querySelector('.aprobar').focus({ preventScroll: true });
  }

  _citas(t, citas) {
    if (t.citas || !citas?.length) return;
    t.citas = true;
    const det = el('details', { class: 'citas', open: true },
      el('summary', {}, `📚 Fuentes citadas (${citas.length})`),
      citas.map((c) => el('div', { class: 'cita' }, el('b', {}, `${c.documento} · ${c.seccion}`), c.fragmento || '')));
    this._abajo(() => t.nodo.append(det));
  }
}
