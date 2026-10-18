import { useState } from 'react'
import Data from './pages/Data'
import Plan from './pages/Plan'
import Store from './pages/Store'
import Strategies from './pages/Strategies'
import { AppProvider, useApp } from './store'

const PAGES = [
  { key: 'data', label: 'Data', hint: 'what happened' },
  { key: 'store', label: 'My store', hint: 'how it runs today' },
  { key: 'strategies', label: 'Strategies', hint: 'what pays off' },
  { key: 'plan', label: 'Plan', hint: 'what we decided' },
] as const
type Page = (typeof PAGES)[number]['key']

function Shell() {
  const app = useApp()
  const [page, setPage] = useState<Page>(() => {
    // older versions stored Italian page keys: anything unknown falls back to the store page
    try { const p = localStorage.getItem('fitpath-page'); return PAGES.some(x => x.key === p) ? (p as Page) : 'store' } catch { return 'store' }
  })
  const go = (p: Page) => { setPage(p); try { localStorage.setItem('fitpath-page', p) } catch { /* ignore */ } }
  const running = Object.values(app.jobs).filter(j => j.state === 'running').length
  const llmOk = app.status?.llm.configured

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">Fit<span>Path</span></div>
        <nav className="nav steps-nav" aria-label="Sections">
          {PAGES.map((p, i) => (
            <button key={p.key} aria-current={p.key === page ? 'page' : undefined} onClick={() => go(p.key)}>
              <i>{i + 1}</i><span>{p.label}<small>{p.hint}</small></span>
              {p.key === 'plan' && app.plan.length > 0 && <em>{app.plan.length}</em>}
            </button>
          ))}
        </nav>
        <div className="status">
          {running > 0 && <span className="pill"><i style={{ background: 'var(--accent)' }} />{running} running</span>}
          <span className="pill"><i style={{ background: app.status ? 'var(--ok)' : 'var(--crit)' }} />{app.status ? 'Data ready' : 'Backend…'}</span>
          <span className="pill"><i style={{ background: llmOk ? 'var(--ok)' : 'var(--warn)' }} />{llmOk ? `LLM ${app.status?.llm.model}` : 'LLM: rules'}</span>
        </div>
      </header>
      <main>
        {app.error && <p className="note" style={{ color: '#ffb4a8' }}>{app.error}</p>}
        {page === 'data' && <Data />}
        {page === 'store' && <Store />}
        {page === 'strategies' && <Strategies />}
        {page === 'plan' && <Plan />}
      </main>
    </div>
  )
}

export default function App() {
  return <AppProvider><Shell /></AppProvider>
}
