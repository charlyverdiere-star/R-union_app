import type { Meeting, Stage } from '../lib/types'

const SYMBOLES: Record<Stage['status'], string> = {
  pending: '·', running: '…', done: '✓', failed: '!',
}

const DESCRIPTIONS: Record<Stage['stage'], string> = {
  audio: "Vérification du fichier, archivage de l'original et découpage si la réunion est longue.",
  transcription: "Conversion de l'audio en texte horodaté, avec repérage des locuteurs si le moteur le permet.",
  analysis: "Repérage des thèmes, décisions, actions et questions, puis vérification de chaque information contre la transcription.",
  documents: 'Rédaction du rapport complet et de la synthèse, en Word et en PDF.',
}

export default function Etapes({
  meeting, onRelancer,
}: {
  meeting: Meeting
  onRelancer: (stage?: string) => void
}) {
  return (
    <div className="carte">
      <div className="carte-titre">
        <h2>Traitement</h2>
        <span className="doux petit">{Math.round(meeting.overall_progress * 100)} %</span>
      </div>

      <ul className="etapes">
        {meeting.stages.map((etape) => (
          <li key={etape.stage} className={`etape etape-${etape.status}`}>
            <span className="etape-puce" aria-hidden="true">{SYMBOLES[etape.status]}</span>
            <div className="etape-corps">
              <div className="etape-nom">{etape.label}</div>
              <div className="petit doux">
                {etape.message || DESCRIPTIONS[etape.stage]}
              </div>
              {etape.status === 'running' && (
                <div className="jauge">
                  <div className="jauge-barre" style={{ width: `${Math.round(etape.progress * 100)}%` }} />
                </div>
              )}
              {etape.error && (
                <div className="message message-erreur" style={{ marginTop: 8, marginBottom: 0 }}>
                  <strong>Échec de cette étape</strong>
                  {etape.error}
                  <div className="actions" style={{ marginTop: 10 }}>
                    <button className="btn btn-petit" onClick={() => onRelancer()}>
                      Reprendre où ça s'est arrêté
                    </button>
                    <button className="btn btn-petit" onClick={() => onRelancer(etape.stage)}>
                      Refaire cette étape
                    </button>
                  </div>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
