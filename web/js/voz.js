// Voz en el navegador: push-to-talk (botón o barra espaciadora) y reproducción de la respuesta hablada.
// El audio se graba aquí y se transcribe en el orquestador (mlx-whisper local); nada sale del equipo.
// No se usa speechSynthesis del navegador: algunas voces de Chrome son remotas.

export class PushToTalk {
  constructor({ boton, aviso, alAudio, alError }) {
    this.boton = boton;
    this.aviso = aviso;
    this.alAudio = alAudio;
    this.alError = alError;
    this.grabador = null;
    this.trozos = [];
    this.stream = null;
    this.activo = false;
    this.inicio = 0;
  }

  enlazar() {
    const empezar = (e) => { e.preventDefault(); this.empezar(); };
    const terminar = (e) => { e.preventDefault(); this.terminar(); };
    this.boton.addEventListener('pointerdown', empezar);
    this.boton.addEventListener('pointerup', terminar);
    this.boton.addEventListener('pointerleave', () => this.activo && this.terminar());
    document.addEventListener('keydown', (e) => {
      if (e.code !== 'Space' || e.repeat || escribiendo(e.target)) return;
      e.preventDefault();
      this.empezar();
    });
    document.addEventListener('keyup', (e) => {
      if (e.code !== 'Space' || escribiendo(e.target)) return;
      e.preventDefault();
      this.terminar();
    });
  }

  async empezar() {
    if (this.activo) return;
    this.activo = true;
    this.inicio = performance.now();
    this._indicar(true);
    try {
      // El micrófono se pide una vez y se conserva: así no hay demora al presionar en escena.
      this.stream ??= await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (!this.activo) return; // se soltó antes de tener micrófono
      const tipo = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', ''].find((t) => !t || MediaRecorder.isTypeSupported(t));
      this.trozos = [];
      this.grabador = new MediaRecorder(this.stream, tipo ? { mimeType: tipo } : {});
      this.grabador.ondataavailable = (e) => e.data.size && this.trozos.push(e.data);
      this.grabador.onstop = () => {
        const blob = new Blob(this.trozos, { type: this.grabador.mimeType || 'audio/webm' });
        if (performance.now() - this.inicio < 350 || blob.size < 1000) {
          this.alError('Mantenga presionado mientras habla.');
          return;
        }
        this.alAudio(blob);
      };
      this.grabador.start();
    } catch (e) {
      this.activo = false;
      this._indicar(false);
      this.alError(`No se pudo usar el micrófono (${e.name || e}). Use los botones o escriba.`);
    }
  }

  terminar() {
    if (!this.activo) return;
    this.activo = false;
    this._indicar(false);
    if (this.grabador && this.grabador.state === 'recording') this.grabador.stop();
  }

  _indicar(on) {
    this.boton.classList.toggle('grabando', on);
    this.aviso.classList.toggle('oculto', !on);
  }
}

function escribiendo(t) {
  // En la tarjeta de aprobación la barra espaciadora debe accionar el botón, no grabar.
  return t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable || !!t.closest?.('.aprobacion'));
}

/** Cola de audio de la respuesta (eventos voz.audio, una oración por evento, en orden de `indice`). */
export class Reproductor {
  constructor() {
    this.cola = [];
    this.actual = null;
    this.silencio = false;
    this.turno = null;
    this.siguiente = 0;
  }

  agregar(turnoId, d) {
    if (this.silencio) return;
    // El orquestador sintetiza en orden: la primera oración que llega marca el inicio de la cuenta.
    if (turnoId !== this.turno) { this.detener(); this.turno = turnoId; this.siguiente = d.indice; }
    this.cola.push(d);
    this.cola.sort((a, b) => a.indice - b.indice);
    this._tocar();
  }

  _tocar() {
    if (this.actual || !this.cola.length) return;
    // Respeta el orden: espera la oración `siguiente` aunque llegue después.
    if (this.cola[0].indice > this.siguiente) return;
    const d = this.cola.shift();
    this.siguiente = d.indice + 1;
    const a = new Audio(d.url);
    this.actual = a;
    const fin = () => { this.actual = null; this._tocar(); };
    a.onended = fin;
    a.onerror = fin;
    a.play().catch(fin);
  }

  detener() {
    this.cola = [];
    if (this.actual) { this.actual.pause(); this.actual = null; }
  }

  silenciar(on) {
    this.silencio = on;
    if (on) this.detener();
  }
}
