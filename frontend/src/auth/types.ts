/** Auth API response types for login / register / me / workspaces. */

export interface AuthUser {
  id: string
  email: string
  display_name: string
  status: string
}

export interface WorkspaceSummary {
  id: string
  name: string
  role: string
  member_count?: number
}

export interface RegisterResponse {
  user: AuthUser
  workspace: WorkspaceSummary
  api_key: string
}

export interface LoginResponse {
  user: AuthUser
  workspaces: WorkspaceSummary[]
  api_key: string
}

export interface MeResponse {
  user: AuthUser
  workspaces: WorkspaceSummary[]
}

export interface MemberSummary {
  user_id: string
  email: string
  display_name: string
  role: string
  created_at: string | null
}

export interface UserApiKeySummary {
  key_hash_prefix: string
  name: string
  status: string
  created_at: string | null
  last_used_at: string | null
}

export interface CreatedUserApiKey extends UserApiKeySummary {
  raw_key: string
}
