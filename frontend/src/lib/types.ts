/** Types renvoyés par l'API. Ils reflètent `backend/app/api/schemas.py`. */

export type MeetingStatus =
  | 'created' | 'recording' | 'ready' | 'processing' | 'completed' | 'failed'

export type StageStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Stage {
  stage: 'audio' | 'transcription' | 'analysis' | 'documents'
  label: string
  status: StageStatus
  progress: number
  message: string
  error: string | null
}

export interface Deliverable {
  kind: 'audio' | 'report' | 'summary'
  label: string
  filename: string
  size_bytes: number
  available: boolean
  formats: string[]
}

export interface Meeting {
  id: string
  title: string
  meeting_date: string
  context: string | null
  notes: string | null
  status: MeetingStatus
  source: 'recording' | 'import'
  duration_seconds: number
  duration_label: string
  created_at: string
  updated_at: string
  documents_generated_at: string | null
  transcribed_at: string | null
  error: string | null
  language: string | null
  audio_bytes: number
  stages: Stage[]
  overall_progress: number
  deliverables: Deliverable[]
  has_transcript: boolean
  is_running: boolean
}

export interface Segment {
  id: string
  start: number
  end: number
  text: string
  speaker: string | null
  confidence: number | null
  low_confidence: boolean
  edited: boolean
}

export interface Transcript {
  language: string
  provider: string
  model: string
  duration: number
  diarization: boolean
  warnings: string[]
  created_at: string
  edited_at: string | null
  segments: Segment[]
  speakers: string[]
  low_confidence_ratio: number
}

export interface ReportSummary {
  generated_at: string | null
  llm_model: string
  topics: number
  decisions: number
  actions: number
  open_questions: number
  risks: number
  items_total: number
  items_verified: number
  items_partial: number
  dropped_items: number
  low_confidence_ratio: number
  notes: string[]
}

export interface AppConfig {
  asr_provider: string
  asr_ready: boolean
  asr_problem: string | null
  asr_is_local: boolean
  asr_is_test: boolean
  llm_provider: string
  llm_model: string
  llm_ready: boolean
  llm_problem: string | null
  llm_is_test: boolean
  language: string
  diarization: boolean
  archive_format: string
  retention_days: number
  max_upload_mb: number
  auth_required: boolean
}
