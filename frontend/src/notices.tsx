import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, ImageOff, PackageSearch, Search } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router'
import { formatDate, type Catalog, type Notice } from './api'
import { Alert, Breadcrumb, Field, Loading, PageHeading, Section, Values, useData } from './ui'

const pageSize = 8

function NoticePhoto({ notice, index = 1 }: { notice: Notice; index?: number }) {
  if (!notice.photo_count) return <div className="image-placeholder"><ImageOff size={25} aria-hidden />사진 비공개</div>
  return <img className="notice-photo" src={`/api/notices/${notice.id}/photos/${index}`} alt={`공개 사진 ${index}`} loading="lazy" />
}

export function NoticesPage() {
  const catalog = useData<Catalog>('/catalog')
  const [params, setParams] = useSearchParams()
  const q = (params.get('q') ?? '').slice(0, 120)
  const category = params.get('category') ?? ''
  const minorCategory = params.get('minor_category') ?? ''
  const [selectedCategory, setSelectedCategory] = useState(category)
  useEffect(() => setSelectedCategory(category), [category])
  const groups = catalog.data?.majors ?? []
  const selectedMajor = groups.find(group => group.code === selectedCategory || group.name === selectedCategory)
  const parsedPage = Number(params.get('page'))
  const page = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? Math.min(parsedPage, 100000) : 1
  const query = new URLSearchParams({ q, limit: String(pageSize), offset: String((page - 1) * pageSize) })
  if (category) query.set('category', category)
  if (minorCategory) query.set('minor_category', minorCategory)
  const results = useData<{ total: number; items: Notice[] }>(`/notices?${query}`)
  const pageCount = Math.ceil((results.data?.total ?? 0) / pageSize)

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const next = new URLSearchParams()
    const keyword = String(fields.get('q') ?? '').trim()
    const type = String(fields.get('category') ?? '')
    if (keyword) next.set('q', keyword)
    if (type) next.set('category', type)
    const minor = String(fields.get('minor_category') ?? '')
    if (type && minor) next.set('minor_category', minor)
    setParams(next)
  }

  function toPage(number: number) {
    const next = new URLSearchParams(params)
    if (number === 1) next.delete('page')
    else next.set('page', String(number))
    const value = next.toString()
    return value ? `?${value}` : '/notices'
  }

  return <>
    <Breadcrumb current="통합 공고 목록" />
    <PageHeading title="통합 습득물 공고" description="습득물의 이름이나 발견 장소를 검색해 등록된 공고를 확인할 수 있습니다." />
    <form className="search-panel" key={`${q}:${category}:${minorCategory}`} onSubmit={search}>
      <Field id="notice-search" label="검색어">
        <input id="notice-search" name="q" type="search" defaultValue={q} maxLength={120} placeholder="물품명, 장소, 보관기관 또는 관리번호" />
      </Field>
      <Field id="category-filter" label="공식 대분류">
        <select id="category-filter" name="category" value={selectedCategory} onChange={event => setSelectedCategory(event.target.value)}>
          <option value="">전체</option>
          {selectedCategory && !selectedMajor && <option value={selectedCategory}>{selectedCategory}</option>}
          {groups.map(group => <option key={group.code} value={group.code}>{group.name}</option>)}
        </select>
      </Field>
      <Field id="minor-filter" label="공식 소분류">
        <select id="minor-filter" name="minor_category" key={selectedCategory} disabled={!selectedMajor}
          defaultValue={selectedCategory === category ? minorCategory : ''}>
          <option value="">전체</option>
          {selectedMajor?.minors.map(entry => <option key={entry.code} value={entry.code}>{entry.name}</option>)}
        </select>
      </Field>
      <button className="button primary" type="submit"><Search size={19} aria-hidden /> 검색</button>
      {(q || category) && <Link className="button secondary" to="/notices">초기화</Link>}
    </form>
    {catalog.error && <Alert type="error">{catalog.error}</Alert>}
    {results.error ? <Alert type="error">{results.error}</Alert> : !results.data ? <Loading /> : <>
      <div className="result-count" aria-live="polite">전체 <strong>{results.data.total.toLocaleString('ko-KR')}</strong>건</div>
      {results.data.items.length ? (
        <ul className="notice-list">
          {results.data.items.map(notice => <li key={notice.id} className="notice-row">
            <NoticePhoto notice={notice} />
            <div className="notice-summary">
              <div className="flex flex-wrap items-center gap-2"><span className="pill pill-blue">{notice.category}</span><span className="hint">{formatDate(notice.found_at, false)}</span></div>
              <h2><Link to={`/notices/${notice.id}`} state={{ from: toPage(page) }}>{notice.title}</Link></h2>
              <p>{notice.location} <span className="separator">·</span> {notice.institution}</p>
            </div>
            <Link className="notice-more" to={`/notices/${notice.id}`} state={{ from: toPage(page) }} aria-label={`${notice.title} 상세보기`}>상세보기 <ArrowRight size={17} aria-hidden /></Link>
          </li>)}
        </ul>
      ) : <div className="empty panel"><PackageSearch size={36} aria-hidden /><h2>조회된 공고가 없습니다</h2><p>검색어 또는 물품 분류를 확인해주세요.</p></div>}
      {pageCount > 1 || page > 1 ? <nav aria-label="공고 목록 페이지" className="pagination">
        {page > 1 && <Link to={toPage(page - 1)} aria-label="이전 페이지"><ChevronLeft size={19} /></Link>}
        {Array.from({ length: Math.min(pageCount, 5) }, (_, i) => Math.max(1, Math.min(page - 2, pageCount - 4)) + i)
          .map(number => <Link key={number} to={toPage(number)} aria-current={number === page ? 'page' : undefined} aria-label={`${number} 페이지`}>{number}</Link>)}
        {page < pageCount && <Link to={toPage(page + 1)} aria-label="다음 페이지"><ChevronRight size={19} /></Link>}
      </nav> : null}
    </>}
  </>
}

export function NoticeDetailPage() {
  const { id = '' } = useParams()
  const routeState = useLocation().state as { from?: string; published?: boolean } | null
  const result = useData<Notice>(`/notices/${id}`)
  const notice = result.data
  const previous = routeState?.from?.startsWith('?') ? `/notices${routeState.from}` : '/notices'
  return <>
    <Breadcrumb group={{ to: previous, label: '통합 공고 목록' }} current="공고 상세" />
    <PageHeading title={notice?.title ?? '습득물 공고 상세'} />
    {routeState?.published && <Alert type="success">공고 등록이 완료되었습니다.</Alert>}
    {result.error ? <Alert type="error">{result.error}</Alert> : !notice ? <Loading /> :
      <Section title="공고 정보" action={<span className="pill pill-blue panel-action">{notice.category}</span>}>
        <div className="notice-detail">
          <div className="photo-grid">
            {notice.photo_count ? Array.from({ length: notice.photo_count }, (_, index) => <NoticePhoto key={index} notice={notice} index={index + 1} />) : <NoticePhoto notice={notice} />}
          </div>
          <Values values={{ name: notice.name, ...(notice.color ? {color: notice.color} : {}),
            ...(notice.minor_category_name ? { '공식 소분류': notice.minor_category_name } : {}),
            quantity: notice.quantity, '습득일시': formatDate(notice.found_at),
            region: notice.region, location: notice.location, institution: notice.institution,
            '관리번호': notice.id, '등록일시': formatDate(notice.published_at) }} />
        </div>
        {notice.description && <div className="public-description"><h3>공개 설명</h3><p>{notice.description}</p></div>}
      </Section>}
    <div className="actions split"><Link className="button secondary" to={previous}><ArrowLeft size={18} aria-hidden /> 목록으로</Link></div>
  </>
}
