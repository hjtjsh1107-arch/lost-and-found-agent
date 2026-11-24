import { FileClock } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import { formatDate, statusLabels, type Drafts, type EventLog } from './api'
import { Alert, Breadcrumb, Field, Loading, PageHeading, Section, useData } from './ui'

export function LogsPage() {
  const [params, setParams] = useSearchParams()
  const drafts = useData<Drafts>('/staff/drafts')
  const id = params.get('draft') ?? drafts.data?.items[0]?.id
  const records = useData<EventLog[]>(id ? `/staff/drafts/${id}/logs` : null)
  return <>
    <Breadcrumb current="에이전트 실행 로그" />
    <PageHeading title="에이전트 실행 로그" description="등록 건별 에이전트 실행 순서, 처리 시간과 검증 분기를 확인합니다." />
    {drafts.error ? <Alert type="error">{drafts.error}</Alert> : !drafts.data ? <Loading /> : (
      <Section title="등록 건 선택" icon={<FileClock size={22} />}>
        {drafts.data.items.length || id ? <div className="flex flex-wrap items-end gap-3">
          <Field id="draft-log-select" label="관리번호">
            <select id="draft-log-select" value={id} onChange={event => setParams({ draft: event.target.value })}>
              {id && !drafts.data.items.some(row => row.id === id) && <option value={id}>{id}</option>}
              {drafts.data.items.map(row => <option key={row.id} value={row.id}>{statusLabels[row.status]} · {row.id}</option>)}
            </select>
          </Field>
          {id && <Link className="button secondary compact" to={`/drafts/${id}/analysis`}>분석 화면 보기</Link>}
          <button className="button secondary compact" onClick={records.reload} disabled={!id}>새로고침</button>
        </div> : <p className="hint">조회할 등록 건이 없습니다.</p>}
      </Section>
    )}
    {records.error && <Alert type="error">{records.error}</Alert>}
    {id && !records.error && (records.data ? <Section title="실행 기록">
      {records.data.length ? <div className="table-scroll" role="region" aria-label="에이전트 실행 기록 표" tabIndex={0}>
        <table><thead><tr>{['순서', '버전', '에이전트', '결과', '처리 시간', '재분석 횟수', '분기', '실행 시각'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{records.data.map(record => <tr key={record.sequence}>
            <td>{record.sequence}</td><td>{record.revision}</td><th scope="row">{record.agent}</th>
            <td><span className={`pill ${record.status === 'ok' ? 'pill-good' : 'pill-error'}`}>{record.status === 'ok' ? '완료' : '오류'}</span></td>
            <td>{record.duration_ms} ms</td><td>{record.attempt}</td><td>{record.decision ?? '—'}</td><td>{formatDate(record.at)}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="hint">이 등록 건에 아직 기록된 로그가 없습니다.</p>}
      {records.data.filter(record => record.mode).map(record => (
        <details className="disclosure" key={record.sequence}>
          <summary>{record.sequence}. {record.agent} · 실행 상세</summary>
          <p>모드 {record.mode === 'real' ? 'Real' : 'Mock'} · Mock 사용 {record.mock_used ? '예' : '아니요'} · 대체 처리 {record.fallback_used ? '예' : '아니요'}</p>
          {record.extracted_fields !== undefined && <p>추출 필드 {record.extracted_fields}개 · OCR 영역 {record.ocr_regions}개</p>}
          {!!record.ignored_attribute_count && <p>서식 또는 수집 정책에서 제외된 속성 {record.ignored_attribute_count}개</p>}
          {record.category_status && <p>공식 분류 {record.category_status === 'mapped' ? '매핑 완료' : '미확정'}</p>}
          {record.verification && <p>검증됨 {record.verification.verified ?? 0}개 · 확인 필요 {record.verification.uncertain ?? 0}개 · 불일치 {record.verification.rejected ?? 0}개</p>}
          {record.privacy_status && <p>공개 심사 {record.privacy_status === 'success' ? '성공' : '실패'}</p>}
          {record.analysis_status && <p>분석 결과 <code>{record.analysis_status}</code></p>}
          {record.error_code && <p>오류 코드 <code>{record.error_code}</code></p>}
          {record.model_calls?.length ? record.model_calls.map(call =>
            <div className="mt-2" key={call.purpose}>
              <p><strong>{call.model}</strong> · {call.server} · {call.duration_ms} ms</p>
              <p>호출 시도 {call.called ? '예' : '아니요'} · 응답 {call.responded ? '수신' : '미수신'} ·
                {call.image_count ? ` 이미지 ${call.image_count}장 ${call.image_input_ok ? '접수' : '미확인'}` : ' 이미지 입력 없음'}</p>
              {call.error_code && <p>오류 코드 <code>{call.error_code}</code></p>}
            </div>) : <p className="hint">이 단계의 모델 호출 기록이 없습니다.</p>}
        </details>
      ))}
    </Section> : <Loading />)}
  </>
}
