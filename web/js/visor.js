// Zona 2: visor. Documento con campos extraídos, tablas y gráficas según el resultado de cada herramienta.
// Cada turno tiene una página; los resultados del mismo turno se apilan en ella.
import { el, svg, dinero, num } from './util.js';

const normalizar = (t) => (t || '').toString().normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

export class Visor {
  constructor(cuerpo, historial) {
    this.cuerpo = cuerpo;
    this.historial = historial;
    this.paginas = [];          // [{turnoId, titulo, nodo, boton}]
    this.anomalas = new Set();  // ids de transacciones marcadas por detectar_anomalias
    this.docs = new Map();      // turnoId -> {campos, validacion}
  }

  limpiar() {
    this.paginas = [];
    this.anomalas.clear();
    this.docs.clear();
    this.historial.replaceChildren();
    this.cuerpo.replaceChildren(el('div', { class: 'vacio' },
      el('p', {}, 'Aquí aparecen los documentos analizados, las tablas y las gráficas de cada consulta.')));
  }

  _pagina(turnoId, titulo) {
    let p = this.paginas.find((x) => x.turnoId === turnoId);
    if (!p) {
      p = { turnoId, nodo: el('div', { class: 'pagina' }) };
      p.boton = el('button', { type: 'button', onclick: () => this._mostrar(p) }, titulo);
      this.paginas.push(p);
      this.historial.append(p.boton);
      if (this.paginas.length > 8) {
        const vieja = this.paginas.shift();
        vieja.boton.remove();
      }
    }
    this._mostrar(p);
    return p;
  }

  _mostrar(p) {
    this.cuerpo.replaceChildren(p.nodo);
    for (const x of this.paginas) x.boton.setAttribute('aria-current', x === p ? 'true' : 'false');
  }

  _agregar(turnoId, titulo, vista) {
    if (!vista) return;
    const p = this._pagina(turnoId, titulo);
    p.nodo.append(vista);
    vista.scrollIntoView({ block: 'nearest' });
  }

  evento(ev) {
    const d = ev.datos || {};
    if (ev.tipo === 'documento.extraido') {
      this._agregar(ev.turno_id, 'Documento', this._documento(ev.turno_id, d));
    } else if (ev.tipo === 'herramienta.fin' && d.ok && d.resultado) {
      const r = d.resultado;
      const doc = this.docs.get(ev.turno_id);
      if (doc) this._validar(doc, d.nombre, r);
      const vista = this._vista(d.nombre, r);
      if (vista) this._agregar(ev.turno_id, vista.dataset.titulo, vista);
    }
  }

  _vista(nombre, r) {
    switch (nombre) {
      case 'detectar_anomalias': return this._anomalias(r);
      case 'movimientos': return this._movimientos(r);
      case 'resumen_cliente': return this._resumenCliente(r);
      case 'consulta_sql': return r.ok ? this._tablaSQL(r) : null;
      case 'buscar_cliente': return (r.clientes || []).length > 1 ? this._clientes(r) : null;
      case 'crear_ticket': case 'actualizar_ticket': return r.ticket ? this._ticket(r.ticket) : null;
      default: return null;
    }
  }

  // ─── Vistas ───

  _documento(turnoId, d) {
    const campos = d.campos || {};
    const filas = Object.entries(campos).map(([k, v]) =>
      el('tr', { dataset: { campo: k } }, el('td', {}, k.replaceAll('_', ' ')),
        el('td', {}, typeof v === 'number' && /monto|saldo|total/.test(k) ? dinero(v, campos.moneda || 'GTQ') : String(v ?? '–'))));
    const validacion = el('div', { class: 'validacion' });
    const tabla = el('table', { class: 'tabla campos' }, el('tbody', {}, filas));
    this.docs.set(turnoId, { campos, tabla, validacion });
    return el('div', { class: 'vista', dataset: { titulo: 'Documento' } },
      el('h3', {}, `📄 ${d.nombre || 'Documento'}`),
      el('p', { class: 'sub' }, `${(d.tipo_documento || '').replaceAll('_', ' ')} · leído con ${d.modelo || 'modelo de visión'} en ${num(d.duracion_ms)} ms`),
      el('div', { class: 'dos-col' },
        el('img', { class: 'doc-img', src: `/api/documentos/${encodeURIComponent(d.documento_id)}`, alt: `Documento ${d.nombre || ''}` }),
        el('div', {}, el('p', { class: 'etiqueta' }, 'Campos extraídos (JSON validado)'), tabla, validacion,
          d.advertencias?.length ? el('p', { class: 'advertencias' }, `⚠ ${d.advertencias.join(' · ')}`) : null)));
  }

  /** Cruza los campos del documento con lo que devolvió la base (titular y existencia de la cuenta). */
  _validar(doc, nombre, r) {
    const marcar = (campo, ok, texto) => {
      const fila = doc.tabla.querySelector(`tr[data-campo="${campo}"]`);
      if (fila) fila.classList.toggle('anomala', !ok);
      doc.validacion.append(el('p', { class: ok ? 'verde' : 'advertencias' }, `${ok ? '✓' : '✗'} ${texto}`));
    };
    let titularBase = null;
    if (nombre === 'consulta_sql' && r.ok) {
      if (!r.filas?.length && doc.campos.numero_cuenta) return marcar('numero_cuenta', false, 'La cuenta no existe en la base');
      const i = r.columnas.findIndex((c) => /titular|nombre/.test(c));
      if (i >= 0) titularBase = r.filas[0][i];
      if (doc.campos.numero_cuenta) marcar('numero_cuenta', true, 'La cuenta existe en la base');
    } else if (nombre === 'resumen_cliente' && r.cliente) {
      titularBase = r.cliente.nombre;
    }
    if (titularBase && doc.campos.titular) {
      const ok = normalizar(titularBase) === normalizar(doc.campos.titular);
      marcar('titular', ok, ok ? 'El titular coincide' : `El titular no coincide: la base dice «${titularBase}»`);
    }
  }

  _anomalias(r) {
    const h = r.hallazgos || [];
    h.forEach((x) => this.anomalas.add(x.transaccion_id));
    const base = r.linea_base || {};
    const v = el('div', { class: 'vista', dataset: { titulo: 'Anomalías' } },
      el('h3', {}, `🚩 ${r.total_hallazgos ?? h.length} operaciones anómalas · ${r.cliente_id || ''}`),
      el('p', { class: 'sub' }, `Línea base: ${base.operaciones ?? '–'} operaciones, promedio ${dinero(base.promedio_gtq)} · ciudades habituales: ${(base.ciudades || []).join(', ') || '–'}`));
    if (h.length) {
      v.append(barras(h.map((x) => ({ etiqueta: x.fecha_hora.slice(5, 16), valor: x.monto, anomala: true })), base.promedio_gtq));
      v.append(el('table', { class: 'tabla' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Fecha'), el('th', {}, 'Monto'), el('th', {}, 'Comercio · ubicación'))),
        el('tbody', {}, h.map((x) => el('tr', { class: 'anomala' },
          el('td', {}, x.fecha_hora.slice(0, 16)),
          el('td', { class: 'num' }, dinero(x.monto, x.moneda)),
          el('td', {}, `${x.comercio} · ${x.ubicacion}`, el('ul', { class: 'motivos' }, (x.motivos || []).map((m) => el('li', {}, m)))))))));
    }
    return v;
  }

  _movimientos(r) {
    if (!r.encontrada) return null;
    const movs = r.movimientos || [];
    const cron = [...movs].reverse();
    const v = el('div', { class: 'vista', dataset: { titulo: 'Movimientos' } },
      el('h3', {}, `Movimientos · ${r.cuenta?.id || ''}`),
      el('p', { class: 'sub' }, `${r.desde} a ${r.hasta} · ${r.devueltos} de ${r.total}${r.truncado ? ' (truncado)' : ''} · en rojo: anomalías detectadas`));
    if (cron.length) v.append(barras(cron.map((m) => ({ etiqueta: m.fecha_hora.slice(5, 10), valor: m.monto, anomala: this.anomalas.has(m.id) }))));
    v.append(el('table', { class: 'tabla' },
      el('thead', {}, el('tr', {}, ['Fecha', 'Tipo', 'Monto', 'Canal', 'Comercio · ubicación'].map((c) => el('th', {}, c)))),
      el('tbody', {}, movs.map((m) => el('tr', { class: this.anomalas.has(m.id) ? 'anomala' : '' },
        el('td', {}, m.fecha_hora.slice(0, 16)), el('td', {}, m.tipo.replaceAll('_', ' ')),
        el('td', { class: 'num' }, dinero(m.monto, r.cuenta?.moneda)), el('td', {}, m.canal),
        el('td', {}, `${m.comercio || ''} · ${m.ubicacion || ''}`))))));
    return v;
  }

  _resumenCliente(r) {
    if (!r.encontrado) return null;
    const c = r.cliente;
    const act = r.actividad_30_dias || {};
    return el('div', { class: 'vista', dataset: { titulo: 'Cliente' } },
      el('h3', {}, `👤 ${c.nombre}`),
      el('p', { class: 'sub' }, `${c.id} · segmento ${c.segmento} · ${c.departamento} · riesgo KYC ${c.riesgo_kyc} · ejecutivo(a) ${c.ejecutivo}`),
      el('div', { class: 'productos' }, (r.productos || []).map((p) => el('div', { class: 'producto' },
        el('div', { class: 'tipo' }, `${p.tipo} · ${p.estado}`), el('div', { class: 'saldo' }, dinero(p.saldo, p.moneda)),
        el('div', { class: 'id' }, p.id)))),
      el('table', { class: 'tabla campos' }, el('tbody', {},
        el('tr', {}, el('td', {}, 'Operaciones (30 días)'), el('td', {}, num(act.operaciones))),
        el('tr', {}, el('td', {}, 'Monto movido (30 días)'), el('td', {}, dinero(act.monto_total))),
        el('tr', {}, el('td', {}, 'Tickets abiertos'), el('td', {}, num((r.tickets_abiertos || []).length))))));
  }

  _tablaSQL(r) {
    return el('div', { class: 'vista', dataset: { titulo: 'Consulta SQL' } },
      el('h3', {}, `Consulta de solo lectura · ${r.filas_devueltas} filas`),
      el('table', { class: 'tabla' },
        el('thead', {}, el('tr', {}, r.columnas.map((c) => el('th', {}, c)))),
        el('tbody', {}, r.filas.map((f) => el('tr', {}, f.map((x) => el('td', { class: typeof x === 'number' ? 'num' : '' },
          typeof x === 'number' ? num(x) : String(x ?? '–'))))))));
  }

  _clientes(r) {
    return el('div', { class: 'vista', dataset: { titulo: 'Clientes' } },
      el('h3', {}, `${r.coincidencias} clientes encontrados`),
      el('table', { class: 'tabla' },
        el('thead', {}, el('tr', {}, ['Id', 'Nombre', 'Segmento', 'Departamento'].map((c) => el('th', {}, c)))),
        el('tbody', {}, r.clientes.map((c) => el('tr', {}, el('td', {}, c.id), el('td', {}, c.nombre), el('td', {}, c.segmento), el('td', {}, c.departamento))))));
  }

  _ticket(t) {
    return el('div', { class: 'vista', dataset: { titulo: 'Ticket' } },
      el('h3', {}, `🎫 Ticket #${t.id} · ${t.estado}`),
      el('table', { class: 'tabla campos' }, el('tbody', {},
        ['tipo', 'prioridad', 'titulo', 'detalle', 'cliente_id', 'creado_por', 'fecha'].map((k) =>
          el('tr', {}, el('td', {}, k.replace('_', ' ')), el('td', {}, String(t[k] ?? '–')))))),
      el('p', { class: 'sub' }, 'Aprobado por la persona y registrado en la tabla de auditoría.'));
  }
}

/** Gráfica de barras en SVG (sin librerías). `base`: línea de referencia (p. ej. promedio). */
function barras(datos, base = null) {
  const W = 560, H = 220, M = { i: 62, d: 10, s: 16, b: 34 };
  const max = Math.max(...datos.map((d) => d.valor), base || 0) * 1.08 || 1;
  const ancho = (W - M.i - M.d) / datos.length;
  const y = (v) => M.s + (H - M.s - M.b) * (1 - v / max);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'grafica', role: 'img', 'aria-label': 'Gráfica de montos' });
  g.append(svg('line', { x1: M.i, x2: W - M.d, y1: H - M.b, y2: H - M.b, class: 'eje' }));
  for (const f of [0.5, 1]) {
    const v = max * f;
    g.append(svg('text', { x: M.i - 6, y: y(v) + 4, 'text-anchor': 'end' }, `Q${Math.round(v).toLocaleString('es-GT')}`));
  }
  const cada = Math.ceil(datos.length / 8);
  datos.forEach((d, i) => {
    const x = M.i + i * ancho;
    const r = svg('rect', { x: x + ancho * 0.15, width: Math.max(ancho * 0.7, 1), y: y(d.valor), height: H - M.b - y(d.valor), rx: 2,
      class: d.anomala ? 'barra-anomala' : 'barra-normal' });
    r.append(svg('title', {}, `${d.etiqueta}: Q${d.valor.toLocaleString('es-GT')}`));
    g.append(r);
    if (i % cada === 0) g.append(svg('text', { x: x + ancho / 2, y: H - M.b + 16, 'text-anchor': 'middle' }, d.etiqueta));
  });
  if (base) {
    g.append(svg('line', { x1: M.i, x2: W - M.d, y1: y(base), y2: y(base), class: 'linea-base' }));
    g.append(svg('text', { x: M.i + 4, y: y(base) - 5, 'text-anchor': 'start' }, `promedio Q${Math.round(base).toLocaleString('es-GT')}`));
  }
  return g;
}
