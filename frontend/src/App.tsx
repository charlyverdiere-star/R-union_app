import { useCallback, useEffect, useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import { api, getAuthToken, setAuthToken } from './lib/api'
import type { AppConfig } from './lib/types'
import TableauDeBord from './pages/TableauDeBord'
import NouvelleReunion from './pages/NouvelleReunion'
import Reunion from './pages/Reunion'

function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="var(--accent)" />
      <path d="M32 14a7 7 0 0 1 7 7v12a7 7 0 0 1-14 0V21a7 7 0 0 1 7-7z" fill="var(--accent-contraste)" />
      <path d="M20 30v3a12 12 0 0 0 24 0v-3" fill="none" stroke="var(--accent-contraste)" strokeWidth="3.4" strokeLinecap="round" />
      <path d="M32 45v6" stroke="var(--accent-contraste)" strokeWidth="3.4" strokeLinecap="round" />
    </svg>
  )
}

/** Bandeau de configuration : prévient avant qu'un traitement n'échoue. */
function BandeauConfig({ config }: { config: AppConfig }) {
  const enTest = config.asr_is_test || config.llm_is_test
  if (enTest) {
    return (
      <div className="message message-erreur">
        <strong>Mode test activé</strong>
        Les transcriptions et les documents produits ne proviennent pas de l'audio réel.
        Ce mode sert aux tests automatisés : ne vous fiez pas aux documents générés.
      </div>
    )
  }
  const problemes = [config.asr_problem, config.llm_problem].filter(Boolean) as string[]
  if (problemes.length === 0) return null
  return (
    <div className="message message-alerte">
      <strong>Configuration incomplète</strong>
      {problemes.map((p) => <div key={p}>{p}</div>)}
      <div className="petit" style={{ marginTop: 6 }}>
        Renseignez les valeurs manquantes dans le fichier <code>.env</code>, puis redémarrez
        l'application. L'enregistrement reste possible : le traitement pourra être lancé plus tard.
      </div>
    </div>
  )
}

function DemandeJeton({ onValide }: { onValide: () => void }) {
  const [valeur, setValeur] = useState(getAuthToken())
  return (
    <main>
      <div className="carte" style={{ maxWidth: 460, margin: '40px auto' }}>
        <h1>Accès protégé</h1>
        <p className="doux">
          Cette installation exige un jeton d'accès. Il est défini par la variable
          <code> AUTH_TOKEN</code> du fichier <code>.env</code>.
        </p>
        <label className="champ">
          <span>Jeton d'accès</span>
          <input
            type="password" value={valeur} autoFocus
            onChange={(e) => setValeur(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { setAuthToken(valeur); onValide() } }}
          />
        </label>
        <button className="btn btn-principal" onClick={() => { setAuthToken(valeur); onValide() }}>
          Valider
        </button>
      </div>
    </main>
  )
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [besoinJeton, setBesoinJeton] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)

  const charger = useCallback(async () => {
    try {
      const chargee = await api.config()
      setConfig(chargee)
      setErreur(null)
      if (chargee.auth_required) {
        try {
          await api.listMeetings()
          setBesoinJeton(false)
        } catch {
          setBesoinJeton(true)
        }
      }
    } catch (e) {
      setErreur(
        "Le serveur ne répond pas. Vérifiez qu'il est démarré, puis rechargez la page.",
      )
    }
  }, [])

  useEffect(() => { void charger() }, [charger])

  return (
    <div className="app">
      <header className="entete">
        <div className="entete-inner">
          <Link to="/" className="marque">
            <Logo />
            Assistant de réunions
          </Link>
          <div className="entete-actions">
            <Link to="/nouvelle" className="btn btn-principal">Nouvelle réunion</Link>
          </div>
        </div>
      </header>

      {besoinJeton ? (
        <DemandeJeton onValide={() => { setBesoinJeton(false); void charger() }} />
      ) : (
        <main>
          {erreur && <div className="message message-erreur"><strong>Serveur injoignable</strong>{erreur}</div>}
          {config && <BandeauConfig config={config} />}
          <Routes>
            <Route path="/" element={<TableauDeBord />} />
            <Route path="/nouvelle" element={<NouvelleReunion config={config} />} />
            <Route path="/reunion/:id" element={<Reunion config={config} />} />
            <Route path="*" element={
              <div className="vide">
                <p>Cette page n'existe pas.</p>
                <Link className="btn" to="/">Retour au tableau de bord</Link>
              </div>
            } />
          </Routes>
        </main>
      )}

      <footer className="pied">
        Les enregistrements, transcriptions et documents sont stockés sur cette machine.
        Pensez à informer les participants avant d'enregistrer une réunion.
      </footer>
    </div>
  )
}
