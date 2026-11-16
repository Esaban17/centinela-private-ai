// Zona 2 · pestaña «Flujo»: diagrama de arquitectura en vivo.
// Muestra los modelos, los servidores MCP y las guardias que hay en este despliegue (sesion.lista) y, con los eventos
// de cada turno, cuáles están trabajando ahora, por dónde viaja la consulta y qué corre en paralelo (línea de tiempo).
// Solo usa eventos del contrato (docs/CONTRATO_EVENTOS.md): modelo.uso, llm.*, herramienta.*, aprobacion.*, voz.audio,
// respuesta.final, guardia y turno.*; y la instantánea de /api/soberania para la guardia de salida.
import { el, svg, ms, num, ROLES } from './util.js';

const W = 560;
const COL = { L: { x: 12, w: 150 }, C: { x: 174, w: 200 }, R: { x: 386, w: 162 } };
const FILA = [34, 94, 154];
const H_NODO = 50;
const ROLES_MODELO = ['agente', 'rapido', 'vision', 'embeddings'];

const recortar = (t, max) => (t && t.length > max ? `${t.slice(0, max - 1)}…` : t || '');

export class Flujo {
  constructor(contenedor) {
    this.cont = contenedor;
    this.cfg = null;
    this.soberania = null;
    this.limpiar();
  }

  // ───────────── Arquitectura (sesion.lista) ─────────────

  configurar(d) {
    this.cfg = d;
    const pais = d.proveedor === 'pais';
    const modelos = d.modelos || {};
    const nodos = {};
    const aristas = {};
    const nodo = (id, o) => { nodos[id] = { id, ...o }; };
    const voz = Boolean(d.voz_disponible || modelos.stt);

    // Este equipo: entrada, orquestador, salida y guardias.
    nodo('usuario', { col: 'L', fila: 0, icono: '👤', titulo: 'Ejecutivo', sub: 'pregunta', tipo: 'usuario' });
    if (voz) nodo('stt', { col: 'L', fila: 1, icono: '🎙', titulo: 'Voz → texto', sub: modelos.stt || 'Whisper', tipo: 'stt' });
    nodo('aprobacion', { col: 'L', fila: 2, icono: '✋', titulo: 'Aprobación humana', sub: 'antes de escribir', tipo: 'guardia' });
    nodo('orq', { x: COL.C.x, y: FILA[0], w: COL.C.w, h: FILA[2] + H_NODO - FILA[0], icono: '🧭', titulo: 'Orquestador',
      sub: 'Centinela · loop del agente', tipo: 'orq' });
    nodo('resp', { col: 'R', fila: 0, icono: '💬', titulo: 'Respuesta', sub: 'al ejecutivo', tipo: 'usuario' });
    if (voz) nodo('tts', { col: 'R', fila: 1, icono: '🔊', titulo: 'Texto → voz', sub: modelos.tts || 'Kokoro', tipo: 'tts' });
    nodo('verificacion', { col: 'R', fila: 2, icono: '🔎', titulo: 'Verificación',
      sub: pais ? 'dato real, no «meta»' : 'citas y dictamen', tipo: 'guardia' });

    // Servidores MCP locales (fila 4) y auditoría.
    const servidores = (d.mcp_servidores && d.mcp_servidores.length) ? d.mcp_servidores
      : [{ nombre: 'banco', ubicacion: 'local', herramientas: (d.herramientas || []).map((h) => h.nombre) }];
    this.servidorDe = {};
    this.desconectados = servidores.filter((s) => s.conectado === false).map((s) => `mcp:${s.nombre}`);
    const locales = servidores.filter((s) => s.ubicacion !== 'remoto');
    const remotos = servidores.filter((s) => s.ubicacion === 'remoto');
    const yLocal = FILA[2] + H_NODO + 14;
    const anchoLocal = locales.length ? Math.min(COL.L.w + COL.C.w / 2, (COL.C.x + COL.C.w / 2 - 22 - COL.L.x - (locales.length - 1) * 10) / locales.length) : 0;
    locales.forEach((s, i) => {
      const id = `mcp:${s.nombre}`;
      nodo(id, { x: COL.L.x + i * (anchoLocal + 10), y: yLocal, w: anchoLocal, h: H_NODO, icono: '🔧',
        titulo: `MCP ${s.nombre}`, sub: s.herramientas.length ? `${s.herramientas.length} herramienta${s.herramientas.length === 1 ? '' : 's'} · local` : 'local', tipo: 'mcp', servidor: s });
      for (const h of s.herramientas) this.servidorDe[h] = id;
    });
    nodo('auditoria', { x: COL.C.x + COL.C.w / 2 + 22, y: yLocal, w: W - 12 - (COL.C.x + COL.C.w / 2 + 22), h: H_NODO,
      icono: '📝', titulo: 'Auditoría', sub: 'cada llamada, enmascarada', tipo: 'guardia' });

    // Frontera: guardia de salida.
    const yFrontera = yLocal + H_NODO + 30;
    this.yFrontera = yFrontera;
    nodo('salida', { x: W / 2 - 110, y: yFrontera - 18, w: 220, h: 36, icono: '🛡', titulo: 'Guardia de salida',
      sub: '', tipo: 'guardia', compacto: true });

    // Zona de modelos: PAIS (datacenter) u Ollama (este equipo).
    const porNombre = new Map();
    for (const rol of ROLES_MODELO) {
      const n = modelos[rol];
      if (!n || (rol === 'vision' && d.vision_disponible === false)) continue;
      if (!porNombre.has(n)) porNombre.set(n, []);
      porNombre.get(n).push(rol);
    }
    this.nodoDeModelo = {};
    this.nodoDeRol = {};
    const filaA = [];
    for (const [n, roles] of porNombre) {
      const id = `modelo:${n}`;
      filaA.push({ id, icono: roles.includes('embeddings') ? '🔢' : '🧠', titulo: n,
        sub: roles.map((r) => ROLES[r] || r).join(' · '), tipo: roles.includes('embeddings') ? 'embeddings' : 'modelo' });
      this.nodoDeModelo[n] = id;
      for (const r of roles) this.nodoDeRol[r] = id;
    }
    if ((d.modos || []).includes('pais')) {
      filaA.push({ id: 'agente-pais', icono: '🤖', titulo: d.agente_pais || 'agente PAIS', sub: 'Agent Builder · MCP + RAG', tipo: 'agente' });
    }
    const filaB = remotos.map((s) => {
      const id = `mcp:${s.nombre}`;
      for (const h of s.herramientas) this.servidorDe[h] = id;
      return { id, icono: '🗄', titulo: `MCP ${s.nombre}`, sub: `${s.herramientas.length} herramientas · solo lectura`, tipo: 'mcp', servidor: s };
    });
    const yA = yFrontera + 46;
    const repartir = (lista, y) => {
      const n = lista.length;
      if (!n) return;
      const w = Math.min(200, (W - 24 - (n - 1) * 10) / n);
      const x0 = (W - (n * w + (n - 1) * 10)) / 2;
      lista.forEach((o, i) => nodo(o.id, { ...o, x: x0 + i * (w + 10), y, w, h: H_NODO }));
    };
    repartir(filaA, yA);
    repartir(filaB, yA + H_NODO + 14);
    // Si una herramienta no aparece en ningún servidor (p. ej. el simulador), se atribuye al primero.
    this.mcpPorDefecto = servidores.length ? `mcp:${servidores[0].nombre}` : null;
    this.alto = yA + (filaB.length ? 2 * H_NODO + 14 : H_NODO) + 14;
    this.zonaRemota = pais ? 'Datacenter · PAIS' : 'Ollama · este equipo';

    // Posiciones de columna → coordenadas.
    for (const n of Object.values(nodos)) {
      if (n.col) Object.assign(n, { x: COL[n.col].x, y: FILA[n.fila], w: COL[n.col].w, h: H_NODO });
    }
    this.nodos = nodos;

    // Aristas.
    const a = (id, de, ladoDe, a2, ladoA, o = {}) => { if (nodos[de] && nodos[a2]) aristas[id] = { id, de, ladoDe, a: a2, ladoA, ...o }; };
    a('usuario-orq', 'usuario', 'der', 'orq', 'izq', { dy: -30 });
    a('usuario-stt', 'usuario', 'abajo', 'stt', 'arriba');
    a('stt-orq', 'stt', 'der', 'orq', 'izq');
    a('orq-aprobacion', 'orq', 'izq', 'aprobacion', 'der', { dyDe: 50 });
    a('orq-resp', 'orq', 'der', 'resp', 'izq', { dyDe: -30 });
    a('resp-tts', 'resp', 'abajo', 'tts', 'arriba');
    a('orq-verificacion', 'orq', 'der', 'verificacion', 'izq', { dyDe: 50 });
    a('orq-auditoria', 'orq', 'abajo', 'auditoria', 'arriba', { dxDe: 40 });
    for (const s of locales) a(`orq-mcp:${s.nombre}`, 'orq', 'abajo', `mcp:${s.nombre}`, 'arriba', { dxDe: -40 });
    a('orq-salida', 'orq', 'abajo', 'salida', 'arriba');
    for (const o of [...filaA, ...filaB]) a(`salida-${o.id}`, 'salida', 'abajo', o.id, 'arriba');
    const emb = this.nodoDeRol.embeddings;
    for (const s of locales) {
      if (emb && s.herramientas.includes('buscar_politicas')) a(`mcp:${s.nombre}-salida`, `mcp:${s.nombre}`, 'abajo', 'salida', 'izq');
    }
    if (nodos['agente-pais']) {
      const llm = this.nodoDeRol.agente;
      if (llm) a('agente-pais-modelo', 'agente-pais', 'abajo', llm, 'abajo', { curva: 'u' });
      for (const o of filaB) a(`agente-pais-${o.id}`, 'agente-pais', 'abajo', o.id, 'arriba', { interna: true });
    }
    this.aristas = aristas;
    this._dibujar();
  }

  // ───────────── Dibujo ─────────────

  _punto(n, lado, dx = 0, dy = 0) {
    const cx = n.x + n.w / 2 + dx;
    const cy = n.y + n.h / 2 + dy;
    return { izq: [n.x, cy], der: [n.x + n.w, cy], arriba: [cx, n.y], abajo: [cx, n.y + n.h] }[lado];
  }

  _camino(e) {
    const de = this.nodos[e.de];
    const a = this.nodos[e.a];
    const [x1, y1] = this._punto(de, e.ladoDe, e.dxDe || 0, e.dyDe ?? e.dy ?? 0);
    const [x2, y2] = this._punto(a, e.ladoA, e.dxA || 0, e.ladoA === 'izq' || e.ladoA === 'der' ? (e.dyA ?? e.dy ?? 0) : 0);
    if (e.curva === 'u') {
      const y = Math.max(y1, y2) + 18;
      return `M${x1},${y1} C${x1},${y} ${x2},${y} ${x2},${y2}`;
    }
    const horizontal = e.ladoDe === 'izq' || e.ladoDe === 'der';
    if (horizontal && (e.ladoA === 'izq' || e.ladoA === 'der')) {
      const mx = (x1 + x2) / 2;
      return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    }
    if (!horizontal && (e.ladoA === 'izq' || e.ladoA === 'der')) {
      return `M${x1},${y1} C${x1},${y2} ${x1},${y2} ${x2},${y2}`;
    }
    const my = (y1 + y2) / 2;
    return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
  }

  _dibujar() {
    const raiz = svg('svg', { viewBox: `0 0 ${W} ${this.alto}`, class: 'flujo-svg', role: 'img',
      'aria-label': 'Diagrama de arquitectura: modelos, herramientas y guardias de Centinela' });
    const defs = svg('defs', {}, svg('marker', { id: 'flecha', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
      svg('path', { d: 'M0,0 L10,5 L0,10 z', class: 'flecha' })));
    raiz.append(defs);
    // Zonas
    raiz.append(
      svg('rect', { x: 2, y: 2, width: W - 4, height: this.yFrontera - 4, rx: 14, class: 'zona-fondo local' }),
      svg('text', { x: 14, y: 21, class: 'zona-etq' }, 'Este equipo · Centinela'),
      svg('rect', { x: 2, y: this.yFrontera + 2, width: W - 4, height: this.alto - this.yFrontera - 4, rx: 14, class: 'zona-fondo remota' }),
      svg('text', { x: 14, y: this.yFrontera + 17, class: 'zona-etq' }, this.zonaRemota),
      svg('line', { x1: 8, x2: W - 8, y1: this.yFrontera, y2: this.yFrontera, class: 'frontera' }),
    );
    this.svgAristas = {};
    const capaA = svg('g', {});
    for (const e of Object.values(this.aristas)) {
      const p = svg('path', { d: this._camino(e), class: `arista${e.interna ? ' interna' : ''}`, 'marker-end': 'url(#flecha)' });
      this.svgAristas[e.id] = p;
      capaA.append(p);
    }
    raiz.append(capaA);
    this.svgNodos = {};
    for (const n of Object.values(this.nodos)) {
      const g = svg('g', { class: `nodo t-${n.tipo}`, transform: `translate(${n.x},${n.y})` });
      g.append(svg('rect', { width: n.w, height: n.h, rx: 10, class: 'caja' }));
      const max = Math.floor((n.w - 16) / 7.2);
      if (n.compacto) {
        g.append(svg('text', { x: 12, y: n.h / 2 + 5, class: 'n-titulo' }, `${n.icono} ${n.titulo}`));
        n.svgSub = svg('text', { x: n.w - 10, y: n.h / 2 + 4, class: 'n-metrica der' }, '');
        g.append(n.svgSub);
      } else {
        g.append(svg('text', { x: 10, y: 19, class: 'n-titulo' }, recortar(`${n.icono} ${n.titulo}`, max)));
        n.svgSub = svg('text', { x: 10, y: 34, class: 'n-sub' }, recortar(n.sub, max + 4));
        n.svgMet = svg('text', { x: 10, y: n.h - 5, class: 'n-metrica' }, '');
        g.append(n.svgSub, n.svgMet);
        if (n.id === 'orq') {
          n.svgMet.setAttribute('y', 56);
          n.svgPasos = svg('g', { transform: 'translate(10,72)' });
          g.append(n.svgPasos);
        }
      }
      g.append(svg('title', {}, `${n.titulo} · ${n.sub}`));
      this.svgNodos[n.id] = g;
      raiz.append(g);
    }
    this.svg = raiz;
    this.leyenda = el('div', { class: 'flujo-leyenda' },
      el('span', { class: 'ley activo' }, 'trabajando'), el('span', { class: 'ley hecho' }, 'respondió'),
      el('span', { class: 'ley espera' }, 'esperando / corrigió'), el('span', { class: 'ley error' }, 'error'));
    this.resumen = el('div', { class: 'flujo-resumen' }, 'Haga una pregunta: aquí verá qué modelos, herramientas y guardias participan.');
    this.lineaTiempo = el('div', { class: 'linea-tiempo' });
    this.cont.replaceChildren(el('div', { class: 'flujo' }, el('div', { class: 'flujo-cabeza' }, this.resumen, this.leyenda),
      this.svg, el('p', { class: 'lt-titulo' }, 'Línea de tiempo del turno · lo que se superpone corre en paralelo'), this.lineaTiempo));
    this._pintarSalida();
    this._reiniciarTurno(false);
  }

  // ───────────── Estado del turno ─────────────

  limpiar() {
    this.turnoId = null;
    this.cuentaNodos = {};
    this.cuentaAristas = {};
    this.tramos = [];
    this.carriles = [];
    if (this.cfg) this._dibujar();
  }

  _reiniciarTurno(conTurno = true) {
    this.cuentaNodos = {};
    this.cuentaAristas = {};
    this.tramos = [];
    this.carriles = [];
    this.activosLlm = {};
    this.llamadas = {};
    this.stats = { modelos: 0, herramientas: 0, guardias: 0 };
    this.t0 = null;
    this.fin = null;
    this.respondiendo = false;
    for (const g of Object.values(this.svgNodos || {})) delete g.dataset.estado;
    for (const p of Object.values(this.svgAristas || {})) p.removeAttribute('data-estado');
    for (const n of Object.values(this.nodos || {})) {
      if (n.svgMet) n.svgMet.textContent = '';
      if (n.svgSub && !n.compacto) n.svgSub.textContent = recortar(n.sub, Math.floor((n.w - 16) / 7.2) + 4);
    }
    if (this.nodos?.orq?.svgPasos) this.nodos.orq.svgPasos.replaceChildren();
    // Un servidor MCP que no conectó queda en rojo: sin él el modelo no tiene esas herramientas.
    for (const id of this.desconectados || []) {
      this._estado(id, 'error');
      this._metrica(id, 'sin conexión');
    }
    if (!conTurno && this.lineaTiempo) this.lineaTiempo.replaceChildren(el('p', { class: 'tenue' }, 'Sin turnos todavía.'));
    this._pintarSalida();
  }

  _estado(id, estado) {
    const g = this.svgNodos?.[id];
    if (g) g.dataset.estado = estado;
  }

  _activar(id, delta = 1) {
    if (!this.nodos?.[id]) return;
    const c = (this.cuentaNodos[id] || 0) + delta;
    this.cuentaNodos[id] = Math.max(0, c);
    this._estado(id, c > 0 ? 'activo' : 'hecho');
  }

  _arista(id, delta = 1) {
    const p = this.svgAristas?.[id];
    if (!p) return;
    const c = Math.max(0, (this.cuentaAristas[id] || 0) + delta);
    this.cuentaAristas[id] = c;
    p.dataset.estado = c > 0 ? 'fluye' : 'usada';
  }

  /** Camino del orquestador a un nodo: pasa por la guardia de salida si el nodo está del otro lado. */
  _ruta(id) {
    if (this.svgAristas[`orq-${id}`]) return [`orq-${id}`];
    if (this.svgAristas[`salida-${id}`]) return ['orq-salida', `salida-${id}`];
    return [];
  }

  _enviar(id, delta = 1) {
    const ruta = this._ruta(id);
    for (const a of ruta) this._arista(a, delta);
    if (ruta.includes('orq-salida')) this._activar('salida', delta);
    this._activar(id, delta);
  }

  _metrica(id, texto) {
    const n = this.nodos?.[id];
    if (n?.svgMet) n.svgMet.textContent = recortar(texto, Math.floor((n.w - 16) / 6.4));
  }

  _sub(id, texto) {
    const n = this.nodos?.[id];
    if (n?.svgSub && !n.compacto) n.svgSub.textContent = recortar(texto, Math.floor((n.w - 16) / 7.2) + 4);
  }

  _paso(texto, tipo) {
    const g = this.nodos?.orq?.svgPasos;
    if (!g) return;
    const i = g.childNodes.length;
    if (i >= 6) g.firstChild.remove();
    [...g.childNodes].forEach((t, j) => t.setAttribute('y', j * 15));
    g.append(svg('text', { x: 0, y: Math.min(i, 5) * 15, class: `n-paso t-${tipo}` }, recortar(texto, 30)));
  }

  _nodoModelo(d) {
    if (d.modelo && String(d.modelo).includes('(PAIS)') && this.nodos['agente-pais']) return 'agente-pais';
    return this.nodoDeModelo?.[d.modelo] || this.nodoDeRol?.[d.rol] || (d.rol === 'stt' ? 'stt' : d.rol === 'tts' ? 'tts' : null);
  }

  // ───────────── Línea de tiempo ─────────────

  _ts(ev) {
    const t = Date.parse(ev.ts);
    return Number.isFinite(t) ? t : Date.now();
  }

  _abrir(clave, nodoId, etiqueta, tipo, t) {
    if (!this.carriles.includes(nodoId)) this.carriles.push(nodoId);
    this.tramos.push({ clave, carril: nodoId, etiqueta, tipo, t0: t, t1: null });
  }

  _cerrar(clave, t) {
    const tr = this.tramos.find((x) => x.clave === clave && x.t1 === null);
    if (tr) tr.t1 = Math.max(t, tr.t0 + 80);
    return tr;
  }

  _pintarTiempo() {
    if (!this.t0) return;
    const ahora = this.fin ?? (Date.now() + (this.desfase || 0));
    const total = Math.max(1000, ahora - this.t0);
    const filas = this.carriles.map((id) => {
      const n = this.nodos[id];
      const pista = el('div', { class: 'lt-pista' });
      for (const tr of this.tramos.filter((x) => x.carril === id)) {
        const fin = tr.t1 ?? ahora;
        const izq = ((tr.t0 - this.t0) / total) * 100;
        const ancho = Math.max(0.8, ((fin - tr.t0) / total) * 100);
        // CSSOM y no el atributo style: la CSP de la página no permite estilos en línea.
        const barra = el('div', { class: `lt-barra t-${tr.tipo}${tr.t1 === null ? ' viva' : ''}`, title: `${tr.etiqueta} · ${ms(fin - tr.t0)}` },
          ancho > 14 ? `${tr.etiqueta} · ${ms(fin - tr.t0)}` : '');
        barra.style.left = `${izq}%`;
        barra.style.width = `${ancho}%`;
        pista.append(barra);
      }
      return el('div', { class: 'lt-fila' }, el('span', { class: 'lt-carril' }, n ? `${n.icono} ${n.titulo}` : id), pista);
    });
    const eje = el('div', { class: 'lt-fila lt-eje' }, el('span', { class: 'lt-carril' }, ''),
      el('div', { class: 'lt-pista' }, ...[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const m = el('span', { class: 'lt-marca' }, ms(total * f));
        m.style.left = `${f * 100}%`;
        return m;
      })));
    this.lineaTiempo.replaceChildren(...filas, eje);
  }

  _animar() {
    if (this.animando) return;
    this.animando = true;
    const paso = () => {
      this._pintarTiempo();
      if (this.fin === null && this.t0) requestAnimationFrame(paso);
      else this.animando = false;
    };
    requestAnimationFrame(paso);
  }

  _pintarResumen(extra = '') {
    const s = this.stats;
    const dur = this.fin && this.t0 ? ` · ${ms(this.fin - this.t0)}` : '';
    this.resumen.textContent = `${this.fin ? 'Turno terminado' : 'Turno en curso'} · ${s.modelos} llamada${s.modelos === 1 ? '' : 's'} a modelos · `
      + `${s.herramientas} herramienta${s.herramientas === 1 ? '' : 's'}${s.guardias ? ` · ${s.guardias} guardia${s.guardias === 1 ? '' : 's'} actuó` : ''}${dur}${extra}`;
  }

  // ───────────── Guardia de salida (instantánea de /api/soberania) ─────────────

  actualizarSoberania(s) {
    this.soberania = s;
    this._pintarSalida();
  }

  _pintarSalida() {
    const n = this.nodos?.salida;
    if (!n?.svgSub) return;
    const c = this.soberania?.conexiones;
    const pais = this.cfg?.proveedor === 'pais';
    if (!c) { n.svgSub.textContent = pais ? 'solo datacenter' : 'nada sale'; return; }
    const bloqueadas = (c.bloqueadas || []).length;
    n.svgSub.textContent = `${c.salientes} fuera${bloqueadas ? ` · ${bloqueadas} bloq.` : ''}`;
    if (c.salientes > 0) this._estado('salida', 'error');
  }

  // ───────────── Eventos ─────────────

  evento(ev) {
    const d = ev.datos || {};
    if (ev.tipo === 'sesion.lista') { this.configurar(d); return; }
    if (!this.nodos) return;
    if (ev.turno_id && ev.turno_id !== this.turnoId && ['transcripcion', 'turno.inicio'].includes(ev.tipo)) {
      this.turnoId = ev.turno_id;
      this._reiniciarTurno();
      this.t0 = this._ts(ev);
      this.desfase = this.t0 - Date.now();
      this._animar();
    }
    if (ev.turno_id && ev.turno_id !== this.turnoId) return;
    const t = this._ts(ev);
    switch (ev.tipo) {
      case 'transcripcion':
        this._estado('usuario', 'hecho');
        this._arista('usuario-stt', 1); this._arista('usuario-stt', -1);
        this._arista('stt-orq', 1); this._arista('stt-orq', -1);
        this._estado('stt', 'hecho');
        this._metrica('stt', `${ms(d.duracion_ms)} · ${d.idioma || ''}`);
        this.tramos.push({ clave: 'stt', carril: 'stt', etiqueta: 'transcribir', tipo: 'stt', t0: t - (d.duracion_ms || 0), t1: t });
        if (!this.carriles.includes('stt')) this.carriles.unshift('stt');
        break;
      case 'turno.inicio':
        this._estado('usuario', 'hecho');
        if (d.entrada !== 'voz') { this._arista('usuario-orq', 1); this._arista('usuario-orq', -1); }
        this._activar('orq', 1);
        this._sub('orq', recortar(`«${d.texto || 'documento'}»`, 40));
        this._abrir('orq', 'orq', 'turno', 'orq', t);
        this._paso('recibe la pregunta', 'orq');
        this._pintarResumen();
        break;
      case 'modelo.uso': {
        // Normalmente llega junto con llm.inicio; si no (simulador, o un uso sin streaming), igual se marca el modelo.
        const id = ['rapido', 'vision', 'embeddings'].includes(d.rol) ? this._nodoModelo(d) : null;
        if (id && !this.cuentaNodos[id]) {
          this._estado(id, 'hecho');
          for (const a of this._ruta(id)) if (!this.cuentaAristas[a]) this.svgAristas[a].dataset.estado = 'usada';
          if (d.tarea) this._sub(id, `${ROLES[d.rol] || d.rol} · ${d.tarea}`);
        }
        if (d.rol === 'tts') {
          this._arista('resp-tts', 1);
          this._activar('tts', 1);
          this._abrir('tts', 'tts', 'sintetizar', 'tts', t);
        }
        break;
      }
      case 'llm.inicio': {
        const id = this._nodoModelo(d);
        if (!id) break;
        this.activosLlm[d.paso] = id;
        this.stats.modelos += 1;
        this._enviar(id, 1);
        if (id === 'agente-pais') {
          // El agente de PAIS razona con el mismo modelo del datacenter: se enciende también.
          if (this.nodoDeRol.agente) { this._activar(this.nodoDeRol.agente, 1); this._sub(this.nodoDeRol.agente, `usado por ${this.cfg.agente_pais || 'el agente'}`); }
          this._arista('agente-pais-modelo', 1);
          for (const k of Object.keys(this.svgAristas)) if (k.startsWith('agente-pais-mcp:')) this._arista(k, 1);
        }
        this._sub(id, `${ROLES[d.rol] || d.rol}${d.iteracion ? ` · vuelta ${d.iteracion}` : ''}`);
        this._abrir(`llm-${d.paso}`, id, `${ROLES[d.rol] || d.rol} · vuelta ${d.iteracion || 1}`, id === 'agente-pais' ? 'agente' : (d.rol === 'embeddings' ? 'embeddings' : 'modelo'), t);
        this._paso(`→ ${recortar(String(d.modelo).replace(' (PAIS)', ''), 18)}${id === 'agente-pais' ? ' (PAIS)' : ` (${ROLES[d.rol] || d.rol})`}`, 'modelo');
        if (this.cfg.proveedor === 'pais' && d.iteracion > 1 && this.nodos.verificacion) {
          this._arista('orq-verificacion', 1); this._arista('orq-verificacion', -1);
          this._estado('verificacion', 'hecho');
          this._metrica('verificacion', 'pidió los datos concretos');
        }
        this._pintarResumen();
        break;
      }
      case 'llm.delta':
        if (!this.respondiendo) {
          this.respondiendo = true;
          this._arista('orq-resp', 1);
          this._activar('resp', 1);
          this._abrir('resp', 'resp', 'escribiendo', 'usuario', t);
        }
        break;
      case 'llm.fin': {
        const id = this.activosLlm[d.paso] || this._nodoModelo(d);
        if (!id) break;
        delete this.activosLlm[d.paso];
        this._enviar(id, -1);
        if (id === 'agente-pais') {
          if (this.nodoDeRol.agente) this._activar(this.nodoDeRol.agente, -1);
          this._arista('agente-pais-modelo', -1);
          for (const k of Object.keys(this.svgAristas)) if (k.startsWith('agente-pais-mcp:')) this._arista(k, -1);
        }
        const tps = d.tokens_por_s ? ` · ${num(d.tokens_por_s)} tok/s` : '';
        this._metrica(id, `${ms(d.duracion_ms)} · ${num(d.tokens_entrada)}→${num(d.tokens_salida)} tok${tps}`);
        this._cerrar(`llm-${d.paso}`, t);
        if (d.herramientas_pedidas?.length) this._paso(`pide ${d.herramientas_pedidas.join(', ')}`, 'mcp');
        break;
      }
      case 'herramienta.inicio': {
        const id = this.servidorDe?.[d.nombre] || this.mcpPorDefecto;
        this.stats.herramientas += 1;
        this.llamadas[d.llamada_id] = { id, nombre: d.nombre };
        if (id) {
          this._enviar(id, 1);
          this._sub(id, `▶ ${d.nombre}`);
          this._abrir(`h-${d.llamada_id}`, id, d.nombre, 'mcp', t);
          if (d.nombre === 'buscar_politicas' && this.nodoDeRol.embeddings) {
            const emb = this.nodoDeRol.embeddings;
            const desde = `${id}-salida`;
            if (this.svgAristas[desde]) { this._arista(desde, 1); this._arista(`salida-${emb}`, 1); this._activar('salida', 1); }
            this._activar(emb, 1);
            this._abrir(`e-${d.llamada_id}`, emb, 'embeddings de la consulta', 'embeddings', t);
            this.llamadas[d.llamada_id].emb = emb;
          }
        }
        this._arista('orq-auditoria', 1);
        this._activar('auditoria', 1);
        this._paso(`⚙ ${d.nombre}`, 'mcp');
        if (d.escritura) {
          this._arista('orq-aprobacion', 1);
          this._estado('aprobacion', 'espera');
        }
        this._pintarResumen();
        break;
      }
      case 'aprobacion.solicitada':
        this._estado('aprobacion', 'espera');
        this._metrica('aprobacion', 'esperando a la persona…');
        this._abrir(`ap-${d.aprobacion_id}`, 'aprobacion', 'esperando aprobación', 'guardia', t);
        this.stats.guardias += 1;
        break;
      case 'aprobacion.resuelta':
        this._arista('orq-aprobacion', -1);
        this._estado('aprobacion', d.aprobada ? 'hecho' : 'error');
        this._metrica('aprobacion', d.aprobada ? 'aprobada' : `no aprobada (${d.origen})`);
        this._cerrar(`ap-${d.aprobacion_id}`, t);
        break;
      case 'herramienta.fin': {
        const ll = this.llamadas[d.llamada_id] || { id: this.servidorDe?.[d.nombre] || this.mcpPorDefecto };
        if (ll.id) {
          this._enviar(ll.id, -1);
          if (!d.ok) this._estado(ll.id, 'error');
          this._metrica(ll.id, `${ms(d.duracion_ms)}${d.ok ? '' : ' · falló'}`);
          this._sub(ll.id, `${d.ok ? '✓' : '✕'} ${d.nombre}`);
          this._cerrar(`h-${d.llamada_id}`, t);
        }
        if (ll.emb) {
          const desde = `${ll.id}-salida`;
          if (this.svgAristas[desde]) { this._arista(desde, -1); this._arista(`salida-${ll.emb}`, -1); this._activar('salida', -1); }
          this._activar(ll.emb, -1);
          this._metrica(ll.emb, 'vectorizó la consulta');
          this._cerrar(`e-${d.llamada_id}`, t);
        }
        this._arista('orq-auditoria', -1);
        this._activar('auditoria', -1);
        this._metrica('auditoria', `${this.stats.herramientas} llamada${this.stats.herramientas === 1 ? '' : 's'} registrada${this.stats.herramientas === 1 ? '' : 's'}`);
        if (this.cfg.proveedor === 'pais' && this.nodos.verificacion) this._estado('verificacion', 'activo');
        break;
      }
      case 'citas':
        this._metrica('verificacion', `${(d.citas || []).length} cita(s) de política`);
        this._estado('verificacion', 'hecho');
        break;
      case 'guardia':
        this.stats.guardias += 1;
        this._estado('verificacion', 'espera');
        this._metrica('verificacion', 'corrigió la respuesta');
        this._paso('🔎 respaldo aplicado', 'guardia');
        this.tramos.push({ clave: 'g', carril: 'verificacion', etiqueta: 'corrigió', tipo: 'guardia', t0: t, t1: t + 150 });
        if (!this.carriles.includes('verificacion')) this.carriles.push('verificacion');
        this._pintarResumen();
        break;
      case 'voz.audio':
        this._metrica('tts', `${d.indice + 1} frase${d.indice ? 's' : ''} · ${d.motor || ''}`);
        break;
      case 'respuesta.final':
        this._estado('resp', 'hecho');
        break;
      case 'error':
        if (ev.turno_id) {
          this._paso(`✕ ${d.codigo}`, 'error');
          const id = d.etapa === 'herramienta' ? null : d.etapa === 'llm' ? this.nodoDeRol.agente : d.etapa;
          if (id && this.nodos[id]) this._estado(id, 'error');
        }
        break;
      case 'turno.fin': {
        this.fin = t;
        for (const tr of this.tramos) if (tr.t1 === null) tr.t1 = t;
        for (const [id, c] of Object.entries(this.cuentaNodos)) if (c > 0) { this.cuentaNodos[id] = 0; this._estado(id, d.ok ? 'hecho' : 'error'); }
        for (const [id, c] of Object.entries(this.cuentaAristas)) if (c > 0) { this.cuentaAristas[id] = 0; this.svgAristas[id].dataset.estado = 'usada'; }
        if (this.svgAristas['orq-resp']) this.svgAristas['orq-resp'].dataset.estado = 'usada';
        this._estado('orq', d.ok ? 'hecho' : 'error');
        if (this.nodos.verificacion && this.svgNodos.verificacion.dataset.estado === 'activo') this._estado('verificacion', 'hecho');
        if (this.cuentaNodos.resp !== undefined || this.respondiendo) this._estado('resp', d.ok ? 'hecho' : 'error');
        const m = d.metricas || {};
        this._metrica('orq', `${ms(m.latencias_ms?.total)} · ${d.iteraciones ?? 0} vuelta(s)`);
        this._paso(d.ok ? '✓ respuesta entregada' : (d.cancelado ? '■ cancelado' : '✕ terminó con error'), d.ok ? 'ok' : 'error');
        this.respondiendo = false;
        this._pintarResumen(m.ttft_ms ? ` · primer token ${ms(m.ttft_ms)}` : '');
        this._pintarTiempo();
        break;
      }
      default:
        break;
    }
  }
}
