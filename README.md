# Assistant de réunions

Enregistrez une réunion, récupérez trois fichiers : **l'audio original**, un
**rapport complet** et une **synthèse** d'une page. Tout tourne sur votre
machine ; seuls l'audio et la transcription partent vers les prestataires d'IA
que vous choisissez — ou vers personne du tout, si vous retenez le mode local.

---

## Sommaire

1. [Ce que fait l'application](#1-ce-que-fait-lapplication)
2. [Installation](#2-installation)
3. [Premier usage](#3-premier-usage)
4. [Configuration](#4-configuration)
5. [Choix techniques et comparatif](#5-choix-techniques-et-comparatif)
6. [Coûts](#6-coûts)
7. [Confidentialité et sécurité](#7-confidentialité-et-sécurité)
8. [Sauvegarde et restauration](#8-sauvegarde-et-restauration)
9. [Architecture du code](#9-architecture-du-code)
10. [Tests](#10-tests)
11. [Limites connues](#11-limites-connues)
12. [Dépannage](#12-dépannage)

---

## 1. Ce que fait l'application

Le parcours tient en six gestes :

1. **Nouvelle réunion** → titre, date, contexte facultatif.
2. **Démarrer l'enregistrement** → un seul bouton. Pause et reprise possibles
   à volonté, sans jamais produire plusieurs fichiers.
3. **Arrêter** → l'audio est déjà intégralement sur le disque.
4. **Lancer le traitement** → préparation de l'audio, transcription, analyse,
   génération des documents, avec une progression lisible étape par étape.
5. **Récupérer les trois livrables**, téléchargeables séparément.
6. **Relire la transcription** si besoin, la corriger, régénérer les documents
   sans repayer la transcription.

### Les trois livrables

| | Fichier | Contenu |
|---|---|---|
| **1** | `AAAA-MM-JJ_Titre_audio.ogg` | L'enregistrement original, sans réencodage. |
| **2** | `AAAA-MM-JJ_Titre_rapport-complet.docx` et `.pdf` | Compte rendu détaillé en sept sections : informations générales, résumé exécutif, compte rendu par thème, décisions, plan d'action, questions ouvertes, conclusion. |
| **3** | `AAAA-MM-JJ_Titre_synthese.docx` et `.pdf` | L'essentiel en deux minutes : décisions, actions, points de vigilance, questions en suspens. |

DOCX et PDF sont deux formats de diffusion du **même** document ; ce ne sont
pas des livrables supplémentaires.

### Ce qui distingue ce compte rendu d'un résumé automatique ordinaire

Chaque décision, chaque chiffre et chaque action du rapport **cite le passage
de la transcription dont il provient**, avec son horodatage. Un contrôle
automatique, sans intervention d'un modèle de langage, vérifie ensuite que les
chiffres, dates, acronymes et noms propres de chaque énoncé figurent bien dans
le passage cité :

- information retrouvée mot pour mot → publiée normalement ;
- information partiellement retrouvée → publiée avec la mention
  **« [à vérifier] »** ;
- information ne citant aucun passage valide → **retirée des documents**, et
  le nombre d'éléments écartés est indiqué en tête du rapport.

Les responsables d'action et les personnes concernées par une décision sont
soumis au même contrôle : une action attribuée à quelqu'un que le passage
cité ne nomme pas est signalée.

---

## 2. Installation

### Prérequis

| Outil | Version | Rôle |
|---|---|---|
| Python | 3.11 ou plus | Serveur, traitement, génération des documents |
| Node.js | 18 ou plus | Compilation de l'interface (une seule fois) |
| ffmpeg | 5 ou plus | Lecture, remise en conteneur et découpage de l'audio |

Vérifiez ffmpeg avec `ffmpeg -version`. S'il manque :
`brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Debian/Ubuntu),
`winget install ffmpeg` (Windows).

### Mise en place

```bash
git clone <votre-dépôt> meeting-assistant
cd meeting-assistant

# 1. Environnement Python
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e .

# 2. Interface
cd frontend && npm ci && npm run build && cd ..

# 3. Configuration
cp .env.example .env
#    puis ouvrez .env et renseignez au minimum :
#      DEEPGRAM_API_KEY=...    (transcription)
#      ANTHROPIC_API_KEY=...   (analyse et rédaction)
```

### Lancement

```bash
source .venv/bin/activate
python -m backend.app.cli
```

L'application s'ouvre sur <http://127.0.0.1:8420>.

> **Important** : l'adresse doit rester `127.0.0.1` ou `localhost`. Les
> navigateurs n'autorisent l'accès au microphone que sur une origine
> considérée comme sûre (adresse locale ou HTTPS).

Options utiles : `--port 9000`, `--no-browser`, `--reload` (développement).

---

## 3. Premier usage

1. Ouvrez l'application. Si une clé manque, un bandeau le signale en haut de
   page **avant** que vous n'enregistriez quoi que ce soit.
2. Cliquez sur **Nouvelle réunion**, saisissez un titre.
3. Cliquez sur **Démarrer l'enregistrement**. Le navigateur demande
   l'autorisation d'accéder au microphone : acceptez.
4. Vérifiez que la barre de niveau bouge quand quelqu'un parle. Si rien ne
   bouge pendant une quinzaine de secondes, l'application vous avertit.
5. À la fin, **Arrêter**, puis **Lancer le traitement**.
6. Les trois livrables apparaissent une fois le traitement terminé.

### Voir à quoi ressemblent les documents, sans clé API

```bash
python scripts/generer_exemples.py
```

Quatre documents sont écrits dans `exemples/`, construits à partir du jeu de
données de test. Ils portent la mention « MODE TEST » : ils ne proviennent pas
d'un enregistrement réel, mais la mise en forme, la structure et le niveau de
détail sont ceux que produit l'application.

### Importer un enregistrement existant

Sur l'écran **Nouvelle réunion**, bouton **Importer un fichier audio** :
mêmes traitements, mêmes livrables. Formats acceptés : tout ce que ffmpeg sait
lire (WAV, MP3, M4A, OGG, FLAC, WebM, et les pistes audio de fichiers vidéo).

### Si l'enregistrement est interrompu

L'audio part vers le serveur au fil de l'enregistrement, par tranches de cinq
secondes. Si le navigateur plante, si l'onglet est fermé ou si la machine
s'éteint, **tout ce qui a été transmis est déjà sur le disque**. La réunion
apparaît dans la liste ; lancez le traitement depuis sa fiche. Un fichier
tronqué est automatiquement réparé au moment de l'archivage.

---

## 4. Configuration

Toute la configuration tient dans le fichier `.env`, documenté ligne par ligne
dans [`.env.example`](.env.example). Les réglages les plus structurants :

| Variable | Défaut | Effet |
|---|---|---|
| `ASR_PROVIDER` | `deepgram` | Moteur de transcription (voir §5). |
| `LLM_MODEL` | `claude-opus-5-5` | Modèle d'analyse et de rédaction. |
| `LLM_EFFORT` | `high` | Profondeur de raisonnement : `low` à `max`. |
| `ASR_VOCABULARY` | *(vide)* | Noms propres et termes métier transmis au moteur. **Le réglage le plus rentable** : il corrige les noms de collègues, de produits et les acronymes. |
| `ARCHIVE_FORMAT` | `original` | `original` (recommandé), `flac` ou `wav`. |
| `CHUNK_TARGET_SECONDS` | `600` | Durée des segments envoyés au moteur, quand le découpage est nécessaire. |
| `RETENTION_DAYS` | `0` | Suppression automatique au-delà de N jours. `0` = conservation illimitée. |
| `AUTH_TOKEN` | *(vide)* | Jeton exigé dans l'en-tête `X-Auth-Token`. Indispensable si vous changez `HOST`. |
| `DATA_DIR` | `./data` | Emplacement de toutes les données. |

### Pourquoi `ARCHIVE_FORMAT=original` par défaut

Le cahier des charges suggérait WAV ou FLAC pour l'archivage. Dans les faits,
le navigateur produit déjà de l'Opus compressé : le convertir en FLAC ne
restaure aucune information perdue, et multiplie la taille du fichier par cinq
environ. L'application se contente donc de **remettre le flux dans un
conteneur propre, sans le décoder ni le réencoder** — ce qui répare au passage
un enregistrement interrompu et rend le fichier navigable dans un lecteur.
C'est à la fois plus fidèle (aucun traitement du signal) et plus économe.

Si vous importez un fichier déjà en WAV ou en FLAC, il est conservé tel quel.
`ARCHIVE_FORMAT=flac` reste disponible si votre politique d'archivage l'exige.

---

## 5. Choix techniques et comparatif

### Architecture : application locale servie par un petit serveur

Trois options ont été pesées.

| Option | Avantages | Inconvénients | Verdict |
|---|---|---|---|
| **PWA pure** (tout dans le navigateur) | Installation nulle | Impossible de protéger une clé API ; des heures d'audio dans IndexedDB sont fragiles ; génération DOCX/PDF limitée | Écartée |
| **Application de bureau** (Electron/Tauri) | Intégration système | Empaquetage et mises à jour lourds à maintenir, pour un gain réel faible ici | Écartée |
| **Serveur local + interface web** | Les clés restent côté serveur ; les fichiers sont des fichiers ordinaires sur votre disque ; ffmpeg, `python-docx` et ReportLab disponibles ; un seul `pip install` ; déployable plus tard sur un serveur d'équipe sans rien réécrire | Il faut lancer une commande | **Retenue** |

L'interface est servie par le même processus que l'API : une seule origine,
pas de configuration CORS, et le contexte sécurisé nécessaire au microphone.
Le manifeste PWA est fourni : le navigateur propose d'installer l'application.

### Moteur de transcription

Le moteur est enfichable (`ASR_PROVIDER`). Quatre implémentations sont
fournies, plus un moteur de rejeu réservé aux tests.

| Moteur | Français | Diarisation | Réunions longues | Confidentialité | Coût indicatif |
|---|---|---|---|---|---|
| **Deepgram `nova-3`** *(défaut)* | bon | **native** | fichier entier accepté | audio envoyé à Deepgram | ~0,25 $/h |
| AssemblyAI | bon | **native** | fichier entier accepté | audio envoyé à AssemblyAI | ~0,27 $/h |
| OpenAI `whisper-1` | bon | non | découpage imposé (25 Mo) | audio envoyé à OpenAI | ~0,36 $/h |
| **`local-whisper`** | bon à très bon selon le modèle | non | sans limite | **rien ne sort de la machine** | 0 € (temps machine) |

**Deepgram par défaut** parce qu'il cumule les trois propriétés qui comptent
pour des réunions professionnelles : identification native des locuteurs,
acceptation d'un fichier de plusieurs heures en un seul appel, et le coût le
plus bas du lot. Le premier point n'est pas un détail : les moteurs qui
imposent un découpage perdent la correspondance des locuteurs d'un segment à
l'autre. L'application exploite cette capacité — **elle ne découpe l'audio que
lorsque le moteur l'impose**.

**Le mode local mérite un examen sérieux** si la confidentialité prime. Sur un
processeur seul, comptez de une à trois fois la durée de la réunion pour le
modèle `large-v3` ; un GPU ramène cela à une fraction. Les modèles `small` ou
`medium` sont nettement plus rapides, au prix d'une dégradation sur les noms
propres et les acronymes — exactement ce que `ASR_VOCABULARY` ne peut que
partiellement compenser. Pour une réunion d'une heure sur un portable récent
sans GPU, prévoyez un traitement en arrière-plan plutôt qu'une attente devant
l'écran.

### Analyse et rédaction

Claude, via l'API Anthropic. Deux mécanismes y sont systématiquement
employés :

- **sorties structurées** : le modèle remplit un schéma Pydantic, il ne
  rédige pas du JSON dans du texte libre. Une réponse non conforme est
  rejetée par le SDK, pas silencieusement mal interprétée ;
- **mise en cache du préfixe** : les consignes et la transcription sont
  envoyées comme préfixe mis en cache. La rédaction thème par thème ne
  repaie donc pas la transcription à chaque appel.

`claude-opus-5-5` est le défaut. `claude-sonnet-5-5` divise le coût par deux
environ, `claude-haiku-5-5` par quarante ; les deux restent utilisables, avec
une perte de finesse sur les réunions longues ou touffues. Un seul changement
dans `.env` suffit.

### Pourquoi une seule passe ne suffisait pas

L'analyse se fait en trois temps, et ce découpage est la raison principale de
la fidélité du résultat :

1. **extraction** fenêtre par fenêtre (~2 000 mots) : le modèle ne voit jamais
   toute la réunion d'un coup, ce qui l'empêche de résumer au lieu d'extraire,
   et chaque élément produit cite ses segments d'origine ;
2. **consolidation** : les éléments des différentes fenêtres sont regroupés.
   Le modèle ne peut que *regrouper et reformuler* — les responsables,
   échéances et priorités sont repris **par le code** depuis les éléments
   d'origine, si bien qu'aucun ne peut apparaître à cette étape ;
3. **rédaction** : un appel par thème, limité aux passages concernés.

Le rattachement des informations aux thèmes est lui aussi **calculé** (par
recouvrement de segments), pas demandé au modèle : il est donc reproductible.

### Génération des documents

`python-docx` pour le Word, ReportLab pour le PDF. Les deux rendus partent du
**même objet `ReportModel`** : les documents ne peuvent pas se contredire, et
un test le vérifie explicitement. Convertir le DOCX en PDF via LibreOffice
aurait imposé une dépendance de plusieurs centaines de mégaoctets pour un
résultat moins maîtrisé.

---

## 6. Coûts

Ordres de grandeur pour **une heure de réunion** (environ 9 000 mots) :

| Poste | Opus 5.5 | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|---|
| Transcription (Deepgram) | ~0,25 $ | ~0,25 $ | ~0,25 $ |
| Analyse et rédaction | ~0,70 $ | ~0,30 $ | ~0,02 $ |
| **Total par heure** | **~0,95 $** | **~0,55 $** | **~0,27 $** |
| **Total avec transcription locale** | ~0,70 $ | ~0,30 $ | ~0,02 $ |

Soit, pour **quatre heures de réunion par semaine** : de 4 $ à 16 $ par mois
selon la combinaison retenue.

Ces montants sont des **estimations calculées à partir des tarifs publics**,
et non des mesures : l'environnement de développement n'avait pas accès aux
API des prestataires (voir §11). Vérifiez les tarifs en vigueur avant de vous
engager. La consommation réelle est journalisée à chaque analyse — cherchez
« Analyse terminée » dans la sortie du serveur pour connaître le nombre exact
de jetons consommés.

Trois leviers si le coût devient un sujet : basculer `LLM_MODEL` sur Sonnet ou
Haiku, abaisser `LLM_EFFORT`, ou passer la transcription en local.

---

## 7. Confidentialité et sécurité

### Où sont les données

Tout est dans `DATA_DIR` (par défaut `./data`), sur votre machine :

```
data/
├── app.db                       base SQLite : métadonnées et index de recherche
└── meetings/<identifiant>/
    ├── audio/
    │   ├── incoming.bin         flux reçu du navigateur
    │   ├── archive.ogg          LIVRABLE 1 — l'enregistrement original
    │   ├── work-16k.wav         fichier de travail, dérivé
    │   └── chunks/              segments et transcriptions en cache
    ├── transcript.json          transcription horodatée
    ├── analysis/                données d'analyse intermédiaires
    └── documents/               LIVRABLES 2 et 3 (DOCX et PDF)
```

Rien n'est envoyé ailleurs que vers les prestataires listés ci-dessous.

### Ce qui sort de votre machine

| Destinataire | Quand | Ce qui est transmis |
|---|---|---|
| Deepgram, AssemblyAI ou OpenAI | pendant la transcription | **le fichier audio** de la réunion |
| Anthropic | pendant l'analyse | **la transcription** (texte), pas l'audio |

Avec `ASR_PROVIDER=local-whisper`, l'audio ne quitte jamais la machine ; seule
la transcription part vers Anthropic. Il n'existe à ce jour aucune
configuration entièrement hors ligne : l'étape de rédaction appelle un modèle
distant. L'interface affiche en permanence quels services sont utilisés —
**l'application ne présente jamais un traitement comme local s'il ne l'est
pas**.

Chaque prestataire a sa propre politique de conservation et d'usage des
données transmises. Consultez-la avant d'enregistrer des échanges sensibles,
et vérifiez si une option de non-conservation (*zero retention*) est
disponible sur votre contrat.

### Protection des secrets

Les clés API sont lues dans `.env`, qui est exclu du dépôt par `.gitignore`.
Elles ne transitent jamais vers le navigateur : le point d'entrée
`/api/config` ne renvoie que des booléens et des noms de fournisseurs. Un test
automatisé vérifie qu'aucune clé ne fuit dans cette réponse.

### Accès réseau

Par défaut le serveur n'écoute que sur `127.0.0.1` : il n'est joignable que
depuis votre machine. **Si vous changez `HOST`, définissez impérativement
`AUTH_TOKEN`** et placez un terminaison TLS devant l'application : le
microphone n'est de toute façon accessible qu'en HTTPS hors adresse locale.

### Suppression et conservation

Supprimer une réunion depuis l'interface efface sa ligne en base, son index de
recherche **et l'intégralité de son dossier** : audio, transcription, analyses
et documents. `RETENTION_DAYS` automatise cette suppression au-delà d'un
certain âge ; déclenchez-la avec :

```bash
curl -X POST http://127.0.0.1:8420/api/maintenance/retention
```

(à placer dans une tâche planifiée si vous souhaitez l'automatiser).

### Information des participants

L'enregistrement d'une réunion professionnelle est encadré par la
réglementation applicable à votre organisation. Informez les participants et
obtenez les autorisations nécessaires. L'application rappelle ce point avant
chaque enregistrement, mais **elle ne vous dispense d'aucune obligation**.

---

## 8. Sauvegarde et restauration

Toutes les données tiennent dans un seul répertoire. La sauvegarde consiste
donc à le copier.

### Sauvegarde

```bash
# Arrêtez le serveur, ou utilisez la commande SQLite ci-dessous.
tar czf sauvegarde-reunions-$(date +%F).tar.gz -C /chemin/vers data/
```

Serveur en fonctionnement, sauvegardez la base proprement avant d'archiver
les fichiers — SQLite est en mode WAL, une copie brute pourrait être
incohérente :

```bash
sqlite3 data/app.db ".backup 'data/app-sauvegarde.db'"
tar czf sauvegarde-reunions-$(date +%F).tar.gz -C /chemin/vers data/
```

### Restauration

```bash
tar xzf sauvegarde-reunions-2026-10-15.tar.gz -C /chemin/vers/
# si vous aviez utilisé .backup :
mv data/app-sauvegarde.db data/app.db
python -m backend.app.cli
```

### Vérification d'une sauvegarde

```bash
sqlite3 data/app.db "SELECT COUNT(*) FROM meetings;"
ls data/meetings | wc -l        # doit correspondre
```

### Reconstruction de l'index de recherche

L'index plein texte se reconstruit en relançant l'étape de transcription
(bouton **Relancer l'analyse** depuis la fiche d'une réunion) : la
transcription existante est réindexée sans nouvel appel au moteur.

### Que faut-il absolument conserver

| Fichier | Indispensable ? |
|---|---|
| `data/app.db` | **Oui** — sans lui, les réunions n'apparaissent plus. |
| `audio/archive.*` | **Oui** — c'est le livrable 1, irremplaçable. |
| `transcript.json` | **Oui** — évite de repayer la transcription. |
| `documents/` | Non — régénérables à partir de la transcription. |
| `analysis/`, `audio/work-16k.wav`, `audio/chunks/` | Non — fichiers de travail. |

Pour une sauvegarde compacte, excluez `work-16k.wav` et `chunks/` :

```bash
tar czf sauvegarde.tar.gz --exclude='work-16k.wav' --exclude='chunks' -C /chemin data/
```

---

## 9. Architecture du code

```
backend/app/
├── cli.py               point d'entrée en ligne de commande
├── main.py              application FastAPI, service de l'interface
├── config.py            configuration et diagnostic (aucun secret exposé)
├── models.py            modèles du domaine — tout élément porte sa source
├── db.py                SQLite + recherche plein texte FTS5
├── storage.py           organisation des fichiers, nommage des livrables
├── api/                 routes HTTP et objets d'échange
├── pipeline/
│   ├── orchestrator.py  machine à états, reprise à la dernière étape réussie
│   ├── audio.py         étape A — archivage, normalisation, découpage
│   ├── transcribe.py    étape B — transcription, cache par segment
│   ├── analyze.py       étapes C et D — extraction, consolidation, rédaction
│   ├── grounding.py     vérification mécanique, sans modèle de langage
│   └── schemas.py       contrats de sortie structurée du modèle
├── providers/
│   ├── asr/             deepgram, assemblyai, openai, local-whisper, rejeu
│   └── llm/             anthropic, rejeu
├── prompts/             consignes transmises au modèle
└── render/              rendus DOCX et PDF, depuis un modèle commun

frontend/src/
├── lib/recorder.ts      microphone, pause/reprise, envoi progressif
├── lib/api.ts           client de l'API
├── pages/               tableau de bord, enregistrement, fiche de réunion
└── components/          étapes de traitement, éditeur de transcription
```

### Reprise du traitement

Chaque étape écrit son résultat sur le disque avant de passer à la suivante,
et son état est enregistré en base. Un traitement interrompu reprend à la
dernière étape réussie.

La distinction suivante est volontaire :

- **reprendre** après un échec conserve les caches — une transcription déjà
  obtenue n'est jamais repayée ;
- **relancer explicitement** une étape efface ses caches — sinon le bouton
  « Relancer l'analyse » se contenterait de rejouer l'ancien résultat.

Corriger la transcription invalide automatiquement les analyses en cache, et
les étapes d'analyse et de génération repassent à « à faire ».

---

## 10. Tests

```bash
source .venv/bin/activate
pip install -e ".[dev]"

pytest -m "not navigateur"   # suite rapide (~35 s)
pytest -m navigateur         # parcours complet dans un vrai navigateur
pytest                       # tout
```

Le parcours navigateur pilote Chromium, lui injecte un fichier audio en guise
de microphone, et déroule le scénario complet : création, enregistrement,
pause, reprise, arrêt, traitement, téléchargement des documents, correction de
la transcription, régénération, recherche et suppression.

Les tests n'appellent aucune API payante : ils utilisent des fournisseurs de
**rejeu** (`ASR_PROVIDER=test-replay`, `LLM_PROVIDER=test-replay`) qui relisent
des réponses enregistrées dans `tests/fixtures/`. Ces fournisseurs ne sont
actifs que si la configuration les nomme explicitement, et l'interface affiche
alors un bandeau rouge sans ambiguïté.

Le jeu de test contient volontairement trois erreurs typiques d'un modèle de
langage — un chiffre inventé, une information fabriquée, une décision citant
un passage inexistant — et les tests vérifient que les deux premières sont
publiées **avec la mention « à vérifier »** et que la troisième est **écartée**.

---

## 11. Limites connues

Ce que l'application ne fait pas, ou fait imparfaitement. Cette section est
volontairement explicite.

### Ce qui n'a pas pu être validé en conditions réelles

L'environnement de développement n'avait accès **ni aux API de transcription
(Deepgram, AssemblyAI, OpenAI), ni au téléchargement des modèles Whisper, ni à
l'API Anthropic**. En conséquence :

- le code des quatre moteurs de transcription et du client Anthropic est écrit
  d'après leurs documentations respectives, et **n'a pas été exécuté contre le
  service réel** ;
- la qualité de transcription en français, l'identification des locuteurs et
  la qualité rédactionnelle des comptes rendus **n'ont pas pu être mesurées** ;
- les coûts annoncés au §6 sont calculés, pas constatés.

Tout le reste — enregistrement navigateur, envoi progressif, préparation et
découpage de l'audio, orchestration et reprise, consolidation, vérification
mécanique, génération des documents, interface — a été exercé de bout en bout
par les tests automatisés, y compris dans un vrai navigateur.

**Première chose à faire après installation** : enregistrer une réunion courte
avec vos vraies clés, et relire le résultat avant d'accorder votre confiance
aux documents produits.

### Limites de conception

- **Identification des locuteurs** : disponible avec Deepgram et AssemblyAI
  seulement. Elle produit des étiquettes (« Locuteur 1 »), pas des noms ; un
  nom n'est attribué que si quelqu'un le prononce dans la réunion.
- **Transcription locale sans diarisation** : ajouter `pyannote` imposerait un
  second modèle lourd et un jeton Hugging Face. Non retenu en v1.
- **Angle mort de la vérification** : un nom propre placé en tout premier mot
  d'un énoncé libre échappe au contrôle textuel, parce que ce premier mot est
  presque toujours un verbe (« Porter le pool à 300 »). Le compromis est
  assumé : le contrôler ferait apparaître une quinzaine de fausses alertes par
  compte rendu. Les attributions qui comptent — responsable d'une action,
  personnes concernées par une décision — sont, elles, vérifiées.
- **La mention « à vérifier » n'est pas un verdict d'erreur** : elle signale
  qu'une partie de l'énoncé n'a pas été retrouvée *mot pour mot dans le
  passage cité*. Une reformulation fidèle peut la déclencher.
- **Un seul utilisateur** : pas de comptes, pas de partage. `AUTH_TOKEN` est un
  jeton partagé, pas un système d'authentification.
- **Pas de retranscription incrémentale** : corriger la transcription puis
  relancer l'analyse refait l'intégralité des appels d'analyse.
- **Réunions très longues** : testé jusqu'à des durées modestes. Au-delà de
  trois ou quatre heures, surveillez la consommation mémoire de l'étape
  d'analyse.

---

## 12. Dépannage

| Symptôme | Cause et remède |
|---|---|
| « Le navigateur bloque l'accès au microphone » | L'application n'est pas ouverte sur `127.0.0.1` ou en HTTPS. Utilisez l'adresse locale. |
| « L'accès au microphone a été refusé » | Autorisez-le via l'icône à gauche de la barre d'adresse, puis rechargez. |
| « Le microphone est déjà utilisé » | Fermez l'autre application (visioconférence, enregistreur). |
| Aucun son détecté | Mauvais périphérique sélectionné dans les réglages du système, ou micro coupé. |
| « ffmpeg est introuvable » | Installez ffmpeg et vérifiez qu'il est dans le `PATH`. |
| « DEEPGRAM_API_KEY n'est pas renseigné » | Complétez `.env` puis redémarrez le serveur. |
| Interface non compilée | `cd frontend && npm ci && npm run build` |
| Le traitement échoue à l'analyse | Le bouton **Reprendre le traitement** repart de l'étape fautive sans retranscrire. |
| Mode local très lent | Normal sans GPU. Essayez `WHISPER_MODEL=medium`, ou passez sur un moteur distant. |
| Bandeau rouge « Mode test » | `ASR_PROVIDER` ou `LLM_PROVIDER` vaut `test-replay`. Remettez les valeurs réelles. |

---

## Licence et dépendances

Dépendances principales : FastAPI, Pydantic, python-docx, ReportLab, SDK
Anthropic, React, Vite. Dépendance système : ffmpeg.
