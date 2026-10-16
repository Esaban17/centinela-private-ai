// Zona 3: Panel de Soberanía.
// - Conexiones salientes, red, modelos cargados, memoria y costo: GET /api/soberania (cada `intervalo` ms).
// - Métricas del turno y acumulado: eventos `turno.fin` y `sesion.acumulado` del orquestador.
import { el, dinero, ms, num, ETAPAS, ROLES } from './util.js';

const $ = (id) => document.getElementById(id);

export class Panel {
  constructor({ intervalo = 1500 } = {}) {
    this.intervalo = intervalo;
    this.tokens = { entrada: 0, salida: 0 };
    this.fallos = 0;
    this.modelosPorRol = {};
  }

  iniciar() {
    this._sondear();
    setInterval(() => this._sondear(), this.intervalo);
  }

  reiniciarSesion() {
    this.tokens = { entrada: 0, salida: 0 };
    this.acumulado({ turnos: 0, tokens_entrada: 0, tokens_salida: 0, herramientas_ejecutadas: 0, documentos_analizados: 0, modelos_usados: [] });
    for (const id of ['k-ttft', 'k-tps', 'k-tin', 'k-tout']) $(id).textContent = '–';
    $('etapas').replaceChildren();
  }

  async _sondear() {
    try {
      const r = await fetch(`/api/soberania?tokens_entrada=${this.tokens.entrada}&tokens_salida=${this.tokens.salida}`, { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      this.fallos = 0;
      this._pintar(await r.json());
    } catch {
      this.fallos += 1;
      if (this.fallos >= 3) {
        $('salientes-detalle').textContent = 'El monitor no responde: ¿está corriendo el orquestador?';
        pill('estado-salientes', 'aviso', 'Monitor sin respuesta');
      }
    }
  }

  _pintar(s) {
    $('panel-hora').textContent = s.hora;
    // Conexiones salientes (lo más importante del panel)
    const c = s.conexiones;
    const ok = c.salientes === 0;
    $('tarjeta-salientes').dataset.estado = ok ? 'ok' : 'alerta';
    $('salientes-cifra').textContent = c.salientes;
    const vigilados = [...new Set(c.procesos.map((p) => p.proceso))].join(', ') || 'ninguno';
    const historico = c.max_sesion > 0 && ok ? ` · ⚠ hubo ${c.max_sesion} en la sesión` : '';
    $('salientes-detalle').textContent = `psutil sobre ${c.procesos.length} procesos (${vigilados}) · ${c.loopback} locales${historico}`;
    $('salientes-lista').replaceChildren(...c.detalle.slice(0, 6).map((x) => el('li', {}, `${x.proceso} → ${x.remoto} (${x.protocolo})`)));
    if (c.sin_permiso?.length) $('salientes-detalle').textContent += ` · sin permiso: ${c.sin_permiso.map((p) => p.proceso).join(', ')}`;
    pill('estado-salientes', ok ? 'ok' : 'alerta', `Conexiones externas: ${c.salientes}`);

    // Red del equipo
    const online = s.red.online;
    $('tarjeta-red').dataset.estado = online ? 'online' : 'offline';
    $('red-valor').textContent = online ? 'En línea' : 'Sin red ✈';
    $('red-detalle').textContent = online ? s.red.interfaces.map((i) => i.interfaz).join(', ') : 'Todo sigue corriendo en este equipo';
    pill('estado-red', online ? '' : 'offline', online ? 'Red: en línea' : 'Red: sin conexión ✈');

    // Modelos cargados en Ollama
    const o = s.ollama;
    if (o.disponible) {
      $('ollama-total').textContent = `· ${o.modelos.length} en memoria · ${o.memoria_total_gb} GB`;
      $('lista-modelos').replaceChildren(...(o.modelos.length ? o.modelos.map((m) => el('li', {},
        el('span', { class: 'nombre', title: m.nombre }, m.nombre),
        el('span', { class: 'mem' }, `${m.memoria_gb} GB${m.gpu_pct ? ` · GPU ${m.gpu_pct}%` : ''}`)))
        : [el('li', { class: 'tenue' }, 'Ningún modelo cargado: ejecute make warmup')]));
    } else {
      $('ollama-total').textContent = '';
      $('lista-modelos').replaceChildren(el('li', { class: 'tenue' }, o.error || 'Ollama no disponible'));
    }
    const sis = s.sistema;
    $('mem-relleno').style.width = `${Math.min(100, sis.memoria_pct)}%`;
    $('mem-detalle').textContent = `Memoria del equipo: ${sis.memoria_usada_gb} de ${sis.memoria_total_gb} GB (${Math.round(sis.memoria_pct)}%)`;

    // Costo equivalente (precios de config.yaml; sin precios quemados)
    const k = s.costo;
    if (k.configurado) {
      const gtq = k.monto_gtq !== null ? ` ≈ ${dinero(k.monto_gtq, 'GTQ')}` : '';
      $('costo-equivalente').textContent = `Equivalente en API: ${dinero(k.monto, k.moneda)}${gtq} · ${k.rotulo.toLowerCase()}${k.referencia ? ` (${k.referencia})` : ''}`;
    } else {
      $('costo-equivalente').textContent = 'Equivalente en API: sin precios en config.yaml';
    }
  }

  // ─── Eventos del orquestador ───
  evento(ev) {
    const d = ev.datos || {};
    if (ev.tipo === 'turno.fin' && d.metricas) this.turno(d.metricas);
    else if (ev.tipo === 'sesion.acumulado') this.acumulado(d);
    else if (ev.tipo === 'sesion.lista' && d.modelos) this.modelosPorRol = d.modelos;
  }

  turno(m) {
    $('k-ttft').textContent = ms(m.ttft_ms);
    $('k-tps').textContent = m.tokens_por_s ?? '–';
    $('k-tin').textContent = num(m.tokens_entrada);
    $('k-tout').textContent = num(m.tokens_salida);
    const lat = m.latencias_ms || {};
    const total = lat.total || Object.entries(lat).reduce((a, [, v]) => a + (v || 0), 0) || 1;
    const filas = Object.keys(ETAPAS).filter((e) => lat[e]).map((e) => {
      const barra = el('div');
      barra.style.width = `${Math.max(2, (100 * lat[e]) / total)}%`;
      return el('div', { class: 'etapa', dataset: { etapa: e } },
        el('span', { class: 'nombre' }, ETAPAS[e]), el('div', { class: 'pista' }, barra), el('span', { class: 'ms' }, ms(lat[e])));
    });
    filas.push(el('div', { class: 'etapa' }, el('span', { class: 'nombre' }, el('b', {}, 'Total')), el('span'), el('span', { class: 'ms' }, el('b', {}, ms(lat.total)))));
    $('etapas').replaceChildren(...filas);
  }

  acumulado(a) {
    this.tokens = { entrada: a.tokens_entrada || 0, salida: a.tokens_salida || 0 };
    $('a-tokens').textContent = num(this.tokens.entrada + this.tokens.salida);
    $('a-herr').textContent = num(a.herramientas_ejecutadas || 0);
    $('a-docs').textContent = num(a.documentos_analizados || 0);
    const usados = a.modelos_usados || [];
    $('a-modelos').textContent = num(usados.length);
    const rolDe = Object.fromEntries(Object.entries(this.modelosPorRol).map(([rol, nombre]) => [nombre, rol]));
    $('a-lista-modelos').replaceChildren(...usados.map((m) => el('span', { class: 'chip', dataset: { rol: rolDe[m] || '' }, title: m },
      rolDe[m] ? el('b', {}, `${ROLES[rolDe[m]]} · `) : null, m)));
    this._sondear();
  }
}

function pill(id, estado, texto) {
  const n = $(id);
  n.dataset.estado = estado;
  n.textContent = texto;
}
