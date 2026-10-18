import { useEffect, useRef, useState } from 'react'
import { api, type Size, type SizeKey, type VolumeDay } from '../api'
import { JobProgress } from '../components'
import { useApp } from '../store'
import { Card, Empty, Kpi, eur, int, pct } from '../ui'
import Results from './Results'
import StoreModel from './StoreModel'
import Twin3D, { type TwinFocus } from './Twin3D'

const PRESETS = [
  { label: 'Black Friday week 2019', start: '2019-11-25', days: 7 },
  { label: 'Winter sale 2019', start: '2019-12-26', days: 7 },
  { label: 'Start of summer 2019', start: '2019-06-03', days: 7 },
  { label: 'A week in October 2019', start: '2019-10-07', days: 7 },
]
const dayFmt = (d: string) => new Date(d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })

export default function Store() {
  const app = useApp()
  const { summary, diagnosis, jobs, runs, runId } = app
  const [form, setForm] = useState({ start: '2019-11-25', days: 7, size: app.size as SizeKey, tills: 3, label: PRESETS[0].label })
  const [sizes, setSizes] = useState<Record<SizeKey, Size> | null>(null)
  const [preview, setPreview] = useState<VolumeDay[]>([])
  const [focus, setFocus] = useState<TwinFocus | null>(null)
  const [showSetup, setShowSetup] = useState(!summary)
  const twinRef = useRef<HTMLDivElement>(null)
  const job = jobs['analysis']
  const running = job?.state === 'running'

  useEffect(() => { api.sizes().then(r => setSizes(r.sizes)).catch(() => {}) }, [])
  useEffect(() => {
    const t = window.setTimeout(() => api.volume(form.start, form.days, form.size).then(setPreview).catch(() => setPreview([])), 250)
    return () => window.clearTimeout(t)
  }, [form.start, form.days, form.size])
  useEffect(() => { if (summary) setShowSetup(false) }, [summary])

  async function analyze(e: React.FormEvent) {
    e.preventDefault()
    const j = await api.startSim({ start: form.start, days: form.days, store_size: form.size, tills_open: form.tills, seed: 42, label: form.label || undefined })
    app.startJob('analysis', { ...j, done: 0, total: form.days })
    app.setSize(form.size)
  }
  const look = (f: { day: string; t: number }) => { setFocus({ ...f, key: Date.now() }); twinRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
  const maxLoss = Math.max(1, ...(diagnosis?.findings.map(f => f.loss_eur ?? 0) ?? [1]))

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My store · how it runs today</h1>
          <p>The digital twin replays a real period in your store: the customers of those days come in with their real receipts, walk between the fixtures, try items on and queue. The result is a diagnosis of <b>where and how much you are losing</b>, plus a 3D view to watch those moments.</p>
        </div>
        {summary && <button className="btn ghost" onClick={() => setShowSetup(s => !s)}>{showSetup ? 'Close' : 'Analyse another period'}</button>}
      </div>

      {(showSetup || !summary) && (
        <Card title="Which period do you want to analyse?" sub="A real period from the past: the twin uses the real receipts of those days.">
          <div className="tabs-inline">
            {PRESETS.map(p => <button key={p.label} type="button" aria-pressed={form.label === p.label} onClick={() => setForm(f => ({ ...f, start: p.start, days: p.days, label: p.label }))}>{p.label}</button>)}
          </div>
          <form className="sim" onSubmit={analyze}>
            <label className="f">From<input id="st-start" type="date" value={form.start} min={app.status?.date_range?.[0]} max={app.status?.date_range?.[1]} onChange={e => setForm(f => ({ ...f, start: e.target.value, label: '' }))} /></label>
            <label className="f">Days<input id="st-days" type="number" min={1} max={30} value={form.days} onChange={e => setForm(f => ({ ...f, days: +e.target.value, label: '' }))} /></label>
            <label className="f">Traffic<select id="st-size" value={form.size} onChange={e => setForm(f => ({ ...f, size: e.target.value as SizeKey }))}>
              {sizes ? (Object.entries(sizes) as [SizeKey, Size][]).map(([k, s]) => <option key={k} value={k}>{s.label} · {int(s.visitors[0])}–{int(s.visitors[1])} entries/day</option>) : <option value="medium">Medium traffic</option>}
            </select></label>
            <label className="f">Open tills<select id="st-tills" value={form.tills} onChange={e => setForm(f => ({ ...f, tills: +e.target.value }))}>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
            <button className="btn" disabled={running}>{running ? 'Analysing…' : 'Analyse'}</button>
          </form>
          {preview.length > 0 && (
            <div className="vol-days">
              {preview.slice(0, 14).map(v => (
                <div key={v.date} className="vol-day" title={`H&M index ${v.index.toFixed(2)}`}><span>{dayFmt(v.date)}</span><b className="num">{int(v.visitors[0])}–{int(v.visitors[1])}</b><small>expected entries</small></div>
              ))}
            </div>
          )}
          <JobProgress job={job} unit="days simulated" />
          {runs.length > 1 && (
            <label className="f" style={{ maxWidth: 520 }}>Or reopen a previous analysis
              <select id="st-run" value={runId ?? ''} onChange={e => app.setRunId(e.target.value)}>
                {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.label || r.layout} · {r.start} · {r.days} {r.days === 1 ? 'day' : 'days'}</option>)}
              </select>
            </label>
          )}
        </Card>
      )}
      {!showSetup && <JobProgress job={job} unit="days simulated" />}

      {!summary ? (running ? null : <Empty>Pick a period and press “Analyse”.</Empty>) : (
        <>
          <div className="kpis">
            <Kpi label="Period" value={<span style={{ fontSize: 15 }}>{summary.config.start}</span>} hint={`${summary.config.days} ${summary.config.days === 1 ? 'day' : 'days'} · ${summary.store_size?.label.toLowerCase() ?? ''}`} />
            <Kpi label="Visits" value={int(summary.totals.visits)} />
            <Kpi label="Conversion" value={pct(summary.totals.conversion)} hint="visits that end with a receipt" />
            <Kpi label="Revenue" value={eur(summary.totals.revenue)} />
            <Kpi label="Lost sales" value={eur(summary.totals.lost_eur)} hint="items picked up and left behind" />
          </div>

          <Card title="Where you lose sales" sub="Sorted by euros lost in the period. Items with a critical moment can be watched in 3D." source="sim">
            {!diagnosis ? <p className="note">Computing the diagnosis…</p> : (
              <div className="findings">
                {diagnosis.findings.map(f => (
                  <article className="finding" key={f.key}>
                    <div className="fhead">
                      <h3>{f.title}</h3>
                      <span className="prio">{f.area}</span>
                      {f.loss_eur != null && <b className="num loss">{eur(f.loss_eur)}</b>}
                    </div>
                    {f.loss_eur != null && <div className="lossbar"><i style={{ width: `${(f.loss_eur / maxLoss) * 100}%` }} /></div>}
                    <p>{f.text}</p>
                    {f.focus && <button className="btn ghost" onClick={() => look(f.focus!)}>Watch in 3D · {f.focus_text}</button>}
                  </article>
                ))}
              </div>
            )}
          </Card>

          <div ref={twinRef} style={{ scrollMarginTop: 70 }}>
            <Card title="The store in 3D" sub="The analysed period replayed: every figure is a customer. Pick a day, press play and click a person to follow them across the cameras." source="sim">
              <Twin3D runId={runId} summary={summary} focus={focus} embedded />
            </Card>
          </div>

          <details className="more">
            <summary>All the numbers of the analysis (visits per day, queues per hour, departments, profiles, cameras)</summary>
            <Results s={summary} embedded />
          </details>
        </>
      )}
      <details className="more">
        <summary>The store and the model assumptions (with sources)</summary>
        <StoreModel />
      </details>
    </>
  )
}
