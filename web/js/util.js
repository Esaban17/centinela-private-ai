// Utilidades de la UI. Sin dependencias externas.

/** Crea un elemento: el('div', {class: 'x', onclick: fn}, 'texto', otroNodo) */
export function el(tag, attrs = {}, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'class') n.className = v;
    else if (k === 'dataset') Object.assign(n.dataset, v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const h of hijos.flat()) {
    if (h === undefined || h === null || h === false) continue;
    n.append(h instanceof Node ? h : document.createTextNode(String(h)));
  }
  return n;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs = {}, ...hijos) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  for (const h of hijos.flat()) if (h !== undefined && h !== null) n.append(h instanceof Node ? h : document.createTextNode(String(h)));
  return n;
}

const fmtNum = new Intl.NumberFormat('es-GT');
const fmtDin = new Intl.NumberFormat('es-GT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const num = (n) => (n === null || n === undefined || Number.isNaN(n) ? '–' : fmtNum.format(n));

export function dinero(monto, moneda = 'GTQ') {
  if (monto === null || monto === undefined) return '–';
  const s = moneda === 'GTQ' ? 'Q' : moneda === 'USD' ? 'US$' : `${moneda} `;
  return `${s}${fmtDin.format(monto)}`;
}

export function ms(v) {
  if (v === null || v === undefined) return '–';
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
}

/** Texto plano con saltos de línea y viñetas «- » convertido a nodos (sin innerHTML). */
export function textoConFormato(texto) {
  const frag = document.createDocumentFragment();
  let lista = null;
  for (const linea of (texto || '').split('\n')) {
    const m = linea.match(/^\s*[-•]\s+(.*)$/);
    if (m) {
      if (!lista) { lista = el('ul'); frag.append(lista); }
      lista.append(el('li', {}, m[1]));
    } else {
      lista = null;
      if (linea.trim()) frag.append(el('div', {}, linea));
    }
  }
  return frag;
}

export function json(v) {
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

export function almacenar(clave, valor) {
  try {
    if (valor === undefined) return localStorage.getItem(clave);
    if (valor === null) localStorage.removeItem(clave);
    else localStorage.setItem(clave, valor);
  } catch { /* almacenamiento bloqueado: se sigue sin recordar */ }
  return null;
}

export const ROLES = {
  agente: 'Agente', rapido: 'Rápido', vision: 'Visión', embeddings: 'Embeddings', stt: 'Voz → texto', tts: 'Texto → voz',
};

export const ETAPAS = {
  stt: 'Voz → texto', vision: 'Visión', intencion: 'Intención', llm: 'LLM', herramientas: 'Herramientas',
  aprobacion: 'Aprobación', resumen: 'Resumen', tts: 'Texto → voz',
};
