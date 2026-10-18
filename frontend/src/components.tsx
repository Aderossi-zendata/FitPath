// Shared pieces for the store and strategy sections
import { useState } from 'react'
import type { AnyJob, Delta, Experiment, Strategy } from './api'

export const VERDICT: Record<Experiment['verdict'], { label: string; cls: string; help: string }> = {
  'worth it': { label: 'Worth it', cls: 'good', help: 'a safe gain: profit rises even in the worst case' },
  'not worth it': { label: 'Not worth it', cls: 'bad', help: 'profit falls even in the best case' },
  uncertain: { label: 'Uncertain', cls: 'unsure', help: "the effect can't be told apart from chance: run more replicas or try it in store" },
}

export const strip = (s: Strategy): Strategy => { const { rationale: _r, source: _s, expected: _e, levers: _l, ...rest } = s; return rest }

export function JobProgress({ job, unit = 'simulations' }: { job?: AnyJob | null; unit?: string }) {
  if (!job || job.state === 'done') return null
  if (job.state === 'error') return <p className="note" style={{ color: '#ffb4a8' }}>Error: {job.error}</p>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div className="progress"><i style={{ width: `${(job.done / Math.max(1, job.total)) * 100}%` }} /></div>
      <span className="note">{job.phase && job.phase !== 'starting' ? `${job.phase}: ` : ''}{job.done}/{job.total} {unit} · you can switch pages, the work keeps running</span>
    </div>
  )
}

/** One KPI difference with its 90% interval, drawn as a small forest-plot row. */
export function DeltaRow({ label, d, fmt, better }: { label: string; d: Delta; fmt: (v: number) => string; better: 'up' | 'down' }) {
  const [lo, hi] = d.ci90 ?? [d.delta, d.delta]
  const m = Math.max(Math.abs(lo), Math.abs(hi), Math.abs(d.delta), 1e-9) * 1.15
  const x = (v: number) => 50 + (v / m) * 50
  const good = better === 'up' ? lo > 0 : hi < 0
  const bad = better === 'up' ? hi < 0 : lo > 0
  const color = good ? 'var(--ok-text)' : bad ? '#ff8a7a' : 'var(--muted)'
  return (
    <div className="drow">
      <span className="dl">{label}</span>
      <div className="dbar" aria-hidden>
        <i className="zero" />
        <i className="ci" style={{ left: `${x(lo)}%`, width: `${Math.max(.5, x(hi) - x(lo))}%`, background: color }} />
        <i className="dot" style={{ left: `${x(d.delta)}%`, background: color }} />
      </div>
      <b className="num" style={{ color }}>{d.delta > 0 ? '+' : ''}{fmt(d.delta)}</b>
      <small className="num">{d.ci90 ? `${fmt(lo)} to ${fmt(hi)}` : ''}</small>
    </div>
  )
}

/** A difference as compact cell text: "+€1,200 (+€300 to +€2,100)", coloured by reliability. */
export function DeltaCell({ d, fmt, better }: { d: Delta; fmt: (v: number) => string; better: 'up' | 'down' }) {
  const [lo, hi] = d.ci90 ?? [d.delta, d.delta]
  const good = better === 'up' ? lo > 0 : hi < 0
  const bad = better === 'up' ? hi < 0 : lo > 0
  const sign = (v: number) => (v > 0 ? '+' : '') + fmt(v)
  return (
    <span className={`dcell ${good ? 'good' : bad ? 'bad' : ''}`}>
      <b className="num">{sign(d.delta)}</b>
      {d.ci90 && <small className="num">{sign(lo)} / {sign(hi)}</small>}
    </span>
  )
}

const WIN = [['', 'No'], ['10-13', '10-13'], ['12-14', '12-14'], ['15-20', '15-20'], ['17-20', '17-20'], ['10-20', 'All day']] as const
const DAYS = [['all', 'every day'], ['weekend', 'weekend'], ['weekdays', 'weekdays'], ['month_end', 'month end']] as const
const GARMENTS = [['Knitwear', 'Knitwear'], ['Outdoor', 'Jackets and coats'], ['Trousers Denim', 'Jeans'], ['Dresses Ladies', 'Dresses'], ['Blouses', 'Blouses'], ['Shirts', 'Shirts'], ['Socks and Tights', 'Socks and tights'], ['Under-, Nightwear', 'Underwear and nightwear'], ['Swimwear', 'Swimwear'], ['Shorts', 'Shorts'], ['Shoes', 'Shoes'], ['Accessories', 'Accessories']] as const
const BUNDLES = [['Gloves', 'Hat/beanie'], ['Night gown', 'Pyjama bottom'], ['Bikini top', 'Swimwear bottom'], ['Bracelet', 'Necklace'], ['Hair string', 'Hair ties']] as const
const ZONES = [['ladies', 'Ladies'], ['divided', 'Divided'], ['men', 'Men'], ['kids', 'Kids'], ['denim', 'Denim'], ['basics', 'Basics'], ['accessories', 'Accessories'], ['new_in', 'New in']] as const
// department swaps allowed by the store rules (New in stays at the front, Ladies next to the fitting rooms)
const SWAPS = [['basics', 'divided', 'Basics by the tills, Divided in the centre'], ['denim', 'accessories', 'Swap Denim and Accessories'],
  ['divided', 'accessories', 'Divided in the window display, Accessories by the tills'], ['men', 'denim', 'Swap Men and Denim']] as const

export function Builder({ onTest, busy }: { onTest: (s: Strategy) => void; busy?: boolean }) {
  const [f, setF] = useState({ till: '', tillDays: 'weekend', att: '', attDays: 'all', layout: '', disc: '', pct: 20, discDays: 'weekend', cross: '', name: '', feature: '', clear: 0, mb: '', mbType: '3x2', bundle: '' })
  const set = (k: keyof typeof f, v: string | number) => setF(x => ({ ...x, [k]: v }))
  function build(): Strategy {
    const s: Strategy = { name: f.name || 'Custom strategy' }
    const win = (w: string) => { const [a, b] = w.split('-').map(Number); return { from: a, to: b } }
    if (f.till) s.extra_till = { ...win(f.till), days: f.tillDays }
    if (f.att) s.fitting_attendant = { ...win(f.att), days: f.attDays }
    if (f.layout) s.layout = { swap: f.layout.split('|') }
    if (f.disc) s.discount = [{ zone: f.disc, pct: f.pct, days: f.discDays }]
    if (f.cross) { const [a, b] = f.cross.split('>'); s.cross_merch = [{ from: a, to: b }] }
    if (f.feature) s.feature = { garment: f.feature, days: 'all' }
    if (f.clear) s.clearance = { pct: f.clear, days: 'all' }
    if (f.mb) s.multibuy = { zone: f.mb, type: f.mbType, days: 'all' }
    if (f.bundle) { const [a, b] = f.bundle.split('+'); s.bundle = [{ a, b, pct: 15 }] }
    return s
  }
  return (
    <form className="sim" onSubmit={e => { e.preventDefault(); onTest(build()) }}>
      <label className="f">Name<input id="b-name" value={f.name} placeholder="Custom strategy" onChange={e => set('name', e.target.value)} /></label>
      <label className="f">Extra till<select id="b-till" value={f.till} onChange={e => set('till', e.target.value)}>{WIN.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">…on which days<select id="b-tilld" value={f.tillDays} onChange={e => set('tillDays', e.target.value)}>{DAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">Fitting-room attendant<select id="b-att" value={f.att} onChange={e => set('att', e.target.value)}>{WIN.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">…on which days<select id="b-attd" value={f.attDays} onChange={e => set('attDays', e.target.value)}>{DAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">Layout<select id="b-lay" value={f.layout} onChange={e => set('layout', e.target.value)}>
        <option value="">Current</option>{SWAPS.map(([a, b, l]) => <option key={a + b} value={`${a}|${b}`}>{l}</option>)}
      </select></label>
      <label className="f">Discount on<select id="b-disc" value={f.disc} onChange={e => set('disc', e.target.value)}><option value="">None</option>{ZONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">Discount %<input id="b-pct" type="number" min={5} max={50} step={5} value={f.pct} onChange={e => set('pct', +e.target.value)} /></label>
      <label className="f">…on which days<select id="b-discd" value={f.discDays} onChange={e => set('discDays', e.target.value)}>{DAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">Cross-merchandising<select id="b-cross" value={f.cross} onChange={e => set('cross', e.target.value)}>
        <option value="">None</option><option value="denim>accessories">Accessories in Denim</option><option value="ladies>accessories">Accessories in Ladies</option><option value="divided>accessories">Accessories in Divided</option><option value="basics>accessories">Accessories in Basics</option><option value="denim>basics">Basics in Denim</option>
      </select></label>
      <label className="f">Entrance window display<select id="b-feat" value={f.feature} onChange={e => set('feature', e.target.value)}><option value="">Nothing special</option>{GARMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">End-of-season sale<select id="b-clear" value={f.clear} onChange={e => set('clear', +e.target.value)}><option value={0}>No</option>{[20, 30, 40, 50].map(n => <option key={n} value={n}>−{n}% off-season</option>)}</select></label>
      <label className="f">Multi-buy promo<select id="b-mb" value={f.mb} onChange={e => set('mb', e.target.value)}><option value="">None</option>{ZONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="f">…type<select id="b-mbt" value={f.mbType} onChange={e => set('mbType', e.target.value)}><option value="3x2">3-for-2</option><option value="2x50">Second item half price</option></select></label>
      <label className="f">Bundle (bought together)<select id="b-bundle" value={f.bundle} onChange={e => set('bundle', e.target.value)}><option value="">None</option>{BUNDLES.map(([a, b]) => <option key={a + b} value={`${a}+${b}`}>{a} + {b}</option>)}</select></label>
      <button className="btn" disabled={busy}>Test in the twin</button>
    </form>
  )
}
