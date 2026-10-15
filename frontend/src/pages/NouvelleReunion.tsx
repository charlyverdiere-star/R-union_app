import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { MeetingRecorder, type RecorderState } from '../lib/recorder'
import type { AppConfig, Meeting } from '../lib/types'
import { formatBytes, formatDuration, today } from '../lib/format'

type Etape = 'formulaire' | 'enregistrement' | 'termine'

interface Probleme { message: string; fatal: boolean }

export default function NouvelleReunion({ config }: { config: AppConfig | null }) {
  const navigate = useNavigate()
  const [etape, setEtape] = useState<Etape>('formulaire')

  const [titre, setTitre] = useState('')
  const [date, setDate] = useState(today())
  const [contexte, setContexte] = useState('')
  const [notes, setNotes] = useState('')

  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [etatEnregistreur, setEtatEnregistreur] = useState<RecorderState>('idle')
  const [duree, setDuree] = useState(0)
  const [niveau, setNiveau] = useState(0)
  const [probleme, setProbleme] = useState<Probleme | null>(null)
  const [enAttente, setEnAttente] = useState(0)
  const [envoye, setEnvoye] = useState(0)
  const [erreur, setErreur] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)

  const enregistreur = useRef<MeetingRecorder | null>(null)
  const fichier = useRef<HTMLInputElement>(null)

  // Avertit avant de fermer l'onglet pendant un enregistrement.
  useEffect(() => {
    const actif = etatEnregistreur === 'recording' || etatEnregistreur === 'paused'
    if (!actif) return
    const garde = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', garde)
    return () => window.removeEventListener('beforeunload', garde)
  }, [etatEnregistreur])

  useEffect(() => () => enregistreur.current?.dispose(), [])

  const creerReunion = useCallback(async (source: 'recording' | 'import') => {
    const nom = titre.trim() || 'Réunion sans titre'
    return api.createMeeting({
      title: nom, meeting_date: date,
      context: contexte.trim() || undefined,
      notes: notes.trim() || undefined,
      source,
    })
  }, [titre, date, contexte, notes])

  // ------------------------------------------------------- enregistrement

  const demarrer = async () => {
    setErreur(null); setProbleme(null); setOccupe(true)
    try {
      const creee = await creerReunion('recording')
      setMeeting(creee)
      const instance = new MeetingRecorder(creee.id, {
        onState: setEtatEnregistreur,
        onLevel: setNiveau,
        onElapsed: setDuree,
        onProblem: (message, fatal) => setProbleme({ message, fatal }),
        onProblemCleared: () => setProbleme(null),
        onUploadQueue: (pending, bytes) => { setEnAttente(pending); setEnvoye(bytes) },
      })
      enregistreur.current = instance
      setEtape('enregistrement')
      await instance.start()
    } catch (e) {
      // Le message précis est déjà remonté par onProblem ; on reste sur l'écran.
      if (!(e instanceof Error) || !probleme) setEtatEnregistreur('error')
    } finally {
      setOccupe(false)
    }
  }

  const arreter = async () => {
    const instance = enregistreur.current
    if (!instance || !meeting) return
    setOccupe(true)
    try {
      const { mimeType, durationSeconds } = await instance.stop()
      await api.completeRecording(meeting.id, {
        mime_type: mimeType,
        duration_seconds: durationSeconds,
        start_processing: false,
      })
      setDuree(durationSeconds)
      setEtape('termine')
    } catch (e) {
      setErreur(
        `L'enregistrement n'a pas pu être finalisé : ${(e as Error).message}. ` +
        'Les données déjà transmises restent disponibles dans la réunion.',
      )
      setEtape('termine')
    } finally {
      setOccupe(false)
    }
  }

  const lancerTraitement = async () => {
    if (!meeting) return
    setOccupe(true)
    try {
      await api.process(meeting.id)
      navigate(`/reunion/${meeting.id}`)
    } catch (e) {
      setErreur((e as Error).message)
      setOccupe(false)
    }
  }

  // --------------------------------------------------------------- import

  const importer = async (choisi: File) => {
    setErreur(null); setOccupe(true)
    try {
      const creee = await creerReunion('import')
      await api.importAudio(creee.id, choisi)
      await api.process(creee.id)
      navigate(`/reunion/${creee.id}`)
    } catch (e) {
      setErreur((e as Error).message)
      setOccupe(false)
    }
  }

  // --------------------------------------------------------------- rendus

  if (etape === 'formulaire') {
    return (
      <>
        <div className="fil"><Link to="/">← Tableau de bord</Link></div>
        <h1>Nouvelle réunion</h1>

        {erreur && <div className="message message-erreur">{erreur}</div>}

        <div className="carte">
          <label className="champ">
            <span>Titre de la réunion</span>
            <input
              type="text" value={titre} autoFocus placeholder="Point d'avancement hebdomadaire"
              onChange={(e) => setTitre(e.target.value)}
            />
            <small>Sert à nommer les fichiers produits. Modifiable plus tard.</small>
          </label>

          <div className="grille grille-2">
            <label className="champ">
              <span>Date</span>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
          </div>

          <label className="champ">
            <span>Contexte (facultatif)</span>
            <textarea
              value={contexte} onChange={(e) => setContexte(e.target.value)}
              placeholder="Projet concerné, participants attendus, objectif de la réunion…"
            />
            <small>Transmis à l'analyse pour mieux situer les échanges. N'invente rien : sert uniquement de repère.</small>
          </label>

          <label className="champ">
            <span>Informations complémentaires (facultatif)</span>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes personnelles" />
          </label>

          <div className="message message-info">
            <strong>Avant d'enregistrer</strong>
            Informez les participants et obtenez leur accord. L'enregistrement de
            réunions professionnelles est encadré par la réglementation applicable
            à votre organisation.
          </div>

          <div className="actions">
            <button className="btn btn-principal btn-grand" onClick={() => void demarrer()} disabled={occupe}>
              ● Démarrer l'enregistrement
            </button>
            <button className="btn btn-grand" onClick={() => fichier.current?.click()} disabled={occupe}>
              Importer un fichier audio
            </button>
            <input
              ref={fichier} type="file" accept="audio/*,video/*,.m4a,.webm,.ogg,.wav,.mp3,.flac"
              hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void importer(f) }}
            />
          </div>
          {config && (
            <p className="petit doux" style={{ marginTop: 10, marginBottom: 0 }}>
              Taille maximale d'un import : {config.max_upload_mb} Mo.
            </p>
          )}
        </div>
      </>
    )
  }

  if (etape === 'enregistrement') {
    const actif = etatEnregistreur === 'recording'
    const enPause = etatEnregistreur === 'paused'
    const pretMicro = actif || enPause
    return (
      <>
        <h1>{titre.trim() || 'Réunion sans titre'}</h1>

        {probleme && (
          <div className={`message ${probleme.fatal ? 'message-erreur' : 'message-alerte'}`}>
            <strong>{probleme.fatal ? 'Enregistrement impossible' : 'Attention'}</strong>
            {probleme.message}
          </div>
        )}
        {erreur && <div className="message message-erreur">{erreur}</div>}

        <div className="carte enregistreur">
          <div className="chrono mono">{formatDuration(duree)}</div>
          <div className="chrono-etat">
            <span className={`pastille ${actif ? 'pastille-active' : enPause ? 'pastille-pause' : ''}`} />
            {etatEnregistreur === 'requesting' && 'Autorisation du microphone demandée…'}
            {actif && 'Enregistrement en cours'}
            {enPause && 'En pause — l\'enregistrement reprendra dans le même fichier'}
            {etatEnregistreur === 'stopping' && 'Finalisation…'}
            {etatEnregistreur === 'error' && 'Le microphone n\'est pas disponible'}
          </div>

          {pretMicro && (
            <>
              <div className="niveau" role="img" aria-label={`Niveau sonore : ${Math.round(niveau * 100)} %`}>
                <div className="niveau-barre" style={{ width: `${Math.min(100, niveau * 100)}%` }} />
              </div>
              <p className="petit doux">
                {niveau > 0.05 ? 'Le microphone capte du son.' : 'Aucun son détecté pour le moment.'}
              </p>
            </>
          )}

          <div className="actions" style={{ justifyContent: 'center', marginTop: 18 }}>
            {actif && (
              <button className="btn btn-grand" onClick={() => enregistreur.current?.pause()}>
                ❚❚ Mettre en pause
              </button>
            )}
            {enPause && (
              <button className="btn btn-principal btn-grand" onClick={() => enregistreur.current?.resume()}>
                ▶ Reprendre
              </button>
            )}
            {(actif || enPause) && (
              <button className="btn btn-danger btn-grand" onClick={() => void arreter()} disabled={occupe}>
                ■ Arrêter
              </button>
            )}
            {etatEnregistreur === 'error' && (
              <button className="btn btn-principal" onClick={() => setEtape('formulaire')}>
                Revenir et réessayer
              </button>
            )}
          </div>

          <p className="petit doux" style={{ marginTop: 18, marginBottom: 0 }}>
            Sauvegarde continue : {formatBytes(envoye)} déjà transmis
            {enAttente > 0 && ` · ${enAttente} morceau(x) en attente d'envoi`}
          </p>
        </div>

        <p className="petit doux" style={{ textAlign: 'center' }}>
          Vous pouvez mettre en pause autant de fois que nécessaire : un seul fichier audio sera produit.
        </p>
      </>
    )
  }

  // étape « termine »
  return (
    <>
      <h1>Enregistrement terminé</h1>
      {erreur && <div className="message message-erreur">{erreur}</div>}
      <div className="carte">
        <p>
          <strong>{titre.trim() || 'Réunion sans titre'}</strong> — durée {formatDuration(duree)},
          {' '}{formatBytes(envoye)} enregistrés.
        </p>
        <p className="doux">
          Vous pouvez lancer le traitement maintenant, ou le faire plus tard depuis
          la fiche de la réunion. L'enregistrement audio est déjà conservé.
        </p>
        {config && !config.asr_ready && (
          <div className="message message-alerte">
            <strong>Traitement indisponible</strong>
            {config.asr_problem} L'enregistrement est conservé : vous pourrez lancer
            le traitement une fois la configuration complétée.
          </div>
        )}
        <div className="actions">
          <button
            className="btn btn-principal btn-grand"
            onClick={() => void lancerTraitement()}
            disabled={occupe || (config ? !config.asr_ready : false)}
          >
            Lancer le traitement
          </button>
          <button className="btn" onClick={() => meeting && navigate(`/reunion/${meeting.id}`)}>
            Plus tard — voir la réunion
          </button>
        </div>
      </div>
    </>
  )
}
