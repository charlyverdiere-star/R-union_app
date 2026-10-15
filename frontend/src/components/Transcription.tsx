import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Segment, Transcript } from '../lib/types'
import { formatDuration } from '../lib/format'

export default function Transcription({
  meetingId, onEnregistre,
}: {
  meetingId: string
  onEnregistre: () => void
}) {
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [segments, setSegments] = useState<Segment[]>([])
  const [erreur, setErreur] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [enregistre, setEnregistre] = useState(false)
  const [filtre, setFiltre] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    void (async () => {
      try {
        const charge = await api.getTranscript(meetingId)
        setTranscript(charge)
        setSegments(charge.segments)
      } catch (e) {
        setErreur((e as Error).message)
      }
    })()
  }, [meetingId])

  const modifie = useMemo(
    () => transcript !== null && segments.some((s, i) => s.text !== transcript.segments[i]?.text),
    [segments, transcript],
  )

  const affiches = filtre ? segments.filter((s) => s.low_confidence) : segments

  const changer = (id: string, texte: string) => {
    setSegments((liste) => liste.map((s) => (s.id === id ? { ...s, text: texte } : s)))
    setEnregistre(false)
  }

  const ecouter = (debut: number) => {
    const lecteur = audio.current
    if (!lecteur) return
    lecteur.currentTime = debut
    void lecteur.play()
  }

  const sauvegarder = async () => {
    setOccupe(true); setErreur(null)
    try {
      const sauve = await api.saveTranscript(meetingId, segments)
      setTranscript(sauve)
      setSegments(sauve.segments)
      setEnregistre(true)
      onEnregistre()
    } catch (e) {
      setErreur((e as Error).message)
    } finally {
      setOccupe(false)
    }
  }

  if (erreur && !transcript) return <div className="message message-erreur">{erreur}</div>
  if (!transcript) return <div className="chargement">Chargement de la transcription…</div>

  const incertains = segments.filter((s) => s.low_confidence).length

  return (
    <div className="carte">
      <div className="carte-titre">
        <h2>Transcription</h2>
        <span className="doux petit">
          {segments.length} passages · {transcript.model}
          {transcript.edited_at && ' · corrigée'}
        </span>
      </div>

      {transcript.warnings.map((w) => (
        <div className="message message-alerte" key={w}>{w}</div>
      ))}
      {erreur && <div className="message message-erreur">{erreur}</div>}
      {enregistre && (
        <div className="message message-succes">
          <strong>Transcription enregistrée</strong>
          Régénérez le rapport et la synthèse pour qu'ils tiennent compte de vos corrections.
          L'audio ne sera pas retranscrit.
        </div>
      )}

      <p className="petit doux">
        Cliquez dans un passage pour le corriger. Cliquez sur l'horodatage pour réécouter
        le moment correspondant. Les passages sur fond coloré ont été transcrits avec une
        confiance faible : ce sont ceux à relire en priorité.
      </p>

      <audio ref={audio} controls preload="none" src={api.audioUrl(meetingId)} />

      <div className="barre-outils" style={{ marginTop: 14 }}>
        <button
          className="btn btn-petit" onClick={() => setFiltre((v) => !v)}
          disabled={incertains === 0}
        >
          {filtre ? 'Afficher tous les passages' : `Afficher les ${incertains} passages incertains`}
        </button>
        <button
          className="btn btn-petit btn-principal"
          onClick={() => void sauvegarder()} disabled={!modifie || occupe}
        >
          {occupe ? 'Enregistrement…' : 'Enregistrer les corrections'}
        </button>
        {modifie && <span className="badge badge-alerte">Modifications non enregistrées</span>}
      </div>

      <div className="segments">
        {affiches.map((segment) => (
          <div
            key={segment.id}
            className={`segment${segment.low_confidence ? ' segment-incertain' : ''}${segment.edited ? ' segment-modifie' : ''}`}
          >
            <button
              className="segment-horodatage mono" onClick={() => ecouter(segment.start)}
              title="Réécouter ce passage"
            >
              {formatDuration(segment.start)}
            </button>
            <div className="segment-corps">
              {segment.speaker && <div className="segment-locuteur">{segment.speaker}</div>}
              <textarea
                className="segment-texte" value={segment.text} rows={1}
                onChange={(e) => changer(segment.id, e.target.value)}
                onInput={(e) => {
                  const zone = e.currentTarget
                  zone.style.height = 'auto'
                  zone.style.height = `${zone.scrollHeight}px`
                }}
                aria-label={`Passage à ${formatDuration(segment.start)}`}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
