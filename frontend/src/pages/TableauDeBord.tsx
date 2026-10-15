import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Meeting } from '../lib/types'
import { STATUS_LABELS, formatBytes, formatDate, formatDateTime } from '../lib/format'

function BadgeStatut({ meeting }: { meeting: Meeting }) {
  const classe =
    meeting.status === 'completed' ? 'badge-ok'
    : meeting.status === 'failed' ? 'badge-erreur'
    : meeting.status === 'processing' ? 'badge-encours'
    : 'badge-alerte'
  const texte = meeting.status === 'processing'
    ? `${STATUS_LABELS[meeting.status]} — ${Math.round(meeting.overall_progress * 100)} %`
    : STATUS_LABELS[meeting.status] ?? meeting.status
  return <span className={`badge ${classe}`}>{texte}</span>
}

export default function TableauDeBord() {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null)
  const [recherche, setRecherche] = useState('')
  const [statut, setStatut] = useState('')
  const [depuis, setDepuis] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)
  const dernier = useRef(0)

  const charger = useCallback(async () => {
    const appel = ++dernier.current
    try {
      const liste = await api.listMeetings({
        query: recherche.trim() || undefined,
        status: statut || undefined,
        date_from: depuis || undefined,
      })
      if (appel === dernier.current) { setMeetings(liste); setErreur(null) }
    } catch (e) {
      if (appel === dernier.current) setErreur((e as Error).message)
    }
  }, [recherche, statut, depuis])

  // Recherche différée : on n'appelle pas le serveur à chaque frappe.
  useEffect(() => {
    const minuteur = setTimeout(() => { void charger() }, 250)
    return () => clearTimeout(minuteur)
  }, [charger])

  // Rafraîchissement tant qu'un traitement est en cours.
  useEffect(() => {
    if (!meetings?.some((m) => m.status === 'processing')) return
    const minuteur = setInterval(() => { void charger() }, 2500)
    return () => clearInterval(minuteur)
  }, [meetings, charger])

  const supprimer = async (meeting: Meeting) => {
    const confirme = window.confirm(
      `Supprimer définitivement « ${meeting.title} » ?\n\n` +
      "L'enregistrement audio, la transcription et les documents générés seront " +
      'effacés du disque. Cette action est irréversible.',
    )
    if (!confirme) return
    try {
      await api.deleteMeeting(meeting.id)
      await charger()
    } catch (e) {
      setErreur((e as Error).message)
    }
  }

  return (
    <>
      <div className="carte-titre">
        <h1>Mes réunions</h1>
      </div>

      {erreur && <div className="message message-erreur">{erreur}</div>}

      <div className="barre-outils">
        <input
          type="search" placeholder="Rechercher un titre ou un mot prononcé…"
          value={recherche} onChange={(e) => setRecherche(e.target.value)}
          aria-label="Rechercher une réunion"
        />
        <select value={statut} onChange={(e) => setStatut(e.target.value)} aria-label="Filtrer par état">
          <option value="">Tous les états</option>
          <option value="completed">Terminées</option>
          <option value="processing">En traitement</option>
          <option value="ready">Prêtes à traiter</option>
          <option value="failed">En échec</option>
          <option value="created">Créées</option>
        </select>
        <input
          type="date" value={depuis} onChange={(e) => setDepuis(e.target.value)}
          aria-label="À partir du" style={{ width: 'auto' }}
        />
        {(recherche || statut || depuis) && (
          <button className="btn btn-petit" onClick={() => { setRecherche(''); setStatut(''); setDepuis('') }}>
            Effacer les filtres
          </button>
        )}
      </div>

      <div className="carte">
        {meetings === null ? (
          <div className="chargement">Chargement…</div>
        ) : meetings.length === 0 ? (
          <div className="vide">
            <p style={{ fontSize: '1.05rem' }}>
              {recherche || statut || depuis
                ? 'Aucune réunion ne correspond à ces critères.'
                : "Vous n'avez pas encore de réunion enregistrée."}
            </p>
            <Link className="btn btn-principal" to="/nouvelle">Enregistrer une réunion</Link>
          </div>
        ) : (
          <ul className="liste">
            {meetings.map((m) => (
              <li className="ligne-reunion" key={m.id}>
                <div className="ligne-principale">
                  <Link className="ligne-titre" to={`/reunion/${m.id}`}>{m.title}</Link>
                  <div className="ligne-meta">
                    {formatDate(m.meeting_date)}
                    {m.duration_seconds > 0 && <> · {m.duration_label}</>}
                    {m.audio_bytes > 0 && <> · {formatBytes(m.audio_bytes)}</>}
                    {m.documents_generated_at && <> · documents du {formatDateTime(m.documents_generated_at)}</>}
                  </div>
                </div>
                <BadgeStatut meeting={m} />
                <div className="actions">
                  {m.deliverables.filter((d) => d.available).map((d) => (
                    <button
                      key={d.kind} className="btn btn-petit"
                      onClick={() => void api.download(
                        m.id, d.kind,
                        d.kind === 'audio' ? d.formats[0] : 'docx',
                        d.filename,
                      )}
                    >
                      {d.kind === 'audio' ? 'Audio' : d.kind === 'report' ? 'Rapport' : 'Synthèse'}
                    </button>
                  ))}
                  <Link className="btn btn-petit" to={`/reunion/${m.id}`}>Ouvrir</Link>
                  <button className="btn btn-petit btn-danger" onClick={() => void supprimer(m)}>
                    Supprimer
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
