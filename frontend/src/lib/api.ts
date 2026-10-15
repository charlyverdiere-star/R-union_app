/** Client de l'API. Une seule origine : aucun jeton n'est exposé dans l'URL. */

import type { AppConfig, Meeting, ReportSummary, Segment, Transcript } from './types'

const TOKEN_KEY = 'assistant-reunions.token'

export function setAuthToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function getAuthToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public headers?: Headers) {
    super(message)
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const token = getAuthToken()
  return token ? { ...extra, 'X-Auth-Token': token } : extra
}

async function parseError(response: Response): Promise<never> {
  let detail = `Erreur ${response.status}`
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') detail = body.detail
  } catch {
    /* réponse sans corps JSON : on garde le message générique */
  }
  throw new ApiError(response.status, detail, response.headers)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: headers(init.headers as never) })
  if (!response.ok) await parseError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  config: () => request<AppConfig>('/api/config'),

  listMeetings: (params: { query?: string; status?: string; date_from?: string; date_to?: string } = {}) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v) search.set(k, v) })
    const suffix = search.toString()
    return request<Meeting[]>(`/api/meetings${suffix ? `?${suffix}` : ''}`)
  },

  createMeeting: (payload: { title: string; meeting_date?: string; context?: string; notes?: string; source?: string }) =>
    request<Meeting>('/api/meetings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getMeeting: (id: string) => request<Meeting>(`/api/meetings/${id}`),

  updateMeeting: (id: string, payload: Partial<Pick<Meeting, 'title' | 'meeting_date' | 'context' | 'notes'>>) =>
    request<Meeting>(`/api/meetings/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  deleteMeeting: (id: string) => request<void>(`/api/meetings/${id}`, { method: 'DELETE' }),

  recordingState: (id: string) =>
    request<{ next_seq: number; bytes: number }>(`/api/meetings/${id}/recording/state`),

  /** Envoie un morceau d'enregistrement. Retourne le numéro attendu en cas de désynchronisation. */
  uploadChunk: async (id: string, seq: number, blob: Blob): Promise<{ ok: true } | { ok: false; expected: number }> => {
    const response = await fetch(`/api/meetings/${id}/recording/chunk?seq=${seq}`, {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/octet-stream' }),
      body: blob,
    })
    if (response.status === 409) {
      const expected = Number(response.headers.get('X-Expected-Seq') ?? seq)
      return { ok: false, expected }
    }
    if (!response.ok) await parseError(response)
    return { ok: true }
  },

  completeRecording: (id: string, payload: { mime_type: string; duration_seconds?: number; start_processing: boolean }) =>
    request<Meeting>(`/api/meetings/${id}/recording/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  importAudio: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Meeting>(`/api/meetings/${id}/import`, { method: 'POST', body: form })
  },

  process: (id: string, fromStage?: string) =>
    request<Meeting>(`/api/meetings/${id}/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_stage: fromStage ?? null }),
    }),

  getTranscript: (id: string) => request<Transcript>(`/api/meetings/${id}/transcript`),

  saveTranscript: (id: string, segments: Segment[]) =>
    request<Transcript>(`/api/meetings/${id}/transcript`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segments }),
    }),

  getReport: (id: string) => request<ReportSummary>(`/api/meetings/${id}/report`),

  downloadUrl: (id: string, kind: string, extension: string) =>
    `/api/meetings/${id}/download/${kind}/${extension}`,

  audioUrl: (id: string) => `/api/meetings/${id}/audio`,

  /** Téléchargement via fetch pour pouvoir joindre le jeton d'accès. */
  download: async (id: string, kind: string, extension: string, filename: string) => {
    const response = await fetch(api.downloadUrl(id, kind, extension), { headers: headers() })
    if (!response.ok) await parseError(response)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  },
}
