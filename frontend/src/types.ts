export interface Workspace {
  id: string
  name: string
  autonomy_level: string
  created_at: string
}

export interface ProductItem {
  name: string
  description: string
  price_point?: string
}

export interface AudienceSegment {
  name: string
  description: string
  pain_points: string[]
  channels: string[]
}

export interface BrandProfile {
  id: string
  workspace_id: string
  company_name: string | null
  brand_name: string | null
  industry: string | null
  products: ProductItem[]
  audience_segments: AudienceSegment[]
  tone: string | null
  voice_guidelines: string | null
  positioning: string | null
  goals: string[]
  avoid: string[]
  extra: Record<string, unknown>
  onboarding_status: 'in_progress' | 'pending_review' | 'active'
  created_at: string
  updated_at: string
}

export interface KnowledgeDocument {
  id: string
  workspace_id: string
  filename: string
  doc_type: string
  storage_path: string
  status: 'processing' | 'indexed' | 'failed'
  uploaded_at: string
}

export interface KnowledgeChunk {
  id: string
  document_id: string
  workspace_id: string
  content: string
  chunk_metadata: Record<string, unknown>
  created_at: string
}

export type PostStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'scheduled'
  | 'published'
  | 'rejected'

export interface Post {
  id: string
  day: number
  theme: string
  format: string
  angle: string
  content: string
  hashtags: string[]
  suggested_time: string
  status: PostStatus
  created_at: string
}

export type PlanStatus = 'generating' | 'ready' | 'failed'

export interface Plan {
  id: string
  workspace_id: string
  goal: string | null
  status: PlanStatus
  error: string | null
  posts: Post[]
  created_at: string
}

export interface Connection {
  id: string
  name: string
  identifier: string
  picture: string
  disabled: boolean
  profile: string
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  metadata_: Record<string, unknown>
  agent_id?: string | null
  meeting_id?: string | null
  turn_index?: number | null
  created_at: string
}

export interface ChatSession {
  id: string
  workspace_id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[]
}
