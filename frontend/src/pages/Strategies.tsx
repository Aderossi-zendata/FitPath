// Strategy workspace: suggestions drawer (left), digital twin (center), agent chat (right).
// The chat follows opencode's structure: a session is a list of messages made of parts; tool calls are
// blocks with a status, progress, arguments and result; long simulations wait for the user's approval.
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type AgentMessage, type AgentSession, type AgentSessionMeta, type MsgPart, type StoreNote, type Suggestion, type Summary, type ToolPart } from '../api'
import { VERDICT } from '../components'
import { useApp } from '../store'
import { eur } from '../ui'
import Twin3D, { type TwinPreview } from './Twin3D'

const EXAMPLES = [
  'Give me a summary of last week: what went well and what can we improve?',
  'What do you suggest we do next month?',
  'How do we get ready for Black Friday 2020? Test the best ideas in the twin.',
  'Are the departments in the right place? Optimise the layout for next weekend.',
  'I noticed that on Saturdays fitting room 2 is often out of order.',
]
const SESSION_KEY = 'fitpath-agent-session'

// ---------- minimal markdown: paragraphs, bullets, headings, bold, italics, code ----------
function inline(t: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_|\*[^*\s][^*]*\*)/g
  let last = 0, m: RegExpExecArray | null, i = 0
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(t.slice(last, m.index))
    const x = m[0]
    if (x.startsWith('**')) out.push(<b key={i++}>{x.slice(2, -2)}</b>)
    else if (x.startsWith('`')) out.push(<code key={i++}>{x.slice(1, -1)}</code>)
    else out.push(<i key={i++}>{x.slice(1, -1)}</i>)
    last = m.index + x.length
  }
  if (last < t.length) out.push(t.slice(last))
  return out
}
function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: string[] = []
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>); list = [] } }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const li = line.match(/^\s*(?:[*\-•]|\d+\.)\s+(.*)$/)
    if (li) { list.push(li[1]); continue }
    flush()
    if (!line.trim()) continue
    const h = line.match(/^#{1,4}\s+(.*)$/)
    blocks.push(h ? <h4 key={blocks.length}>{inline(h[1])}</h4> : <p key={blocks.length}>{inline(line)}</p>)
  }
  flush()
  return <div className="md">{blocks}</div>
}

// ---------- tool block ----------
const ICON: Record<ToolPart['status'], string> = { running: '◐', done: '✓', error: '✕', rejected: '⊘', pending_approval: '?' }
function argLine(p: ToolPart): string {
  const a = p.args as Record<string, unknown>
  const bits: string[] = []
  if (a.start) bits.push(`from ${a.start}`)
  if (a.days) bits.push(`${a.days} ${Number(a.days) === 1 ? 'day' : 'days'}`)
  if (a.reps) bits.push(`${a.reps} replicas`)
  if (a.topic) bits.push(String(a.topic).replace(/_/g, ' '))
  const st = a.strategy as { name?: string } | undefined
  if (st?.name) bits.unshift(`“${st.name}”`)
  if (a.text) bits.push(`“${String(a.text).slice(0, 60)}”`)
  return bits.join(' · ')
}
function ToolBlock({ p, onApprove }: { p: ToolPart; onApprove: (ok: boolean) => void }) {
  const [open, setOpen] = useState(false)
  const secs = p.ended && p.started ? Math.round(p.ended - p.started) : null
  return (
    <div className={`tool ${p.status}`}>
      <button type="button" className="tool-head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className={`ticon ${p.status}`}>{ICON[p.status]}</span>
        <span className="tname">{p.title}</span>
        <span className="targs">{argLine(p)}</span>
        {p.summary && <span className="tsum">{p.summary}</span>}
        {secs != null && secs > 1 && <span className="ttime">{secs}s</span>}
      </button>
      {p.status === 'running' && p.progress && (
        <div className="tprog"><div className="progress"><i style={{ width: `${(p.progress.done / Math.max(1, p.progress.total)) * 100}%` }} /></div><small>{p.progress.phase} {p.progress.done}/{p.progress.total}</small></div>
      )}
      {p.status === 'pending_approval' && (
        <div className="approve">
          <span>The agent wants to run this simulation ({argLine(p)}). It may take a few minutes.</span>
          <button className="btn" onClick={() => onApprove(true)}>Approve</button>
          <button className="btn ghost" onClick={() => onApprove(false)}>Reject</button>
        </div>
      )}
      {p.status === 'error' && <div className="terr">{p.error}</div>}
      {open && (
        <div className="tbody">
          <div><small>arguments</small><pre>{JSON.stringify(p.args, null, 1)}</pre></div>
          {p.result !== undefined && <div><small>result</small><pre>{JSON.stringify(p.result, null, 1).slice(0, 4000)}</pre></div>}
        </div>
      )}
    </div>
  )
}

function Message({ m, onApprove }: { m: AgentMessage; onApprove: (callId: string, ok: boolean) => void }) {
  if (m.role === 'user') return <div className="msg user"><Markdown text={m.text} /></div>
  const secs = m.ended ? Math.round(m.ended - m.started) : null
  return (
    <div className="msg assistant">
      {m.parts.map((p: MsgPart, i) => (
        <Fragment key={i}>
          {p.type === 'text' && <Markdown text={p.text} />}
          {p.type === 'error' && <div className="terr">{p.text}</div>}
          {p.type === 'tool' && <ToolBlock p={p} onApprove={ok => onApprove(p.call_id, ok)} />}
        </Fragment>
      ))}
      {!m.ended && <div className="thinking"><span /><span /><span /></div>}
      {m.ended && <div className="meta-line">{m.model ?? ''}{secs != null ? ` · ${secs}s` : ''}</div>}
    </div>
  )
}

// ---------- page ----------
export default function Strategies() {
  const app = useApp()
  const [sessions, setSessions] = useState<AgentSessionMeta[]>([])
  const [sid, setSid] = useState<string | null>(() => { try { return localStorage.getItem(SESSION_KEY) } catch { return null } })
  const [session, setSession] = useState<AgentSession | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [suggs, setSuggs] = useState<Suggestion[]>([])
  const [notes, setNotes] = useState<{ notes: StoreNote[]; constraints: { cabins_open: number; tills_open: number } } | null>(null)
  const [drawer, setDrawer] = useState(true)
  const [center, setCenter] = useState<{ runId: string; label: string } | null>(null)
  const [centerSummary, setCenterSummary] = useState<Summary | null>(null)
  const [preview, setPreview] = useState<TwinPreview | null>(null)
  const [shown, setShown] = useState<{ runId: string; label: string }[]>([])
  const handled = useRef(new Set<string>())
  const listRef = useRef<HTMLDivElement>(null)

  const reloadSide = useCallback(() => {
    api.suggestions().then(setSuggs).catch(() => {})
    api.notes().then(setNotes).catch(() => {})
  }, [])
  useEffect(() => { api.agentSessions().then(setSessions).catch(() => {}); reloadSide() }, [reloadSide])
  useEffect(() => { try { if (sid) localStorage.setItem(SESSION_KEY, sid) } catch { /* ignore */ } }, [sid])

  // load the session; poll while the agent works
  useEffect(() => {
    if (!sid) { setSession(null); return }
    let alive = true, timer = 0
    const tick = async () => {
      const s = await api.agentSession(sid).catch(() => null)
      if (!alive) return
      if (!s) { setSid(null); return }
      setSession(s)
      if (s.busy) timer = window.setTimeout(tick, 900)
      else api.agentSessions().then(setSessions).catch(() => {})
    }
    tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [sid, session?.busy])

  // react to what the tools did: show runs in the 3D, previews, refresh drawer
  useEffect(() => {
    if (!session) return
    for (const m of session.messages) {
      if (m.role !== 'assistant') continue
      for (const p of m.parts) {
        if (p.type !== 'tool' || p.status !== 'done' || handled.current.has(p.call_id)) continue
        handled.current.add(p.call_id)
        const fresh = Date.now() / 1000 - (p.ended ?? 0) < 60
        if (p.ui?.show_run) {
          const item = { runId: p.ui.show_run, label: p.ui.label ?? 'Simulation' }
          setShown(s => [item, ...s.filter(x => x.runId !== item.runId)].slice(0, 8))
          if (fresh) { setCenter(item); setPreview(null) }
        }
        if (p.ui?.preview && fresh) setPreview({ layout: p.ui.preview.layout, strategy: p.ui.preview.strategy, key: Date.now() })
        if (['propose_suggestions', 'test_strategy', 'optimize', 'save_note', 'add_to_plan'].includes(p.name) && fresh) {
          reloadSide()
          if (p.name === 'add_to_plan') api.plan().then(app.setPlan).catch(() => {})
        }
      }
    }
  }, [session, reloadSide, app.setPlan])

  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' }) }, [session?.messages.length, session?.updated])

  // what the twin shows: the agent's latest run, else the latest store analysis
  const centerRun = center?.runId ?? app.runId
  useEffect(() => {
    setCenterSummary(null)
    if (centerRun) api.run(centerRun).then(setCenterSummary).catch(() => {})
  }, [centerRun])

  async function newSession() {
    const s = await api.agentNew(); setSid(s.id); setSession(s)
    api.agentSessions().then(setSessions).catch(() => {})
  }
  async function send(t?: string) {
    const body = (t ?? text).trim()
    if (!body) return
    setError(null)
    let id = sid
    if (!id) { const s = await api.agentNew(); id = s.id; setSid(id) }
    try { const s = await api.agentSend(id, body); setSession(s); setText('') } catch (e) { setError((e as Error).message) }
  }
  const approve = (callId: string, ok: boolean) => { if (sid) api.agentApprove(sid, callId, ok).catch(() => {}) }
  const showSuggestion = async (s: Suggestion) => {
    const p = await api.preview(s.strategy); setPreview({ layout: p.layout, strategy: p.strategy, key: Date.now() })
  }
  const busy = session?.busy ?? false
  const centerLabel = useMemo(() => preview ? 'Strategy preview (not simulated)' : center ? `${center.label} · simulated peak day` : 'Current store · latest analysis', [preview, center])

  return (
    <div className={`ws ${drawer ? '' : 'drawer-closed'}`}>
      <aside className="ws-drawer" aria-label="Suggestions">
        <button type="button" className="drawer-toggle" onClick={() => setDrawer(d => !d)} aria-label={drawer ? 'Close suggestions' : 'Open suggestions'}>{drawer ? '‹' : '›'}</button>
        {drawer && (
          <div className="drawer-body">
            <h3>Suggestions <small>{suggs.length}</small></h3>
            <p className="sub">The strategies the agent suggests in the chat end up here.</p>
            {suggs.length === 0 && <div className="empty small">Ask the agent what to do: its suggestions will show up here.</div>}
            {suggs.map(s => (
              <article className="sugg" key={s.id}>
                <div className="sugg-head">
                  <b>{s.title}</b>
                  <button type="button" className="x" onClick={async () => setSuggs(await api.removeSuggestion(s.id))} aria-label="Remove">×</button>
                </div>
                {s.verdict && <span className={`verdict ${VERDICT[s.verdict].cls}`}>{VERDICT[s.verdict].label}{s.profit != null ? ` · ${s.profit >= 0 ? '+' : ''}${eur(s.profit)}` : ''}</span>}
                <p>{s.why}</p>
                <ul className="levers">{s.levers.map(l => <li key={l}>{l}</li>)}</ul>
                <div className="sugg-actions">
                  <button className="btn ghost" onClick={() => showSuggestion(s)}>Show in 3D</button>
                  <button className="btn ghost" disabled={busy} onClick={() => send(`Test the suggestion “${s.title}” (${s.levers.join('; ')}) in the twin and tell me whether it is worth it.`)}>Test</button>
                  {s.experiment_id && <button className="btn ghost" disabled={app.plan.some(x => x.experiment_id === s.experiment_id)} onClick={async () => app.setPlan(await api.addPlan(s.experiment_id!))}>{app.plan.some(x => x.experiment_id === s.experiment_id) ? 'In the plan' : 'Add to plan'}</button>}
                </div>
              </article>
            ))}
            <h3 style={{ marginTop: 18 }}>Store notes <small>{notes?.notes.length ?? 0}</small></h3>
            <p className="sub">What you tell the agent. Simulations run with {notes?.constraints.cabins_open ?? 8} fitting rooms and {notes?.constraints.tills_open ?? 3} tills.</p>
            {notes?.notes.map(n => (
              <div className="note-item" key={n.id}>
                <span>{n.text}</span>
                <button type="button" className="x" onClick={async () => setNotes(await api.removeNote(n.id))} aria-label="Remove">×</button>
              </div>
            ))}
          </div>
        )}
      </aside>

      <section className="ws-center">
        <div className="center-head">
          <div><b>Digital twin</b><span className="note"> · {centerLabel}</span></div>
          <div className="center-actions">
            {preview && <button className="btn ghost" onClick={() => setPreview(null)}>Close preview</button>}
            {shown.length > 0 && (
              <select aria-label="Simulations shown" value={center?.runId ?? ''} onChange={e => { const x = shown.find(s => s.runId === e.target.value); setCenter(x ?? null); setPreview(null) }}>
                <option value="">Latest store analysis</option>
                {shown.map(s => <option key={s.runId} value={s.runId}>{s.label}</option>)}
              </select>
            )}
          </div>
        </div>
        {centerRun ? <Twin3D key={centerRun} runId={centerRun} summary={centerSummary} embedded preview={preview} /> : <div className="empty">Analyse the store or ask the agent for a simulation: it will show up here.</div>}
      </section>

      <section className="ws-chat" aria-label="Agent">
        <div className="chat-head">
          <select aria-label="Conversations" value={sid ?? ''} onChange={e => setSid(e.target.value || null)}>
            <option value="">New conversation</option>
            {sessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
          <button className="btn ghost" onClick={newSession} title="New conversation">+ New</button>
        </div>
        <div className="chat-list" ref={listRef}>
          {!session?.messages.length && (
            <div className="chat-empty">
              <h3>FitPath agent</h3>
              <p>Ask me what to do in the store: I read the real sales, analyse the store, test strategies in the twin and tell you what they bring in. You can also tell me what you have noticed in store.</p>
              <div className="examples">{EXAMPLES.map(e => <button key={e} type="button" onClick={() => send(e)}>{e}</button>)}</div>
            </div>
          )}
          {session?.messages.map(m => <Message key={m.id} m={m} onApprove={approve} />)}
        </div>
        {error && <p className="note" style={{ color: '#ffb4a8', margin: '0 12px' }}>{error}</p>}
        <form className="chat-input" onSubmit={e => { e.preventDefault(); send() }}>
          <textarea id="agent-input" value={text} rows={3} placeholder={busy ? 'The agent is working…' : 'Message the agent (Enter to send, Shift+Enter for a new line)'}
            onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) send() } }} />
          <div className="chat-actions">
            <span className="note">{session?.messages.length ? `${session.messages.length} ${session.messages.length === 1 ? 'message' : 'messages'}` : ''}</span>
            {busy ? <button type="button" className="btn ghost" onClick={() => sid && api.agentStop(sid)}>Stop</button> : <button className="btn" disabled={!text.trim()}>Send</button>}
          </div>
        </form>
      </section>
    </div>
  )
}
