import { Box, ChevronRight, ClipboardList, ExternalLink, FileClock, LogIn, LogOut, PackageSearch } from 'lucide-react'
import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router'
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './style.css'
import { describe, type Mode } from './api'
import { LogsPage } from './logs'
import { NoticeDetailPage, NoticesPage } from './notices'
import { RegisterPage } from './register'
import { Session, StaffOnly, useAuth } from './session'
import { Alert, Breadcrumb, PageHeading, useData } from './ui'
import { AnalysisPage, ConfirmPage, ReviewPage } from './workflow'

function Shell() {
  const location = useLocation()
  const [logoutError, setLogoutError] = useState('')
  const health = useData<{ mode: Mode }>('/health')
  const { signedIn, login, logout } = useAuth()
  const main = useRef<HTMLElement>(null)
  const firstPath = useRef(location.pathname)
  useEffect(() => {
    window.scrollTo(0, 0)
    if (firstPath.current !== location.pathname) main.current?.focus({ preventScroll: true })
    firstPath.current = location.pathname
  }, [location.pathname])

  return <>
    <a className="skip-link" href="#main-content">본문 바로가기</a>
    <div className="service-bar"><div className="shell flex items-center justify-between gap-3">
      <div className="flex items-center gap-2"><span className="poc">PoC</span> 기관 업무용 시범 서비스</div>
      <span className="service-type">습득물 등록 · 통합 공고</span>
    </div></div>
    <header className="header">
      <div className="shell brand-row">
        <Link to="/" className="brand"><span className="brand-icon"><Box size={25} strokeWidth={1.8} aria-hidden /></span><span>습득물 공고 센터</span></Link>
        {signedIn ? <button className="staff-button" onClick={() => { setLogoutError(''); void logout().catch(error => setLogoutError(describe(error))) }}><LogOut size={18} aria-hidden /> 접속 해제</button>
          : <button className="staff-button" onClick={() => login()}><LogIn size={18} aria-hidden /> 담당자 접속</button>}
      </div>
      <nav aria-label="주 메뉴" className="primary-nav">
        <div className="shell nav-links">
          <NavLink to="/" end className={({ isActive }) => isActive || location.pathname.startsWith('/drafts/') ? 'nav-active' : ''}><ClipboardList size={18} aria-hidden /> 습득물 등록</NavLink>
          <NavLink to="/notices" className={({ isActive }) => isActive ? 'nav-active' : ''}><PackageSearch size={18} aria-hidden /> 통합 공고 목록</NavLink>
          <NavLink to="/logs" className={({ isActive }) => isActive ? 'nav-active' : ''}><FileClock size={18} aria-hidden /> 실행 로그</NavLink>
        </div>
      </nav>
    </header>
    <main id="main-content" ref={main} tabIndex={-1} className="shell main">
      {logoutError && <Alert type="error">{logoutError}</Alert>}
      {health.error && <Alert type="error">{health.error} <button className="text-link" onClick={health.reload}>다시 연결</button></Alert>}
      {health.data?.mode === 'mock' && <div className="mode-strip" role="status"><span className="mode-dot" />
        <strong>Mock Mode</strong><span>사진 기반 규칙으로 가상 분석 결과를 제공합니다.</span></div>}
      {health.data?.mode === 'real' && <div className="mode-strip" role="status"><span className="mode-dot" />
        <strong>Real Mode</strong><span>로컬 AI 모델과 OCR로 분석합니다.</span></div>}
      <Routes>
        <Route index element={<RegisterPage />} />
        <Route path="drafts/:id/analysis" element={<StaffOnly><AnalysisPage /></StaffOnly>} />
        <Route path="drafts/:id/confirm" element={<StaffOnly><ConfirmPage /></StaffOnly>} />
        <Route path="drafts/:id/review" element={<StaffOnly><ReviewPage /></StaffOnly>} />
        <Route path="notices" element={<NoticesPage />} />
        <Route path="notices/:id" element={<NoticeDetailPage />} />
        <Route path="logs" element={<StaffOnly><LogsPage /></StaffOnly>} />
        <Route path="*" element={<><Breadcrumb current="페이지 없음" /><PageHeading title="페이지를 찾을 수 없습니다" /><Link className="button secondary" to="/">등록 화면으로 <ChevronRight size={16} /></Link></>} />
      </Routes>
    </main>
    <footer className="footer"><div className="shell">
      <div className="footer-title"><Box size={21} aria-hidden /> 습득물 공고 센터</div>
      <p>습득물 등록과 공고 조회를 검증하는 독립 PoC 서비스입니다.</p>
      <a href="https://www.krds.go.kr/html/site/utility/utility_06.html">디자인 출처 · 행정안전부 KRDS (공공누리 제1유형) <ExternalLink size={14} aria-hidden /></a>
    </div></footer>
  </>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><BrowserRouter><Session><Shell /></Session></BrowserRouter></StrictMode>,
)
