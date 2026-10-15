/**
 * Enregistrement du microphone et envoi progressif au serveur.
 *
 * Choix de conception :
 *  - un seul `MediaRecorder` pour toute la réunion : la pause et la reprise
 *    n'ouvrent pas un nouveau fichier, les morceaux se recollent en un flux
 *    continu unique ;
 *  - chaque morceau part vers le serveur dès qu'il est produit, dans l'ordre.
 *    Si l'onglet ou la machine s'arrête, tout ce qui a été envoyé est déjà
 *    sur le disque du serveur ;
 *  - la file d'envoi réessaie en cas de coupure réseau et se resynchronise si
 *    le serveur attend un autre numéro de morceau : aucun doublon, aucun trou.
 */

import { api } from './api'

export type RecorderState =
  | 'idle' | 'requesting' | 'recording' | 'paused' | 'stopping' | 'stopped' | 'error'

export interface RecorderCallbacks {
  onState?: (state: RecorderState) => void
  onLevel?: (level: number) => void
  onElapsed?: (seconds: number) => void
  /** `fatal` à vrai : l'enregistrement ne peut pas continuer. */
  onProblem?: (message: string, fatal: boolean) => void
  onProblemCleared?: () => void
  onUploadQueue?: (pending: number, uploadedBytes: number) => void
}

const TIMESLICE_MS = 5000
const MAX_UPLOAD_ATTEMPTS = 6
/** Amplitude efficace en dessous de laquelle on considère qu'il n'y a pas de son.
 *  Valeur basse à dessein : mieux vaut ne pas alerter que crier au loup dans une
 *  salle calme. */
const SILENCE_THRESHOLD = 0.004
const SILENCE_ALERT_AFTER_MS = 15000
/** Plage affichée par l'indicateur de niveau, en décibels pleine échelle. */
const METER_FLOOR_DB = -60

/** Formats tentés dans l'ordre ; le premier accepté par le navigateur gagne. */
const PREFERRED_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

export function pickMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  for (const type of PREFERRED_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type
  }
  return ''
}

export function microphoneErrorMessage(error: unknown): string {
  const name = (error as DOMException)?.name ?? ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return "L'accès au microphone a été refusé. Autorisez-le dans les réglages du navigateur (icône à gauche de la barre d'adresse), puis réessayez."
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return "Aucun microphone n'a été détecté. Branchez un micro ou un casque, puis réessayez."
    case 'NotReadableError':
    case 'TrackStartError':
      return "Le microphone est déjà utilisé par une autre application. Fermez-la (visioconférence, enregistreur…) puis réessayez."
    case 'OverconstrainedError':
      return "Le microphone ne gère pas les réglages demandés. Essayez un autre périphérique."
    case 'SecurityError':
      return "Le navigateur bloque l'accès au microphone sur cette adresse. Ouvrez l'application depuis http://127.0.0.1 ou en HTTPS."
    default:
      return `Le microphone n'a pas pu être ouvert${name ? ` (${name})` : ''}.`
  }
}

export class MeetingRecorder {
  private stream: MediaStream | null = null
  private recorder: MediaRecorder | null = null
  private audioContext: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  /** Référence conservée volontairement : l'analyseur n'étant relié à aucune
   *  sortie, le navigateur peut libérer un nœud source qui n'est plus
   *  référencé, et l'indicateur de niveau retombe alors silencieusement à
   *  zéro alors que l'enregistrement, lui, continue. */
  private sourceNode: MediaStreamAudioSourceNode | null = null
  private levelTimer: number | null = null
  private elapsedTimer: number | null = null

  private queue: Blob[] = []
  private nextSeq = 0
  private uploading = false
  private uploadedBytes = 0
  private finalFlush: Promise<void> | null = null

  private accumulatedMs = 0
  private segmentStart = 0
  private lastLoudAt = 0
  private silenceReported = false
  private networkReported = false

  state: RecorderState = 'idle'
  mimeType = ''

  constructor(private meetingId: string, private callbacks: RecorderCallbacks = {}) {}

  // ----------------------------------------------------------------- cycle

  async start(): Promise<void> {
    this.setState('requesting')
    if (!navigator.mediaDevices?.getUserMedia) {
      this.fail(
        "Ce navigateur ne permet pas l'accès au microphone sur cette adresse. " +
        'Ouvrez l\'application depuis http://127.0.0.1 ou en HTTPS.',
      )
      throw new Error('getUserMedia indisponible')
    }
    this.mimeType = pickMimeType()
    if (typeof MediaRecorder === 'undefined') {
      this.fail("Ce navigateur ne sait pas enregistrer l'audio (MediaRecorder absent).")
      throw new Error('MediaRecorder indisponible')
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
    } catch (error) {
      this.fail(microphoneErrorMessage(error))
      throw error
    }

    // Reprise d'un enregistrement interrompu : on repart du morceau attendu.
    try {
      const state = await api.recordingState(this.meetingId)
      this.nextSeq = state.next_seq
      this.uploadedBytes = state.bytes
    } catch {
      this.nextSeq = 0
    }

    this.startLevelMeter()

    this.recorder = new MediaRecorder(
      this.stream,
      this.mimeType ? { mimeType: this.mimeType, audioBitsPerSecond: 48000 } : undefined,
    )
    this.mimeType = this.recorder.mimeType || this.mimeType || 'audio/webm'

    this.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        this.queue.push(event.data)
        this.notifyQueue()
        void this.drain()
      }
    }
    this.recorder.onerror = () => {
      this.fail("L'enregistrement s'est interrompu de façon inattendue. Les données déjà envoyées sont conservées.")
    }

    this.accumulatedMs = 0
    this.segmentStart = performance.now()
    this.lastLoudAt = performance.now()
    this.recorder.start(TIMESLICE_MS)
    this.setState('recording')
    this.startElapsedTimer()
  }

  pause(): void {
    if (this.recorder?.state !== 'recording') return
    this.recorder.pause()
    this.accumulatedMs += performance.now() - this.segmentStart
    this.setState('paused')
  }

  resume(): void {
    if (this.recorder?.state !== 'paused') return
    this.recorder.resume()
    this.segmentStart = performance.now()
    this.lastLoudAt = performance.now()
    this.setState('recording')
  }

  /** Arrête l'enregistrement et attend que tous les morceaux soient envoyés. */
  async stop(): Promise<{ mimeType: string; durationSeconds: number }> {
    if (!this.recorder) {
      return { mimeType: this.mimeType, durationSeconds: this.elapsedSeconds() }
    }
    this.setState('stopping')
    if (this.recorder.state === 'recording') {
      this.accumulatedMs += performance.now() - this.segmentStart
    }

    const stopped = new Promise<void>((resolve) => {
      this.recorder!.onstop = () => resolve()
    })
    if (this.recorder.state !== 'inactive') this.recorder.stop()
    await stopped

    await this.drain()
    const duration = this.elapsedSeconds()
    this.cleanup()
    this.setState('stopped')
    return { mimeType: this.mimeType, durationSeconds: duration }
  }

  /** Libère le microphone sans attendre la fin des envois (fermeture d'onglet). */
  dispose(): void {
    this.cleanup()
  }

  elapsedSeconds(): number {
    const running = this.state === 'recording' ? performance.now() - this.segmentStart : 0
    return (this.accumulatedMs + running) / 1000
  }

  get pendingChunks(): number {
    return this.queue.length
  }

  // ------------------------------------------------------------- envoi

  private async drain(): Promise<void> {
    if (this.uploading) return this.finalFlush ?? Promise.resolve()
    this.uploading = true
    this.finalFlush = (async () => {
      while (this.queue.length > 0) {
        const blob = this.queue[0]
        const sent = await this.sendWithRetry(blob)
        if (!sent) break // échec définitif : on garde le morceau pour un nouvel essai
        this.queue.shift()
        this.uploadedBytes += blob.size
        this.nextSeq += 1
        this.notifyQueue()
      }
      this.uploading = false
    })()
    return this.finalFlush
  }

  private async sendWithRetry(blob: Blob): Promise<boolean> {
    for (let attempt = 0; attempt < MAX_UPLOAD_ATTEMPTS; attempt += 1) {
      try {
        const result = await api.uploadChunk(this.meetingId, this.nextSeq, blob)
        if (result.ok) {
          if (this.networkReported) {
            this.networkReported = false
            this.callbacks.onProblemCleared?.()
          }
          return true
        }
        // Le serveur attend un autre numéro : on se recale et on réessaie.
        if (result.expected > this.nextSeq) {
          this.nextSeq = result.expected
          return true // ce morceau est déjà arrivé
        }
        this.nextSeq = result.expected
      } catch {
        if (!this.networkReported) {
          this.networkReported = true
          this.callbacks.onProblem?.(
            "La connexion au serveur est interrompue. L'enregistrement continue et " +
            'les données en attente seront envoyées dès le rétablissement.',
            false,
          )
        }
        await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** attempt, 15000)))
      }
    }
    return false
  }

  private notifyQueue(): void {
    this.callbacks.onUploadQueue?.(this.queue.length, this.uploadedBytes)
  }

  // ------------------------------------------------------ niveau sonore

  private startLevelMeter(): void {
    if (!this.stream) return
    try {
      const Context = window.AudioContext ?? (window as never as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      this.audioContext = new Context()
      // Chrome crée le contexte audio en état « suspendu » tant qu'aucun geste
      // utilisateur n'a eu lieu. Sans cette reprise explicite, l'analyseur ne
      // reçoit que du silence et l'indicateur de niveau reste à zéro.
      if (this.audioContext.state === 'suspended') {
        void this.audioContext.resume().catch(() => undefined)
      }
      this.sourceNode = this.audioContext.createMediaStreamSource(this.stream)
      this.analyser = this.audioContext.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.smoothingTimeConstant = 0.3
      this.sourceNode.connect(this.analyser)
    } catch {
      return // l'indicateur de niveau est un confort, pas une condition
    }

    const buffer = new Float32Array(this.analyser.fftSize)
    this.levelTimer = window.setInterval(() => {
      if (!this.analyser) return
      this.analyser.getFloatTimeDomainData(buffer)
      let sum = 0
      for (const value of buffer) sum += value * value
      const rms = Math.sqrt(sum / buffer.length)
      // L'oreille perçoit l'intensité de façon logarithmique : une échelle
      // linéaire écraserait tout le registre de la parole dans les premiers
      // pour-cent de la barre.
      const decibels = rms > 0 ? 20 * Math.log10(rms) : METER_FLOOR_DB
      const level = Math.min(1, Math.max(0, (decibels - METER_FLOOR_DB) / -METER_FLOOR_DB))
      this.callbacks.onLevel?.(level)
      this.watchSilence(rms)
    }, 100)
  }

  /** Avertit si plus aucun son n'est capté : micro coupé, mauvais périphérique…
   *  Le contrôle porte sur l'amplitude brute, pas sur la valeur affichée. */
  private watchSilence(rms: number): void {
    if (this.state !== 'recording') return
    const now = performance.now()
    if (rms > SILENCE_THRESHOLD) {
      this.lastLoudAt = now
      if (this.silenceReported) {
        this.silenceReported = false
        this.callbacks.onProblemCleared?.()
      }
      return
    }
    if (!this.silenceReported && now - this.lastLoudAt > SILENCE_ALERT_AFTER_MS) {
      this.silenceReported = true
      this.callbacks.onProblem?.(
        "Aucun son n'est capté depuis une dizaine de secondes. Vérifiez que le " +
        'microphone n\'est pas coupé et qu\'il s\'agit du bon périphérique.',
        false,
      )
    }
  }

  private startElapsedTimer(): void {
    this.elapsedTimer = window.setInterval(() => {
      this.callbacks.onElapsed?.(this.elapsedSeconds())
    }, 250)
  }

  // ------------------------------------------------------------ interne

  private setState(state: RecorderState): void {
    this.state = state
    this.callbacks.onState?.(state)
  }

  private fail(message: string): void {
    this.setState('error')
    this.callbacks.onProblem?.(message, true)
  }

  private cleanup(): void {
    if (this.levelTimer !== null) window.clearInterval(this.levelTimer)
    if (this.elapsedTimer !== null) window.clearInterval(this.elapsedTimer)
    this.levelTimer = null
    this.elapsedTimer = null
    this.sourceNode?.disconnect()
    this.sourceNode = null
    this.analyser = null
    void this.audioContext?.close().catch(() => undefined)
    this.audioContext = null
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
  }
}
