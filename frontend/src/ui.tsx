import { AlertCircle, ArrowRight, Check, ChevronRight, Info, LoaderCircle } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { api, describe, displayValue, fieldLabels, type FieldValue } from './api'

export function useData<T>(url: string | null) {
  const [state, setState] = useState<{ url: string; data?: T; error?: string }>()
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    api<T>(url, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setState({ url, data }) })
      .catch(error => { if (!controller.signal.aborted) setState({ url, error: describe(error) }) })
    return () => controller.abort()
  }, [url, version])
  const reload = useCallback(() => setVersion(previous => previous + 1), [])
  return { ...(state?.url === url ? state : {}), reload }
}

export function Alert({ children, type = 'info' }: { children: ReactNode; type?: 'info' | 'error' | 'success' | 'note' }) {
  const Icon = type === 'error' ? AlertCircle : type === 'success' ? Check : Info
  return <div className={`alert alert-${type}`} role={type === 'error' ? 'alert' : 'status'}><Icon size={20} aria-hidden /> <div>{children}</div></div>
}

export function Loading({ children = '불러오는 중입니다.' }: { children?: ReactNode }) {
  return <div className="loading" role="status"><LoaderCircle size={22} aria-hidden /> {children}</div>
}

export function Breadcrumb({ group, current }: { group?: { to: string; label: string }; current: string }) {
  return <nav aria-label="현재 위치" className="breadcrumb">
    <Link to="/">홈</Link><ChevronRight size={14} aria-hidden />
    {group && <><Link to={group.to}>{group.label}</Link><ChevronRight size={14} aria-hidden /></>}
    <span aria-current="page">{current}</span>
  </nav>
}

export function PageHeading({ title, description }: { title: string; description?: string }) {
  return <div className="page-heading"><h1>{title}</h1>{description && <p>{description}</p>}</div>
}

const steps = ['등록 정보 입력', 'AI 분석 및 검증', '추가 정보 확인', '공고 검토 및 승인']

export function Steps({ current }: { current: number }) {
  return <ol className="steps" aria-label="공고 등록 진행 단계">
    {steps.map((text, i) => <li key={text} className={i === current ? 'step-current' : i < current ? 'step-done' : ''} aria-current={i === current ? 'step' : undefined}>
      <span className="step-number">{i < current ? <Check size={17} aria-hidden /> : i + 1}</span>
      <span>{text}</span>
      {i + 1 < steps.length && <ChevronRight className="step-arrow" size={16} aria-hidden />}
    </li>)}
  </ol>
}

export function Field({ id, label, required, error, hint, children }: {
  id: string; label: string; required?: boolean; error?: string; hint?: string; children: ReactNode
}) {
  return <div className="field">
    <label htmlFor={id}>{label}{required && <span className="required" aria-label="필수"> *</span>}</label>
    {children}
    {hint && <small id={`${id}-hint`} className="hint">{hint}</small>}
    {error && <small id={`${id}-error`} className="field-error" role="alert">{error}</small>}
  </div>
}

export function Section({ title, icon, text, action, children }: {
  title: string; icon?: ReactNode; text?: string; action?: ReactNode; children: ReactNode
}) {
  return <section className="panel">
    <div className="panel-header">
      {icon && <span className="panel-icon" aria-hidden>{icon}</span>}
      <div><h2>{title}</h2>{text && <p>{text}</p>}</div>
      {action}
    </div>
    {children}
  </section>
}

export function Values({ values, labels = fieldLabels }: { values: Record<string, FieldValue>; labels?: Record<string, string> }) {
  return <dl className="values">
    {Object.entries(values).map(([key, value]) => <div key={key}><dt>{labels[key] ?? key}</dt><dd>{displayValue(value)}</dd></div>)}
  </dl>
}

export function Continue({ to, children }: { to: string; children: ReactNode }) {
  return <Link className="button primary" to={to}>{children}<ArrowRight size={18} aria-hidden /></Link>
}
