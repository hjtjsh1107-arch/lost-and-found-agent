export type DraftStatus = 'pending' | 'error' | 'needs_review' | 'review' | 'approved' | 'published'
export type Mode = 'mock' | 'real'
export type Denomination = { denomination: number; count: number }
export type FieldValue = string | number | boolean | string[] | Denomination[] | null
export type Item = Record<string, FieldValue> & { category: string }
export type Catalog = {
  major_count: number; minor_count: number
  majors: { code: string; name: string; minors: { code: string; name: string }[] }[]
}
export type SchemaField = {
  field_key: string; label: string; data_type: 'string' | 'number' | 'select' | 'multiselect' | 'boolean' | 'denominations'
  description: string; required: boolean; options: string[]; display_order: number
  extraction_source: string; default_visibility: string; store: boolean
}
export type StructuredField = SchemaField & {
  value: FieldValue; source: string | null; verification_status: string
  evidence: { photos: number[]; ocr_regions: number[] }
  visibility: 'public' | 'verification_only' | 'restricted' | 'discard'
}
export type ItemSchema = { schema_id: string; schema_version: string; fields: SchemaField[] }
export type EventLog = {
  agent: string; status: string; attempt: number; duration_ms: number; at: string; decision: string | null
  sequence?: number; revision?: number
  mode?: Mode; mock_used?: boolean; fallback_used?: boolean; error_code?: string | null
  extracted_fields?: number; ocr_regions?: number; category_status?: string
  ignored_attribute_count?: number
  verification?: Record<string, number>; privacy_status?: string; analysis_status?: string
  model_calls?: {
    purpose: string; model: string; server: string; image_count: number; called: boolean
    responded: boolean; image_input_ok: boolean; duration_ms: number; error_code: string | null
  }[]
}
export type Intake = {
  found_at: string; region: string; location: string; finder: string
  institution: string; memo: string; quantity: number
}
export type Privacy = {
  public: Record<string, string | null>
  verification_only: Record<string, FieldValue>
  restricted: Record<string, string | null>
  photo_available: boolean; text_mask_count?: number; policy: string
  ocr: { photo: number; score: number; text: string; polygon: number[][] }[]
  public_attributes?: Record<string, string>
  custom_text?: string[]
  privacy_check_status?: 'passed' | 'failed'
  reviewed?: boolean
  discard?: string[]
  detections?: { field: string; type: string; visibility: string; source: string }[]
  photo_checks?: PhotoCheck[]
}
export type MaskBox = [number, number, number, number]
export type PhotoCheck = {
  position: number; width: number; height: number; original_allowed: boolean
  masking_status: 'clean' | 'masked' | 'covered'; privacy_check_status: 'passed' | 'blocked'
  reason: 'policy' | 'unverified' | 'mock' | null
  regions: { box: MaskBox; types: string[]; source: string }[]
}
export type Draft = {
  id: string; mode: Mode; status: DraftStatus; revision: number; intake: Intake
  reextractions: number; photo_count: number; approval: { at: string; publish_photos: boolean } | null
  analysis: {
    error?: string; item?: Item; questions?: string[]; privacy?: Privacy; fields?: StructuredField[]; item_schema?: ItemSchema
    extraction?: { item: Item }
    findings?: { field: string; status: string; reason: string }[]
    analysis_status?: string; warnings?: string[]; retry_recommended?: boolean; previous_result?: boolean
    observation?: {
      observed_item_name: string | null; observed_item_type: string | null; category_status: 'mapped' | 'unmapped'
      major_category_code: string | null; minor_category_code: string | null; attributes: Record<string, FieldValue>
      major_category_name?: string | null; minor_category_name?: string | null
      category_candidates?: { major_code: string; major_name: string; minor_code: string; minor_name: string }[]
    }
    evidence?: Record<string, { value: FieldValue; source: string | null; photos: number[]; ocr_regions?: number[]; status?: string }>
  }
}
export type Notice = {
  id: string; mode: Mode; found_at: string; published_at: string; photo_count: number; quantity: number
  category: string; name: string; title: string; color: string | null; description: string | null
  region: string; location: string; institution: string
  major_category_name?: string | null; minor_category_name?: string | null
}

export function displayValue(value: FieldValue | undefined): string {
  if (value == null) return '미확인'
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  return Array.isArray(value) ? value.map(entry => typeof entry === 'string' ? entry : `${entry.denomination} × ${entry.count}`).join(', ') : String(value)
}
export type Drafts = { max_reextractions: number; items: { id: string; status: DraftStatus; updated_at: string }[] }
export type Progress = { status: DraftStatus; running: boolean; stages: EventLog[] }

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api' + path, { ...options, credentials: 'same-origin' })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new ApiError('서버에 연결할 수 없습니다. 서비스 실행 상태를 확인해주세요.', 0)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    if (response.status === 401 && path.startsWith('/staff/') && path !== '/staff/session') {
      window.dispatchEvent(new Event('laf:session-expired'))
    }
    throw new ApiError(typeof body.detail === 'string' ? body.detail : '요청을 처리하지 못했습니다. 다시 시도해주세요.', response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function post<T>(path: string, data: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
}

export function describe(error: unknown) {
  return error instanceof Error ? error.message : '요청을 처리하지 못했습니다.'
}

export function formatDate(value: string, withTime = true) {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  }).format(new Date(value))
}

export const statusLabels: Record<DraftStatus, string> = {
  pending: '분석 중', error: '분석 오류', needs_review: '추가 확인 필요',
  review: '승인 대기', approved: '승인 완료', published: '공고 등록 완료',
}
export const fieldLabels: Record<string, string> = {
  category: '개발용 물품 분류', subcategory: '관찰된 물품 유형', name: '물품명', brand: '브랜드',
  model: '모델명', color: '색상', material: '재질', shape: '형태', features: '외관 특징',
  damage: '파손 여부', detail_1: '유형별 특징', detail_2: '세부 식별 특징',
  quantity: '수량', title: '공고 제목', description: '공개 설명', region: '습득 지역',
  location: '습득 장소', institution: '보관기관', finder: '습득자명', memo: '추가 메모',
  ocr: '이미지 문자', workflow: '분석 실행',
  part_colors: '부위별 색상', handle: '손잡이 특징', handle_color: '손잡이 색상', accessories: '부속품', pattern: '무늬',
}
export const analysisLabels: Record<string, string> = {
  success: '실제 분석 성공', partial: '일부 정보 확인', mock: '가상 분석', verification_failed: '검증 필요',
  model_connection_failed: 'Ollama 연결 실패', model_not_found: '모델 미설치',
  image_input_failed: '이미지 입력 실패', parsing_failed: '응답 구조화 실패',
  model_timeout: '모델 응답 시간 초과', model_request_failed: '모델 요청 실패', processing_failed: '분석 중단',
}
