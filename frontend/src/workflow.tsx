import { ArrowLeft, CheckCircle2, ClipboardCheck, FileSearch, ListChecks, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { analysisLabels, api, describe, fieldLabels, formatDate, post, statusLabels, type Catalog, type Draft, type Drafts, type FieldValue, type ItemSchema, type Progress } from './api'
import { DynamicInput, StandardizedValues } from './item-fields'
import { MaskingPanel, photoUrl } from './masking'
import { Alert, Breadcrumb, Continue, Field, Loading, PageHeading, Section, Steps, Values, useData } from './ui'

function draftUrl(id: string) { return `/drafts/${id}` }
function draftApi(id: string) { return `/staff/drafts/${id}` }

const agents = [
  ['Registration Agent', '사진에서 물품 정보 추출'], ['Evidence Tracker', '필드별 근거 기록'],
  ['Verification Agent', '정확성 검증'], ['Privacy Agent', '공개 범위 및 마스킹 검사'],
  ['Supervisor', '검증 결과 취합'], ['Adaptive Question Agent', '추가 확인 사항 정리'],
]
const findingStatus: Record<string, string> = { verified: '검증됨', uncertain: '확인 필요', rejected: '불일치' }

function ProgressPanel({ id, complete }: { id: string; complete: () => void }) {
  const [data, setData] = useState<Progress>()
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let timer = 0
    async function poll() {
      try {
        const result = await api<Progress>(draftApi(id) + '/progress', { signal: controller.signal })
        if (controller.signal.aborted) return
        setData(result)
        setError('')
        if (result.status !== 'pending') complete()
        else if (result.running) timer = window.setTimeout(poll, 800)
      } catch (failure) {
        if (!controller.signal.aborted) {
          setError(describe(failure))
          timer = window.setTimeout(poll, 2500)
        }
      }
    }
    void poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [id, complete])
  return <>
    {error ? <Alert type="error">{error}</Alert> : data?.status === 'pending' && !data.running
      ? <Alert type="note">이전 분석 실행이 중단되었습니다. 사진 재분석을 실행해주세요.</Alert>
      : <Loading>등록한 사진을 분석 중입니다.</Loading>}
    <ul className="agent-list">
      {agents.map(([agent, title]) => {
        const result = data?.stages.find(stage => stage.agent === agent)
        return <li key={agent}><div><strong>{title}</strong><small>{agent}</small></div>
          <span className={`pill ${result?.status === 'error' ? 'pill-error' : result ? 'pill-good' : ''}`}>
            {result?.status === 'error' ? '오류' : result ? '완료' : '대기'}
          </span>
        </li>
      })}
    </ul>
    {data?.status === 'pending' && !data.running && <Retry id={id} refresh={complete} />}
  </>
}

function Retry({ id, draft, refresh }: { id: string; draft?: Draft; refresh: () => void }) {
  const current = useData<Draft>(draft ? null : draftApi(id))
  const caps = useData<Drafts>('/staff/drafts')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const state = draft ?? current.data
  const capped = state && caps.data ? state.reextractions >= caps.data.max_reextractions : false
  async function retry() {
    if (!state) return
    setBusy(true)
    setMessage('')
    try {
      await post(draftApi(id) + '/reextract', { revision: state.revision })
      refresh()
    } catch (error) { setMessage(describe(error)) }
    finally { setBusy(false) }
  }
  return <div className="stack compact-stack">
    {message && <Alert type="error">{message}</Alert>}
    <div className="flex flex-wrap items-center gap-3">
      <button className="button secondary" disabled={!state || busy || capped} onClick={() => void retry()}>
        {busy ? '사진 분석 중…' : '사진 재분석'}
      </button>
      {state && caps.data && <span className="hint">재분석 {state.reextractions} / {caps.data.max_reextractions}회</span>}
    </div>
  </div>
}

export function AnalysisPage() {
  const { id = '' } = useParams()
  const result = useData<Draft>(draftApi(id))
  const draft = result.data
  return <>
    <Breadcrumb group={{ to: '/', label: '습득물 등록' }} current="AI 분석 및 검증" />
    <PageHeading title="AI 분석 및 검증" description="사진에서 추출한 물품의 특징과 에이전트별 검증 결과를 확인합니다." />
    <Steps current={1} />
    {result.error ? <Alert type="error">{result.error}</Alert> : !draft ? <Loading /> : <>
      <div className="stack">
        <Section title="분석 진행 상태" icon={<FileSearch size={22} />} action={<span className="pill panel-action">{statusLabels[draft.status]}</span>}>
          {draft.analysis.error && <Alert type="error"><strong>{analysisLabels[draft.analysis.analysis_status ?? ''] ?? ''}</strong> {draft.analysis.error}</Alert>}
          {draft.analysis.previous_result && <Alert type="note">아래 정보는 이전 분석 결과입니다. 최신 분석에 실패해 공고에 등록할 수 없습니다.</Alert>}
          {draft.analysis.warnings?.map(warning => <Alert key={warning} type="note">{warning}</Alert>)}
          {draft.analysis.analysis_status === 'partial' && <Alert type="note">일부 특징만 확인했습니다. 확인된 공개 정보로만 공고를 작성했습니다.</Alert>}
          {draft.analysis.retry_recommended && <Alert type="note">필수 정보의 검증 결과를 확인하고 사진을 재분석하거나 정보를 수정해주세요.</Alert>}
          {draft.status === 'pending' ? <ProgressPanel id={id} complete={result.reload} /> : (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="hint">관리번호 {draft.id}</div>
              <Link className="text-link" to={`/logs?draft=${id}`}>에이전트 실행 로그 보기</Link>
            </div>
          )}
        </Section>
        {draft.analysis.item && <Section title="표준화된 물품 정보" icon={<ListChecks size={22} />}>
          {draft.analysis.item_schema && <p className="hint mb-3">스키마 {draft.analysis.item_schema.schema_id} · {draft.analysis.item_schema.schema_version}</p>}
          <StandardizedValues fields={draft.analysis.fields ?? []} categoryNames={{
            major_category_code: draft.analysis.observation?.major_category_name,
            minor_category_code: draft.analysis.observation?.minor_category_name,
          }} />
        </Section>}
        {draft.analysis.observation && <Section title="사진 인식과 공식 분류">
          <Values values={{
            '사진 인식 물품명': draft.analysis.observation.observed_item_name,
            '사진 인식 유형': draft.analysis.observation.observed_item_type,
            '공식 분류 상태': draft.analysis.observation.category_status === 'mapped' ? '매핑 완료' : '미확정',
            '공식 대분류 코드': draft.analysis.observation.major_category_code ?? '미확정',
            '공식 소분류 코드': draft.analysis.observation.minor_category_code ?? '미확정',
          }} />
          {!!draft.analysis.observation.category_candidates?.length && <p className="hint mt-3">
            확인 후보 {draft.analysis.observation.category_candidates.slice(0, 5).map(candidate => candidate.minor_name).join(', ')}
          </p>}
        </Section>}
        {draft.analysis.findings && draft.analysis.findings.length > 0 && <Section title="검증 결과 및 추출 근거" icon={<ShieldCheck size={22} />}>
          <div className="table-scroll" role="region" aria-label="필드별 정확성과 근거" tabIndex={0}>
            <table><thead><tr><th scope="col">검증 항목</th><th scope="col">상태</th><th scope="col">출처 및 근거</th></tr></thead>
              <tbody>{draft.analysis.findings.map(finding => {
                const source = draft.analysis.evidence?.[finding.field]
                return <tr key={finding.field}><th scope="row">{draft.analysis.fields?.find(field => field.field_key === finding.field)?.label ?? fieldLabels[finding.field] ?? finding.field}</th>
                  <td><span className={`pill ${finding.status === 'rejected' ? 'pill-error' : finding.status === 'verified' ? 'pill-good' : ''}`}>{findingStatus[finding.status]}</span></td>
                  <td>{finding.reason}{source?.source && <small className="hint block mt-1">{source.source}
                    {!!source.photos.length && ` · 사진 ${source.photos.join(', ')}`}
                    {!!source.ocr_regions?.length && ` · OCR 영역 ${source.ocr_regions.join(', ')}`}
                  </small>}</td>
                </tr>
              })}</tbody>
            </table>
          </div>
        </Section>}
        <MaskingPanel draft={draft} refresh={result.reload} />
        {draft.status !== 'published' && draft.status !== 'pending' && <Retry id={id} draft={draft} refresh={result.reload} />}
      </div>
      <div className="actions split">
        <Link className="button secondary" to="/"><ArrowLeft size={18} aria-hidden /> 등록 화면으로</Link>
        {draft.status === 'published' ? <Continue to={`/notices/${id}`}>등록된 공고 보기</Continue>
          : draft.analysis.item && !draft.analysis.previous_result && <Continue to={draftUrl(id) + '/confirm'}>추가 정보 확인</Continue>}
      </div>
    </>}
  </>
}

function CorrectionForm({ draft, catalog }: { draft: Draft; catalog: Catalog }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [inline, setInline] = useState<Record<string, string>>({})
  const item = draft.analysis.item
  const available = draft.analysis.privacy?.public
  const [major, setMajor] = useState(draft.analysis.observation?.major_category_code ?? '')
  const [minor, setMinor] = useState(draft.analysis.observation?.minor_category_code ?? '')
  const [classificationEdited, setClassificationEdited] = useState(false)
  const [template, setTemplate] = useState<ItemSchema | undefined>(draft.analysis.item_schema)
  const [loadingTemplate, setLoadingTemplate] = useState(false)
  const [templateError, setTemplateError] = useState('')
  const [values, setValues] = useState<Record<string, FieldValue>>(() => Object.fromEntries((draft.analysis.fields ?? []).map(field => [field.field_key, field.value])))
  const currentType = useRef(values.subcategory)
  const schemaRequest = useRef<AbortController | null>(null)
  useEffect(() => () => schemaRequest.current?.abort(), [])
  const navigate = useNavigate()
  if (!item) return <Alert type="note">AI 분석을 마치면 수정할 물품 정보가 표시됩니다.</Alert>
  const labels = { ...fieldLabels, ...Object.fromEntries((template?.fields ?? []).map(field => [field.field_key, field.label])) }
  const availableMinors = catalog.majors.find(group => group.code === major)?.minors ?? []

  async function changeSchema(selection?: { major: string; minor: string }) {
    if (selection) {
      setMajor(selection.major)
      setMinor(selection.minor)
    }
    setClassificationEdited(!!selection)
    schemaRequest.current?.abort()
    const controller = new AbortController()
    schemaRequest.current = controller
    setLoadingTemplate(true)
    setTemplateError('')
    try {
      const response = await api<{ item_schema: ItemSchema; classification: NonNullable<Draft['analysis']['observation']> }>(draftApi(draft.id) + '/schema', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ name: values.name, subcategory: values.subcategory,
          ...(selection ? { major_category_code: selection.major || null, minor_category_code: selection.minor || null } : {}) }),
      })
      if (controller.signal.aborted) return
      if (!selection) {
        setMajor(response.classification.major_category_code ?? '')
        setMinor(response.classification.minor_category_code ?? '')
      }
      const next = response.item_schema
      const keys = new Set(next.fields.map(field => field.field_key))
      setValues(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => keys.has(key))))
      setTemplate(next)
      setInline({})
    } catch (failure) {
      if (!controller.signal.aborted) setTemplateError(describe(failure))
    } finally { if (!controller.signal.aborted) setLoadingTemplate(false) }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const get = (key: string) => String(form.get(key) ?? '').trim()
    const missing = [
      ...(template?.fields.filter(field => field.required && values[field.field_key] == null).map(field => field.field_key) ?? []),
      ...['region', 'location', 'institution'].filter(key => !get(key)),
    ]
    setInline(Object.fromEntries(missing.map(key => [key, `${labels[key]} 항목을 입력해주세요.`])))
    if (missing.length) {
      const field = event.currentTarget.querySelector<HTMLInputElement>(`[name="${missing[0]}"]`)
      const disclosure = field?.closest('details')
      if (disclosure) disclosure.open = true
      field?.focus()
      return
    }
    setBusy(true)
    setError('')
    try {
      const freeText = (field: 'title' | 'description') => {
        const value = get(field)
        const previouslyEdited = draft.analysis.privacy?.custom_text?.includes(field)
        return value && (value !== available?.[field] || previouslyEdited) ? value : null
      }
      const next = await post<Draft>(draftApi(draft.id) + '/validate', {
        revision: draft.revision,
        item: { category: item?.category, ...Object.fromEntries((template?.fields ?? [])
          .filter(field => !['major_category_code', 'minor_category_code', 'quantity'].includes(field.field_key))
          .map(field => [field.field_key, values[field.field_key] ?? null])) },
        quantity: values.quantity,
        ...(classificationEdited ? { major_category_code: major || null, minor_category_code: minor || null } : {}),
        title: freeText('title'), description: freeText('description'), region: get('region'),
        location: get('location'), institution: get('institution'), memo: get('memo'),
      })
      navigate(draftUrl(draft.id) + '/review', { state: { revalidated: next.status === 'review' } })
    } catch (failure) { setError(describe(failure)) }
    finally { setBusy(false) }
  }

  function text(key: string, initial: string | null | undefined, required = false) {
    return <Field key={key} id={`edit-${key}`} label={labels[key]} error={inline[key]} required={required}>
      <input id={`edit-${key}`} name={key} maxLength={120} defaultValue={initial ?? ''}
        required={required} aria-invalid={inline[key] ? true : undefined} aria-describedby={inline[key] ? `edit-${key}-error` : undefined} />
    </Field>
  }

  return <form onSubmit={save} className="stack" noValidate>
    {error && <Alert type="error">{error}</Alert>}
    <Section title="물품 및 공고 정보 수정" icon={<ClipboardCheck size={22} />} text="분석으로 확인하지 못한 필드를 보완하고 공개할 제목과 설명을 확인해주세요.">
      <div className="form-grid">
        <Field id="official-major" label={labels.major_category_code}>
          <select id="official-major" value={major} onChange={event => void changeSchema({ major: event.target.value, minor: '' })}>
            <option value="">미확정</option>
            {catalog.majors.map(group => <option key={group.code} value={group.code}>{group.name}</option>)}
          </select>
        </Field>
        <Field id="official-minor" label={labels.minor_category_code}>
          <select id="official-minor" value={minor} disabled={!major} onChange={event => void changeSchema({ major, minor: event.target.value })}>
            <option value="">미확정</option>
            {availableMinors.map(group => <option key={group.code} value={group.code}>{group.name}</option>)}
          </select>
        </Field>
        {template?.fields.filter(field => !['major_category_code', 'minor_category_code'].includes(field.field_key)).map(field => {
          const key = field.field_key
          return <DynamicInput field={field} key={key} value={values[key]} error={inline[key]}
            change={value => setValues(previous => ({ ...previous, [key]: value }))}
            onBlur={key === 'subcategory' ? () => {
              if (currentType.current !== values.subcategory) {
                currentType.current = values.subcategory
                void changeSchema()
              }
            } : undefined} />
        })}
      </div>
      {loadingTemplate && <Loading>물품 유형의 입력 서식을 확인합니다.</Loading>}
      {templateError && <Alert type="error">{templateError}</Alert>}
      {text('title', available?.title)}
      <Field id="edit-description" label="공개 설명">
        <textarea id="edit-description" name="description" rows={3} maxLength={300} defaultValue={available?.description ?? ''} />
      </Field>
      <details className="disclosure"><summary>습득 기본 정보 수정</summary>
        <div className="form-grid">
          {(['region', 'location', 'institution'] as const).map(key => text(key, available?.[key] ?? draft.intake[key], true))}
        </div>
        <Field id="edit-memo" label="추가 메모">
          <textarea id="edit-memo" name="memo" maxLength={2000} rows={3} defaultValue={draft.intake.memo} />
        </Field>
      </details>
    </Section>
    <div className="actions split">
      <Link className="button secondary" to={draftUrl(draft.id) + '/analysis'}><ArrowLeft size={18} aria-hidden /> 분석 결과</Link>
      <div className="flex flex-wrap gap-2">
        {draft.status !== 'needs_review' && <Link className="button secondary" to={draftUrl(draft.id) + '/review'}>공고 검토로 이동</Link>}
        <button className="button primary" type="submit" disabled={busy || loadingTemplate || !!templateError || !template}>{busy ? '재검증 중…' : '수정 및 재검증'}</button>
      </div>
    </div>
  </form>
}

export function ConfirmPage() {
  const { id = '' } = useParams()
  const result = useData<Draft>(draftApi(id))
  const catalog = useData<Catalog>('/catalog')
  const draft = result.data
  return <>
    <Breadcrumb group={{ to: '/', label: '습득물 등록' }} current="추가 정보 확인" />
    <PageHeading title="추가 정보 확인" description="분석 결과를 검토하고 필요한 항목만 입력해주세요." />
    <Steps current={2} />
    {result.error || catalog.error ? <Alert type="error">{result.error || catalog.error}</Alert> : !draft || !catalog.data ? <Loading /> :
      draft.status === 'published' ? <Continue to={`/notices/${id}`}>등록된 공고 보기</Continue>
        : draft.status === 'pending' ? <Continue to={draftUrl(id) + '/analysis'}>분석 진행 상태 확인</Continue> :
          <div className="stack">
            {draft.analysis.questions?.length ? <Alert type="note"><strong>담당자 확인 요청</strong>
              <ul className="list-disc pl-5 mt-2">{draft.analysis.questions.map(question => {
                const [label, tail] = question.split(/ 항목(.*)/s)
                return <li key={question}>{tail ? `${fieldLabels[label] ?? label} 항목${tail}` : question}</li>
              })}</ul>
            </Alert> : <Alert>추가 확인 요청이 없습니다. 필요한 경우 값을 수정해 다시 검증할 수 있습니다.</Alert>}
            <CorrectionForm key={`${id}-${draft.revision}`} draft={draft} catalog={catalog.data} />
          </div>}
  </>
}

function ApprovalActions({ draft, refresh }: { draft: Draft; refresh: () => void }) {
  const [confirmed, setConfirmed] = useState(false)
  const [includePhoto, setIncludePhoto] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  async function submit(kind: 'approve' | 'publish') {
    setBusy(true)
    setError('')
    try {
      if (kind === 'approve') {
        await post(draftApi(draft.id) + '/approve', {
          revision: draft.revision, confirmed, publish_photos: includePhoto,
        })
        refresh()
      } else {
        await post(draftApi(draft.id) + '/publish', { revision: draft.revision })
        navigate(`/notices/${draft.id}`, { state: { published: true } })
      }
    } catch (failure) { setError(describe(failure)) }
    finally { setBusy(false) }
  }
  return <div className="stack compact-stack">
    {error && <Alert type="error">{error}</Alert>}
    {draft.status === 'review' && <>
      <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> 공개 정보와 개인정보 마스킹 결과를 검토했습니다.</label>
      {draft.analysis.privacy?.photo_available && <label className="checkbox-label"><input type="checkbox" checked={includePhoto} onChange={event => setIncludePhoto(event.target.checked)} /> 마스킹 사진을 공개 공고에 포함합니다.</label>}
    </>}
    {draft.status === 'approved' && <Alert type="success">담당자 승인 완료 · 공고를 등록하면 통합 목록에 표시됩니다.</Alert>}
    {draft.status === 'needs_review' && <Alert type="note">추가 확인 요청을 해결하고 재검증을 마친 뒤 승인할 수 있습니다.</Alert>}
    <div className="actions split">
      <Link className="button secondary" to={draftUrl(draft.id) + '/confirm'}>정보 수정 및 재검증</Link>
      {draft.status === 'review' && <button className="button primary" disabled={!confirmed || busy} onClick={() => void submit('approve')}>{busy ? '처리 중…' : '담당자 승인'}</button>}
      {draft.status === 'approved' && <button className="button primary" disabled={busy} onClick={() => void submit('publish')}>{busy ? '등록 중…' : '공고 등록'}</button>}
      {draft.status === 'published' && <Continue to={`/notices/${draft.id}`}>등록된 공고 보기</Continue>}
    </div>
  </div>
}

export function ReviewPage() {
  const { id = '' } = useParams()
  const result = useData<Draft>(draftApi(id))
  const draft = result.data
  const privacy = draft?.analysis.previous_result ? undefined : draft?.analysis.privacy
  return <>
    <Breadcrumb group={{ to: '/', label: '습득물 등록' }} current="공고 검토 및 승인" />
    <PageHeading title="공고 검토 및 승인" description="공고에 표시할 정보와 개인정보 보호 결과를 확인한 뒤 등록합니다." />
    <Steps current={3} />
    {result.error ? <Alert type="error">{result.error}</Alert> : !draft ? <Loading /> :
      <div className="stack">
        {draft.analysis.error && <Alert type="error">{draft.analysis.error}</Alert>}
        {draft.status === 'pending' && <Continue to={draftUrl(id) + '/analysis'}>분석 진행 상태 확인</Continue>}
        {privacy && <>
          <Section title="공개 공고 미리보기" icon={<ClipboardCheck size={22} />} action={<span className="pill pill-blue panel-action">공개</span>}>
            {draft.analysis.observation?.category_status === 'unmapped' &&
              <p className="hint mb-3">공식 분류가 미확정인 공고 초안입니다.</p>}
            <Values values={{ ...privacy.public, ...(draft.analysis.observation?.minor_category_name ? { '공식 소분류': draft.analysis.observation.minor_category_name } : {}),
              '습득일시': formatDate(draft.intake.found_at), quantity: draft.intake.quantity }} labels={{ ...fieldLabels, category: '물품 분류' }} />
            {privacy.photo_available && draft.status !== 'published' && (!draft.approval || draft.approval.publish_photos) &&
              <div className="photo-grid mt-3">{privacy.photo_checks?.map(check =>
                <img key={check.position} src={photoUrl(draft, check.position)} alt={`공개 후보 사진 ${check.position}`} />)}
              </div>}
          </Section>
          <Section title="개인정보 검토" icon={<ShieldCheck size={22} />} text="본인확인용과 비공개 정보는 담당자에게만 표시됩니다.">
            <div className="privacy-grid">
              <div><h3><span className="pill pill-blue">본인확인용</span></h3><Values values={privacy.verification_only} labels={{ ...fieldLabels, ...Object.fromEntries((draft.analysis.fields ?? []).map(field => [field.field_key, field.label])) }} /></div>
              <div><h3><span className="pill">비공개</span></h3><Values values={privacy.restricted} labels={{ ...fieldLabels, ...Object.fromEntries((draft.analysis.fields ?? []).map(field => [field.field_key, field.label])) }} /></div>
            </div>
            <MaskingPanel draft={draft} refresh={result.reload} />
          </Section>
        </>}
        {draft.status !== 'pending' && <Section title="담당자 승인 및 공고 등록" icon={<CheckCircle2 size={22} />}
          action={<span className="pill panel-action">{statusLabels[draft.status]}</span>}>
          <ApprovalActions key={`${draft.id}-${draft.revision}`} draft={draft} refresh={result.reload} />
        </Section>}
      </div>}
  </>
}
