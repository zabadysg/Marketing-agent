import type { BrandProfile, ChatSession, ChatSessionDetail, Connection, KnowledgeChunk, KnowledgeDocument, Plan, Post, Workspace } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''
const ADMIN_KEY = import.meta.env.VITE_ADMIN_API_KEY ?? ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const extraHeaders: Record<string, string> = {}
  if (!isFormData) extraHeaders['Content-Type'] = 'application/json'
  if (path.startsWith('/api/admin') && ADMIN_KEY) extraHeaders['X-Admin-Key'] = ADMIN_KEY
  const res = await fetch(`${BASE}${path}`, {
    headers: { ...extraHeaders, ...(init?.headers as Record<string, string> | undefined) },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// Workspaces
export const listWorkspaces = () => req<Workspace[]>('/api/workspaces')
export const createWorkspace = (name: string) =>
  req<Workspace>('/api/workspaces', { method: 'POST', body: JSON.stringify({ name }) })

// Brand Profile (canonical)
export const getBrandProfile = (wsId: string) =>
  req<BrandProfile>(`/api/workspaces/${wsId}/brand-profile`).catch(() => null)
export const updateBrandProfile = (wsId: string, data: Partial<BrandProfile>) =>
  req<BrandProfile>(`/api/workspaces/${wsId}/brand-profile`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })

// Deprecated aliases — kept to avoid breaking existing pages until cleanup
export const getBrand = (wsId: string) => getBrandProfile(wsId)
export const upsertBrand = (wsId: string, data: Partial<BrandProfile>) =>
  updateBrandProfile(wsId, data)

// Knowledge documents
export const uploadDocument = (wsId: string, file: File, docType = 'other') => {
  const form = new FormData()
  form.append('file', file)
  form.append('doc_type', docType)
  return req<KnowledgeDocument>(`/api/workspaces/${wsId}/knowledge/documents`, {
    method: 'POST',
    body: form,
  })
}
export const listDocuments = (wsId: string) =>
  req<KnowledgeDocument[]>(`/api/workspaces/${wsId}/knowledge/documents`)
export const deleteDocument = (wsId: string, docId: string) =>
  req<void>(`/api/workspaces/${wsId}/knowledge/documents/${docId}`, { method: 'DELETE' })
export const searchKnowledge = (wsId: string, q: string, k = 5) =>
  req<KnowledgeChunk[]>(
    `/api/workspaces/${wsId}/knowledge/search?q=${encodeURIComponent(q)}&k=${k}`
  )

// Plans
export const listPlans = (wsId: string) => req<Plan[]>(`/api/workspaces/${wsId}/plans`)
export const getPlan = (wsId: string, planId: string) =>
  req<Plan>(`/api/workspaces/${wsId}/plans/${planId}`)
export const generatePlan = (wsId: string, goal?: string) =>
  req<Plan>(`/api/workspaces/${wsId}/plans:generate`, {
    method: 'POST',
    body: JSON.stringify({ goal: goal || null }),
  })

// Connections
export const getConnections = (wsId: string) =>
  req<{ workspace_id: string; connections: Connection[] }>(`/api/workspaces/${wsId}/connections`)

// Posts
export const approvePost = (postId: string) =>
  req<Post>(`/api/posts/${postId}:approve`, { method: 'POST' })
export const rejectPost = (postId: string, reason?: string) =>
  req<Post>(`/api/posts/${postId}:reject`, { method: 'POST', body: JSON.stringify({ reason }) })
export const editPost = (postId: string, data: { content?: string; hashtags?: string[]; suggested_time?: string }) =>
  req<Post>(`/api/posts/${postId}`, { method: 'PATCH', body: JSON.stringify(data) })
export const regeneratePost = (postId: string, note?: string) =>
  req<void>(`/api/posts/${postId}:regenerate`, { method: 'POST', body: JSON.stringify({ note }) })
export const schedulePost = (postId: string, integrationId: string, provider: string, when: string) =>
  req<Post>(`/api/posts/${postId}:schedule`, {
    method: 'POST',
    body: JSON.stringify({ integration_id: integrationId, provider, when }),
  })

// Chat
export const createChatSession = (wsId: string, title?: string) =>
  req<ChatSession>(`/api/workspaces/${wsId}/chat/sessions`, {
    method: 'POST',
    body: JSON.stringify({ title: title ?? null }),
  })
export const listChatSessions = (wsId: string) =>
  req<ChatSession[]>(`/api/workspaces/${wsId}/chat/sessions`)
export const getChatSession = (wsId: string, sessionId: string) =>
  req<ChatSessionDetail>(`/api/workspaces/${wsId}/chat/sessions/${sessionId}`)
export const deleteChatSession = (wsId: string, sessionId: string) =>
  req<void>(`/api/workspaces/${wsId}/chat/sessions/${sessionId}`, { method: 'DELETE' })
export const sendChatMessage = (wsId: string, sessionId: string, content: string, mode: 'chat' | 'meeting' = 'chat') =>
  req<{ message_id: string; meeting_id?: string }>(
    `/api/workspaces/${wsId}/chat/sessions/${sessionId}/messages`,
    { method: 'POST', body: JSON.stringify({ content, mode }) }
  )
export const submitDraftPost = (postId: string) =>
  req<Post>(`/api/posts/${postId}:submit`, { method: 'POST' })
export const deleteDraftPost = (postId: string) =>
  req<void>(`/api/posts/${postId}`, { method: 'DELETE' })

// Admin
export interface AdminStats {
  workspaces: number
  plans_generating: number; plans_ready: number; plans_failed: number
  posts_pending: number; posts_approved: number; posts_scheduled: number
  posts_published: number; posts_rejected: number
  action_logs: number
}
export interface AdminPlan {
  id: string; workspace_id: string; workspace_name: string
  goal: string | null; status: string; error: string | null
  post_count: number; created_at: string
}
export interface AdminPost {
  id: string; plan_id: string; workspace_id: string; workspace_name: string
  day: number; theme: string; format: string; angle: string; content: string
  hashtags: string[]; suggested_time: string; status: string
  postiz_post_id: string | null; created_at: string
}
export interface AdminLog {
  id: string; workspace_id: string; actor: string; action: string
  payload: Record<string, unknown>; result: Record<string, unknown> | null; created_at: string
}

export const adminStats = () => req<AdminStats>('/api/admin/stats')
export const adminWorkspaces = () => req<{ id: string; name: string; autonomy_level: string; created_at: string }[]>('/api/admin/workspaces')
export const adminDeleteWorkspace = (id: string) => req<void>(`/api/admin/workspaces/${id}`, { method: 'DELETE' })
export const adminPlans = () => req<AdminPlan[]>('/api/admin/plans')
export const adminDeletePlan = (id: string) => req<void>(`/api/admin/plans/${id}`, { method: 'DELETE' })
export const adminPosts = (status?: string) => req<AdminPost[]>(`/api/admin/posts${status ? `?status=${status}` : ''}`)
export const adminDeletePost = (id: string) => req<void>(`/api/admin/posts/${id}`, { method: 'DELETE' })
export const adminLogs = (limit = 100, offset = 0) => req<AdminLog[]>(`/api/admin/logs?limit=${limit}&offset=${offset}`)
