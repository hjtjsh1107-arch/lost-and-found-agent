import { LockKeyhole, X } from 'lucide-react'
import { createContext, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, describe, post } from './api'
import { Alert, Loading } from './ui'

const Auth = createContext<{ signedIn: boolean | null; login: (after?: () => void) => void; logout: () => Promise<void> } | null>(null)

export function useAuth() {
  const auth = useContext(Auth)
  if (!auth) throw new Error('Missing session provider')
  return auth
}

export function Session({ children }: { children: ReactNode }) {
  const [signedIn, setSignedIn] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const entry = useRef<HTMLInputElement>(null)
  const afterLogin = useRef<(() => void) | undefined>(undefined)
  useEffect(() => {
    let active = true
    api<{ authenticated: boolean }>('/staff/session').then(data => {
      if (active) setSignedIn(data.authenticated)
    }).catch(() => { if (active) setSignedIn(false) })
    const expire = () => setSignedIn(false)
    window.addEventListener('laf:session-expired', expire)
    return () => { active = false; window.removeEventListener('laf:session-expired', expire) }
  }, [])

  function login(after?: () => void) {
    afterLogin.current = after
    setError('')
    if (entry.current) entry.current.value = ''
    dialog.current?.showModal()
    entry.current?.focus()
  }

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const key = String(new FormData(event.currentTarget).get('key') ?? '')
    setPending(true)
    setError('')
    try {
      await post('/staff/session', { key })
      setSignedIn(true)
      if (entry.current) entry.current.value = ''
      dialog.current?.close()
      afterLogin.current?.()
    } catch (failure) {
      setError(describe(failure))
      entry.current?.focus()
    } finally {
      setPending(false)
    }
  }

  async function logout() {
    await api('/staff/session', { method: 'DELETE' })
    setSignedIn(false)
  }

  return <Auth.Provider value={{ signedIn, login, logout }}>
    {children}
    <dialog ref={dialog} className="auth-dialog" aria-labelledby="auth-title" onCancel={event => { if (pending) event.preventDefault() }}>
      <form onSubmit={authenticate}>
        <button type="button" className="icon-button dialog-close" aria-label="닫기" disabled={pending} onClick={() => dialog.current?.close()}><X size={22} /></button>
        <span className="panel-icon"><LockKeyhole size={22} aria-hidden /></span>
        <h2 id="auth-title">담당자 접속</h2>
        <p>습득물 등록과 공고 검토에 사용하는 접근 키를 입력해주세요.</p>
        {error && <Alert type="error">{error}</Alert>}
        <label htmlFor="staff-key">담당자 접근 키</label>
        <input ref={entry} id="staff-key" name="key" type="password" required maxLength={512} autoComplete="off" />
        <button className="button primary" type="submit" disabled={pending}>{pending ? '접속 중…' : '접속하기'}</button>
      </form>
    </dialog>
  </Auth.Provider>
}

export function StaffOnly({ children }: { children: ReactNode }) {
  const { signedIn, login } = useAuth()
  if (signedIn === null) return <Loading />
  if (!signedIn) return <div className="empty panel">
    <LockKeyhole size={28} aria-hidden />
    <h2>담당자 접속이 필요합니다</h2>
    <p>접속 후 등록 건의 분석 결과와 실행 기록을 볼 수 있습니다.</p>
    <button className="button primary" onClick={() => login()}>담당자 접속</button>
  </div>
  return children
}
