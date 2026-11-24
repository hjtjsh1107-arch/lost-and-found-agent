import { useRef, useState, type PointerEvent } from 'react'
import { describe, fieldLabels, post, type Draft, type MaskBox, type PhotoCheck } from './api'
import { Alert } from './ui'

const piiLabels: Record<string, string> = {
  name: '성명', phone: '전화번호', email: '이메일', address: '주소',
  card_number: '카드번호', card_expiry: '카드 유효기간', card_security: '카드 보안코드',
  resident_id: '주민등록번호', passport: '여권번호', driver_license: '운전면허번호', account: '계좌번호',
  license_plate: '자동차번호판', imei: 'IMEI', serial: '일련번호', identifier: '식별번호',
  face: '얼굴', personal_image: '개인 사진 또는 화면', unreadable: '판독 불가',
  manual: '담당자 지정', contextual: '문맥상 개인정보',
}
const reasons = { policy: '민감 품목의 사진 공개 제한', unverified: '마스킹 안전성을 확인하지 못함', mock: 'Mock Mode 사진 공개 제한' }
const masking = { clean: '감지 영역 없음', masked: '불투명 마스킹 완료', covered: '전체 가림' }

export function photoUrl(draft: Draft, position: number, kind = 'masked') {
  return `/api/staff/drafts/${draft.id}/photos/${position}?kind=${kind}&revision=${draft.revision}`
}

function PhotoEditor({ draft, check, refresh }: { draft: Draft; check: PhotoCheck; refresh: () => void }) {
  const [box, setBox] = useState<MaskBox>([0, 0, 0, 0])
  const origin = useRef<[number, number] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [failed, setFailed] = useState(false)
  const editable = ['review', 'needs_review', 'approved'].includes(draft.status)
  const coordinates = (event: PointerEvent<HTMLElement>): [number, number] => {
    const rect = event.currentTarget.getBoundingClientRect()
    const scale = (value: number, size: number, pixels: number) =>
      Math.round(Math.max(0, Math.min(1, value / size)) * (pixels - 1))
    return [scale(event.clientX - rect.left, rect.width, check.width), scale(event.clientY - rect.top, rect.height, check.height)]
  }
  function track(event: PointerEvent<HTMLElement>) {
    if (!origin.current) return
    const [x, y] = coordinates(event)
    const [firstX, firstY] = origin.current
    setBox([Math.min(x, firstX), Math.min(y, firstY), Math.max(x, firstX), Math.max(y, firstY)])
  }
  async function save() {
    setBusy(true)
    setError('')
    try {
      await post(`/staff/drafts/${draft.id}/masking`, { revision: draft.revision, position: check.position, boxes: [box] })
      refresh()
    } catch (failure) { setError(describe(failure)) }
    finally { setBusy(false) }
  }
  const valid = box[0] >= 0 && box[1] >= 0 && box[0] < box[2] && box[1] < box[3] && box[2] < check.width && box[3] < check.height
  return <div className="stack compact-stack">
    <h4>사진 {check.position} · {masking[check.masking_status]}</h4>
    <p className="hint">{check.privacy_check_status === 'passed' ? '공개 검토 가능' : '비공개'}
      {check.reason && ` · ${reasons[check.reason]}`}</p>
    {!!check.regions.length && <p className="hint">탐지 영역 {check.regions.length}개 · {
      [...new Set(check.regions.flatMap(region => region.types))].map(kind => piiLabels[kind] ?? kind).join(', ')
    }</p>}
    <div className="photo-comparison">
      <figure><figcaption>접근 제한 원본</figcaption>
        {check.original_allowed && draft.status !== 'published'
          ? <img src={photoUrl(draft, check.position, 'original')} alt={`원본 사진 ${check.position}`} />
          : <p className="hint">원본 접근이 제한된 사진입니다.</p>}
      </figure>
      <figure><figcaption>마스킹 결과</figcaption>
        {failed ? <p className="hint">미리보기 없음</p> : <div className={editable ? 'mask-target editable' : 'mask-target'}
          onPointerDown={event => {
            if (!editable || event.button !== 0) return
            origin.current = coordinates(event)
            event.currentTarget.setPointerCapture(event.pointerId)
          }}
          onPointerMove={track}
          onPointerUp={event => { track(event); origin.current = null }}
          onPointerCancel={() => { origin.current = null }}>
          <img draggable={false} src={photoUrl(draft, check.position)} alt={`마스킹된 사진 ${check.position}`}
            onError={() => setFailed(true)} />
          {valid && <span className="mask-selection" aria-hidden="true" style={{
            left: `${box[0] / check.width * 100}%`, top: `${box[1] / check.height * 100}%`,
            width: `${(box[2] - box[0]) / check.width * 100}%`, height: `${(box[3] - box[1]) / check.height * 100}%`,
          }} />}
        </div>}
      </figure>
    </div>
    {editable && <fieldset className="stack compact-stack">
      <legend>추가 마스킹 영역</legend>
      <p className="hint">마스킹 사진에서 드래그하거나 픽셀 좌표를 입력하세요. 저장하면 사진 재검사가 진행됩니다.</p>
      <div className="mask-coordinates">{['왼쪽 X', '위쪽 Y', '오른쪽 X', '아래쪽 Y'].map((label, index) => (
        <label key={label}>{label}<input type="number" min="0" max={(index % 2 ? check.height : check.width) - 1}
          value={box[index]} onChange={event => {
            const value = event.currentTarget.valueAsNumber
            setBox(current => current.map((entry, position) => position === index ? value || 0 : entry) as MaskBox)
          }} /></label>
      ))}</div>
      <button type="button" className="button secondary compact self-start" disabled={!valid || busy}
        onClick={() => void save()}>{busy ? '재검사 중…' : '추가 마스킹 및 재검사'}</button>
      {error && <Alert type="error">{error}</Alert>}
    </fieldset>}
  </div>
}

export function MaskingPanel({ draft, refresh }: { draft: Draft; refresh: () => void }) {
  const privacy = draft.analysis.privacy
  const [show, setShow] = useState(false)
  if (!privacy || draft.analysis.previous_result) return null
  const labels = { ...fieldLabels, ...Object.fromEntries((draft.analysis.fields ?? []).map(field => [field.field_key, field.label])) }
  return <div className="masking stack compact-stack">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3>개인정보 마스킹</h3><p className="hint">
        {privacy.privacy_check_status === 'passed' ? '텍스트 검증 완료' : '재검증 필요'} · {privacy.reviewed ? '담당자 검토 완료' : '담당자 검토 전'}
      </p></div>
      <button type="button" className="button secondary compact" aria-expanded={show} onClick={() => setShow(!show)}>
        {show ? '마스킹 사진 숨기기' : '마스킹 사진 보기'}
      </button>
    </div>
    {!!privacy.detections?.length && <ul className="list-disc pl-5">
      {[...new Set(privacy.detections.map(hit =>
        `${labels[hit.field] ?? (hit.field === 'photo' ? '사진' : hit.field)} · ${piiLabels[hit.type] ?? hit.type}${hit.visibility === 'discard' ? ' · 미수집' : ''}`
      ))].map(label => <li key={label}>{label}</li>)}
    </ul>}
    {!!privacy.discard?.length && <p className="hint">미수집 필드 {privacy.discard.map(field => labels[field] ?? field).join(', ')}</p>}
    {show && (privacy.photo_checks?.length
      ? privacy.photo_checks.map(check => <PhotoEditor key={`${draft.revision}-${check.position}`} draft={draft} check={check} refresh={refresh} />)
      : <Alert>사진을 다시 분석하면 현재 정책에 따른 마스킹 결과를 확인할 수 있습니다.</Alert>)}
  </div>
}
