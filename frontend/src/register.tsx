import { ArrowRight, Camera, ClipboardList, FileImage, ImagePlus, LockKeyhole, Trash2, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { api, describe, type Intake } from './api'
import { useAuth } from './session'
import { Alert, Breadcrumb, Field, PageHeading, Section, Steps } from './ui'

type Attachment = { id: string; file: File }
const imageTypes = /\.(jpe?g|png|gif|bmp|webp)$/i
const maxBytes = 8 * 1024 * 1024

function PhotoPreview({ entry, index, remove }: { entry: Attachment; index: number; remove: () => void }) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    const url = URL.createObjectURL(entry.file)
    setSrc(url)
    return () => URL.revokeObjectURL(url)
  }, [entry.file])
  return <li className="attachment">
    {src && <img alt={`첨부 사진 ${index + 1}`} src={src} />}
    <span><strong>{entry.file.name}</strong><small>{Math.ceil(entry.file.size / 1024)} KB</small></span>
    <button className="icon-button" aria-label={`사진 ${index + 1} 삭제`} type="button" onClick={remove}><Trash2 size={19} aria-hidden /></button>
  </li>
}

function localDate() {
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23',
  }).format(new Date()).split(' ')
  return { date: parts[0], time: parts[1] }
}

export function RegisterPage() {
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [photoIssue, setPhotoIssue] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [hovered, setHovered] = useState(false)
  const [busy, setBusy] = useState(false)
  const [serverError, setServerError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const chooser = useRef<HTMLButtonElement>(null)
  const { signedIn, login } = useAuth()
  const navigate = useNavigate()
  const [initial] = useState(localDate)

  function addPhotos(files: File[]) {
    const accepted: Attachment[] = []
    let reason = ''
    for (const file of files) {
      if (!imageTypes.test(file.name)) { reason = 'JPG, PNG, GIF, BMP 또는 WEBP 파일을 선택해주세요.'; continue }
      if (file.size > maxBytes) { reason = '사진은 한 장당 8 MB까지 첨부할 수 있습니다.'; continue }
      if (attachments.length + accepted.length >= 3) { reason = '사진은 최대 3장까지 첨부할 수 있습니다.'; break }
      accepted.push({ id: crypto.randomUUID(), file })
    }
    setAttachments(existing => [...existing, ...accepted].slice(0, 3))
    setPhotoIssue(reason)
  }

  function drop(event: DragEvent) {
    event.preventDefault()
    setHovered(false)
    addPhotos(Array.from(event.dataTransfer.files))
  }

  async function upload(metadata: Intake, photos: Attachment[]) {
    setBusy(true)
    setServerError('')
    const body = new FormData()
    body.append('metadata', JSON.stringify(metadata))
    photos.forEach(({ file }) => body.append('photos', file))
    try {
      const draft = await api<{ id: string }>('/staff/drafts?background=true', { method: 'POST', body })
      navigate(`/drafts/${draft.id}/analysis`)
    } catch (error) {
      setServerError(describe(error))
      window.scrollTo(0, 0)
    } finally { setBusy(false) }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    const get = (name: string) => String(values.get(name) ?? '').trim()
    const check: Record<string, string> = {}
    const fields = { region: '습득 지역', location: '습득 장소', finder: '습득자명', institution: '보관기관' }
    for (const [key, label] of Object.entries(fields)) if (!get(key)) check[key] = `${label} 항목을 입력해주세요.`
    if (!get('date')) check.date = '습득일자를 선택해주세요.'
    if (!get('time')) check.time = '습득시각을 입력해주세요.'
    const found_at = `${get('date')}T${get('time')}:00+09:00`
    if (!check.date && !check.time && Date.parse(found_at) > Date.now()) check.date = '습득일시는 현재 시각보다 늦을 수 없습니다.'
    const quantity = Number(get('quantity'))
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 999) check.quantity = '수량을 1~999 사이로 입력해주세요.'
    setErrors(check)
    if (!attachments.length) {
      setPhotoIssue('습득물 사진을 한 장 이상 첨부해주세요.')
      chooser.current?.focus()
      return
    }
    const first = Object.keys(check).sort((a, b) => ['date', 'time', 'quantity', 'region', 'location', 'finder', 'institution'].indexOf(a) -
      ['date', 'time', 'quantity', 'region', 'location', 'finder', 'institution'].indexOf(b))[0]
    if (first) { form.querySelector<HTMLInputElement>(`[name="${first}"]`)?.focus(); return }
    const metadata = {
      found_at, region: get('region'), location: get('location'), finder: get('finder'),
      institution: get('institution'), memo: get('memo'), quantity,
    }
    if (signedIn) void upload(metadata, attachments)
    else login(() => { void upload(metadata, attachments) })
  }

  function field(key: string, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) {
    const id = `intake-${key}`
    return <Field id={id} label={label} required error={errors[key]}>
      <input id={id} name={key} required aria-invalid={errors[key] ? true : undefined}
        aria-describedby={errors[key] ? `${id}-error` : undefined} {...props} />
    </Field>
  }

  return <>
    <Breadcrumb current="습득물 등록" />
    <PageHeading title="습득물 등록" description="습득물 사진과 기본 정보를 입력해주세요. 사진을 분석해 공고 초안을 작성합니다." />
    <Steps current={0} />
    {serverError && <Alert type="error">{serverError}</Alert>}
    <div className="work-grid">
      <form className="stack" onSubmit={submit} noValidate>
        <Section title="습득물 사진" icon={<Camera size={22} />} text="외관을 확인할 수 있도록 물품 전체가 보이는 사진을 올려주세요.">
          <div className={`drop-zone ${hovered ? 'drop-active' : ''} ${photoIssue ? 'drop-error' : ''}`}
            onDragOver={event => { event.preventDefault(); setHovered(true) }} onDragLeave={() => setHovered(false)} onDrop={drop}>
            <span className="drop-icon"><UploadCloud size={30} strokeWidth={1.7} aria-hidden /></span>
            <strong>사진을 이곳에 끌어다 놓으세요</strong>
            <span>JPG, PNG, GIF, BMP, WEBP · 장당 최대 8 MB</span>
            <button ref={chooser} className="button outline compact" type="button" onClick={() => input.current?.click()}
              aria-describedby={photoIssue ? 'photo-error' : undefined} disabled={busy}>
              <ImagePlus size={18} aria-hidden /> 사진 선택
            </button>
            <input ref={input} type="file" name="photos" aria-label="습득물 사진" className="sr-only" tabIndex={-1}
              accept=".jpg,.jpeg,.png,.gif,.bmp,.webp" multiple onChange={event => { addPhotos(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} />
          </div>
          {photoIssue && <p className="field-error" id="photo-error" role="alert">{photoIssue}</p>}
          <div className="upload-meta"><strong>{attachments.length}</strong> / 최대 3장 <span className="required">필수</span></div>
          {attachments.length > 0 && <ul className="attachment-list">
            {attachments.map((entry, index) => <PhotoPreview key={entry.id} entry={entry} index={index} remove={() => {
              setAttachments(items => items.filter(item => item.id !== entry.id))
              setPhotoIssue('')
            }} />)}
          </ul>}
        </Section>
        <Section title="습득 기본 정보" icon={<ClipboardList size={22} />}
          action={<span className="panel-action hint"><span className="required">*</span> 필수 입력 항목</span>}>
          <div className="form-grid three">
            {field('date', '습득일자', { type: 'date', defaultValue: initial.date, max: localDate().date })}
            {field('time', '습득시각', { type: 'time', defaultValue: initial.time })}
            {field('quantity', '수량', { type: 'number', defaultValue: 1, min: 1, max: 999 })}
          </div>
          <div className="form-grid">
            {field('region', '습득 지역', { placeholder: '예: 서울시 중구', maxLength: 120 })}
            {field('location', '습득 장소', { placeholder: '예: 시청역 대합실', maxLength: 120 })}
            {field('finder', '습득자명', { placeholder: '습득한 사람의 이름', maxLength: 120, autoComplete: 'off' })}
            {field('institution', '보관기관', { placeholder: '예: 시청역 고객안내센터', maxLength: 120 })}
          </div>
          <Field id="intake-memo" label="추가 메모" hint="안전한 공고를 위해 이름, 전화번호 등 개인정보 입력은 피해주세요.">
            <textarea id="intake-memo" name="memo" rows={3} maxLength={2000} aria-describedby="intake-memo-hint"
              placeholder="사진만으로 확인하기 어려운 특징이나 습득 상황을 입력해주세요." />
          </Field>
        </Section>
        <div className="actions">
          <button type="submit" className="button primary" disabled={busy}>{busy ? '사진 전송 중…' : 'AI 분석 시작'} <ArrowRight size={19} aria-hidden /></button>
        </div>
      </form>
      <aside className="guide" aria-labelledby="guide-title">
        <h2 id="guide-title">등록 안내</h2>
        <div><FileImage size={20} aria-hidden /><section><h3>사진 첨부</h3><p>물품 전체와 주요 특징이 보이는 사진을 1~3장 올려주세요.</p></section></div>
        <div><ClipboardList size={20} aria-hidden /><section><h3>AI 초안 확인</h3><p>물품명, 분류, 외관 특징과 공고 제목을 분석 결과에서 확인하고 수정할 수 있습니다.</p></section></div>
        <div><LockKeyhole size={20} aria-hidden /><section><h3>공개 범위</h3><p>습득자명은 비공개로 보관합니다. 담당자가 검토하고 승인한 정보만 공고에 등록됩니다.</p></section></div>
      </aside>
    </div>
  </>
}
