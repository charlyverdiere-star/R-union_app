import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import Etapes from '../components/Etapes'
import Transcription from '../components/Transcription'
import type { AppConfig, Deliverable, Meeting, ReportSummary } from '../lib/types'
import { STATUS_LABELS, formatBytes, formatDate, formatDateTime } from '../lib/format'

/** Les trois livrables, présentés sous forme de cartes distinctes. */
function CarteLivrable({
  meeting, livrable,
}: {
  meeting: Meeting
  livrable: Deliverable
}) {
  const descriptions: Record<Deliverable['kind'], string> = {
    audio: "L'enregistrement original de la réunion, conservé sans modification.",
    report: "Compte rendu détaillé : contexte, échanges, décisions, plan d'action et questions ouvertes.",
    summary: "L'essentiel en deux minutes : décisions, actions, points de vigilance.",
  }

  return (
    <div className="carte">
      <div className="carte-titre">
        <h3>{livrable.label}</h3>
        {livrable.available
          ? <span className="badge badge-ok">Disponible</span>
          : <span className="badge">En attente</span>}
      </div>
      <p className="petit doux">{descriptions[livrable.kind]}</p>

      {livrable.available ? (
        <>
          <p className="petit mono doux" style={{ wordBreak: 'break-all' }}>{livrable.filename}</p>
          {livrable.kind === 'audio' && (
            <audio controls preload="none" src={api.audioUrl(meeting.id)} />
          )}
          <div className="actions" style={{ marginTop: 10 }}>
            {livrable.formats.map((format) => (
              <button
                key={format} className="btn btn-petit"
                onClick={() => void api.download(
                  meeting.id, livrable.kind, format,
                  livrable.filename.replace(/\.[^.]+$/, `.${format}`),
                )}
              >
                Télécharger {format.toUpperCase()}
              </button>
            ))}
          </div>
          <p className="petit doux" style={{ marginTop: 8, marginBottom: 0 }}>
            {formatBytes(livrable.size_bytes)}
          </p>
        </>
      ) : (
        <p className="petit doux" style={{ marginBottom: 0 }}>
          Ce fichier sera disponible une fois le traitement terminé.
        </p>
      )}
    </div>
  )
}

function Fiabilite({ rapport }: { rapport: ReportSummary }) {
  const taux = rapport.items_total
    ? Math.round((rapport.items_verified / rapport.items_total) * 100)
    : 0
  return (
    <div className="carte">
      <div className="carte-titre">
        <h2>Fiabilité du compte rendu</h2>
        <span className="doux petit">
          généré le {formatDateTime(rapport.generated_at)} — {rapport.llm_model}
        </span>
      </div>
      <table>
        <tbody>
          <tr>
            <td>Informations retrouvées mot pour mot dans le passage cité</td>
            <td className="mono"><strong>{rapport.items_verified} / {rapport.items_total}</strong> ({taux} %)</td>
          </tr>
          <tr>
            <td>Informations signalées « à vérifier » dans les documents</td>
            <td className="mono">{rapport.items_partial}</td>
          </tr>
          <tr>
            <td>Informations écartées faute de passage justificatif</td>
            <td className="mono">{rapport.dropped_items}</td>
          </tr>
          <tr>
            <td>Passages transcrits avec une confiance faible</td>
            <td className="mono">{Math.round(rapport.low_confidence_ratio * 100)} %</td>
          </tr>
        </tbody>
      </table>
      <p className="petit doux" style={{ marginTop: 12 }}>
        Chaque décision, chiffre et action du compte rendu est rattaché à un passage
        horodaté de la transcription. Les mentions « à vérifier » signalent les
        informations dont une partie n'a pas été retrouvée telle quelle dans le
        passage cité.
      </p>
      {rapport.notes.map((note) => (
        <div className="message message-alerte" key={note}>{note}</div>
      ))}
      <p className="petit doux" style={{ marginBottom: 0 }}>
        Thèmes : {rapport.topics} · Décisions : {rapport.decisions} · Actions : {rapport.actions}
        {' '}· Questions ouvertes : {rapport.open_questions} · Points de vigilance : {rapport.risks}
      </p>
    </div>
  )
}

export default function Reunion({ config }: { config: AppConfig | null }) {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [rapport, setRapport] = useState<ReportSummary | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [editionTitre, setEditionTitre] = useState(false)
  const [titre, setTitre] = useState('')
  const [date, setDate] = useState('')
  const [contexte, setContexte] = useState('')
  const [ongletTranscription, setOngletTranscription] = useState(false)

  const charger = useCallback(async () => {
    try {
      const charge = await api.getMeeting(id)
      setMeeting(charge)
      setTitre(charge.title); setDate(charge.meeting_date); setContexte(charge.context ?? '')
      if (charge.stages.find((s) => s.stage === 'analysis')?.status === 'done') {
        try { setRapport(await api.getReport(id)) } catch { setRapport(null) }
      } else {
        setRapport(null)
      }
      setErreur(null)
    } catch (e) {
      setErreur((e as Error).message)
    }
  }, [id])

  useEffect(() => { void charger() }, [charger])

  // Suivi en direct pendant le traitement.
  useEffect(() => {
    if (!meeting || (meeting.status !== 'processing' && !meeting.is_running)) return
    const minuteur = setInterval(() => { void charger() }, 1500)
    return () => clearInterval(minuteur)
  }, [meeting, charger])

  const relancer = async (stage?: string) => {
    setOccupe(true); setErreur(null)
    try {
      await api.process(id, stage)
      await charger()
    } catch (e) {
      setErreur((e as Error).message)
    } finally {
      setOccupe(false)
    }
  }

  const enregistrerInfos = async () => {
    setOccupe(true)
    try {
      await api.updateMeeting(id, {
        title: titre.trim() || undefined,
        meeting_date: date || undefined,
        context: contexte,
      })
      setEditionTitre(false)
      await charger()
    } catch (e) {
      setErreur((e as Error).message)
    } finally {
      setOccupe(false)
    }
  }

  const supprimer = async () => {
    if (!window.confirm(
      "Supprimer définitivement cette réunion ?\n\nL'audio, la transcription et les " +
      'documents seront effacés du disque. Cette action est irréversible.',
    )) return
    try {
      await api.deleteMeeting(id)
      navigate('/')
    } catch (e) {
      setErreur((e as Error).message)
    }
  }

  if (erreur && !meeting) return <div className="message message-erreur">{erreur}</div>
  if (!meeting) return <div className="chargement">Chargement…</div>

  const analyseFaite = meeting.stages.find((s) => s.stage === 'analysis')?.status === 'done'
  const documentsAFaire = meeting.stages.find((s) => s.stage === 'documents')?.status === 'pending'
  const peutTraiter = meeting.status !== 'processing' && !meeting.is_running

  return (
    <>
      <div className="fil"><Link to="/">← Tableau de bord</Link></div>

      <div className="carte-titre">
        <h1 style={{ margin: 0 }}>{meeting.title}</h1>
        <span className={`badge ${
          meeting.status === 'completed' ? 'badge-ok'
          : meeting.status === 'failed' ? 'badge-erreur'
          : meeting.status === 'processing' ? 'badge-encours' : 'badge-alerte'}`}>
          {STATUS_LABELS[meeting.status] ?? meeting.status}
        </span>
      </div>
      <p className="doux petit">
        {formatDate(meeting.meeting_date)}
        {meeting.duration_seconds > 0 && <> · {meeting.duration_label}</>}
        {meeting.language && <> · langue détectée : {meeting.language}</>}
        {meeting.documents_generated_at && <> · documents générés le {formatDateTime(meeting.documents_generated_at)}</>}
      </p>

      {erreur && <div className="message message-erreur">{erreur}</div>}
      {meeting.error && meeting.status === 'failed' && (
        <div className="message message-erreur"><strong>Le traitement a échoué</strong>{meeting.error}</div>
      )}
      {meeting.status === 'ready' && config && !config.asr_ready && (
        <div className="message message-alerte">
          <strong>Traitement indisponible</strong>{config.asr_problem}
        </div>
      )}

      <div className="actions" style={{ marginBottom: 18 }}>
        {(meeting.status === 'ready' || meeting.status === 'failed') && (
          <button className="btn btn-principal" onClick={() => void relancer()} disabled={occupe || !peutTraiter}>
            {meeting.status === 'failed' ? 'Reprendre le traitement' : 'Lancer le traitement'}
          </button>
        )}
        {analyseFaite && (
          <button className="btn" onClick={() => void relancer('documents')} disabled={occupe || !peutTraiter}>
            Régénérer les documents
          </button>
        )}
        {meeting.has_transcript && (
          <button className="btn" onClick={() => void relancer('analysis')} disabled={occupe || !peutTraiter}>
            Relancer l'analyse (sans retranscrire)
          </button>
        )}
        {meeting.has_transcript && (
          <button className="btn" onClick={() => setOngletTranscription((v) => !v)}>
            {ongletTranscription ? 'Masquer la transcription' : 'Voir et corriger la transcription'}
          </button>
        )}
        <button className="btn" onClick={() => setEditionTitre((v) => !v)}>Modifier les informations</button>
        <button className="btn btn-danger" onClick={() => void supprimer()}>Supprimer</button>
      </div>

      {documentsAFaire && meeting.has_transcript && analyseFaite === false && meeting.status !== 'processing' && (
        <div className="message message-info">
          La transcription a été corrigée. Relancez l'analyse pour que le rapport et
          la synthèse tiennent compte de vos corrections — l'audio ne sera pas retranscrit.
        </div>
      )}

      {editionTitre && (
        <div className="carte">
          <h2>Informations de la réunion</h2>
          <label className="champ">
            <span>Titre</span>
            <input type="text" value={titre} onChange={(e) => setTitre(e.target.value)} />
            <small>Les noms des fichiers générés suivront ce titre.</small>
          </label>
          <label className="champ">
            <span>Date</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="champ">
            <span>Contexte</span>
            <textarea value={contexte} onChange={(e) => setContexte(e.target.value)} />
          </label>
          <div className="actions">
            <button className="btn btn-principal" onClick={() => void enregistrerInfos()} disabled={occupe}>
              Enregistrer
            </button>
            <button className="btn" onClick={() => setEditionTitre(false)}>Annuler</button>
          </div>
        </div>
      )}

      {meeting.status !== 'completed' && (
        <Etapes meeting={meeting} onRelancer={(stage) => void relancer(stage)} />
      )}

      <h2 style={{ marginTop: 26 }}>Livrables</h2>
      <div className="grille grille-3">
        {meeting.deliverables.map((livrable) => (
          <CarteLivrable key={livrable.kind} meeting={meeting} livrable={livrable} />
        ))}
      </div>

      {rapport && <div style={{ marginTop: 18 }}><Fiabilite rapport={rapport} /></div>}

      {meeting.status === 'completed' && (
        <details style={{ marginTop: 18 }}>
          <summary className="doux petit" style={{ cursor: 'pointer' }}>
            Voir le détail du traitement
          </summary>
          <div style={{ marginTop: 12 }}>
            <Etapes meeting={meeting} onRelancer={(stage) => void relancer(stage)} />
          </div>
        </details>
      )}

      {ongletTranscription && meeting.has_transcript && (
        <div style={{ marginTop: 18 }}>
          <Transcription meetingId={id} onEnregistre={() => void charger()} />
        </div>
      )}
    </>
  )
}
