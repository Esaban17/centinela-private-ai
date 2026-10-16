// Conexión con el orquestador (contrato de eventos v1, docs/CONTRATO_EVENTOS.md).
// WebSocket /ws/{sesion_id} con reconexión automática y reanudación por `seq`.

export class Conexion {
  constructor(sesionId, { alEvento, alEstado }) {
    this.sesionId = sesionId;
    this.alEvento = alEvento;
    this.alEstado = alEstado;
    this.ultimoSeq = 0;
    this.ws = null;
    this.espera = 500;
    this.cerrada = false;
  }

  conectar() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const desde = this.ultimoSeq ? `?desde_seq=${this.ultimoSeq}` : '?desde_seq=0';
    this.alEstado('conectando');
    const ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(this.sesionId)}${desde}`);
    this.ws = ws;
    ws.onopen = () => { this.espera = 500; this.alEstado('conectado'); };
    ws.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch { return; }
      // seq 0 = eventos fuera del búfer (sesion.lista, pong); los demás se deduplican al reanudar.
      if (ev.seq) {
        if (ev.seq <= this.ultimoSeq) return;
        this.ultimoSeq = ev.seq;
      }
      this.alEvento(ev);
    };
    ws.onclose = () => {
      if (this.cerrada) return;
      this.alEstado('desconectado');
      setTimeout(() => this.conectar(), this.espera);
      this.espera = Math.min(this.espera * 2, 5000);
    };
    ws.onerror = () => ws.close();
  }

  enviar(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  cerrar() {
    this.cerrada = true;
    this.ws?.close();
  }

  // ─── REST (mismo origen) ───
  async subirAudio(blob) {
    const fd = new FormData();
    fd.append('audio', blob, `voz.${blob.type.includes('ogg') ? 'ogg' : blob.type.includes('wav') ? 'wav' : 'webm'}`);
    return pedir(`/api/sesiones/${encodeURIComponent(this.sesionId)}/audio`, { method: 'POST', body: fd });
  }
}

export async function pedir(url, opciones = {}) {
  const r = await fetch(url, opciones);
  if (!r.ok) {
    let detalle = '';
    try { detalle = (await r.json()).detail || ''; } catch { /* sin cuerpo */ }
    throw new Error(`${r.status} ${detalle}`.trim());
  }
  const tipo = r.headers.get('content-type') || '';
  return tipo.includes('json') ? r.json() : r;
}

export async function subirDocumento(archivo) {
  const fd = new FormData();
  fd.append('archivo', archivo, archivo.name);
  return pedir('/api/documentos', { method: 'POST', body: fd });
}
