import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type LayoutResponse, type ReplayVisit, type StoreLayout, type Summary } from '../api'
import { CHUNK_S, buildChunk, chunkRange, clock, idxAt, poseAt, type Chunk } from '../twin/replay'
import { PERSONA_HEX, StoreTwin, camsFrom, coverCam, type Cam, type Pose, type StrategyLike } from '../twin/scene'
import { Empty, Source, Toggle, eur, int } from '../ui'

const SPEEDS = ['30×', '60×', '120×', '300×'] as const
type Speed = (typeof SPEEDS)[number]
const DEFAULT_CAM = 'Entrance · New in'

type FollowInfo = { vid: number; track: number | null; cam: Cam | null; seq: string[]; gap: boolean; inStore: boolean }
type Live = { inStore: number; entered: number; receipts: number; revenue: number; fitting: number }
type Row = { vid: number; track: number; persona: number; enter: number }

export type TwinFocus = { day: string; t: number; key: number }
export type TwinPreview = { layout: StoreLayout; strategy: StrategyLike; key: number }

export default function Twin3D({ runId, summary, focus, embedded, preview }: { runId: string | null; summary: Summary | null; focus?: TwinFocus | null; embedded?: boolean; preview?: TwinPreview | null }) {
  const stageRef = useRef<HTMLDivElement>(null), canvasRef = useRef<HTMLCanvasElement>(null), labelsRef = useRef<HTMLDivElement>(null)
  const pipRef = useRef<HTMLDivElement>(null), pipCanvasRef = useRef<HTMLCanvasElement>(null)
  const twinRef = useRef<StoreTwin | null>(null)
  const [layoutRes, setLayoutRes] = useState<LayoutResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const days = useMemo(() => summary?.daily.map(d => d.date) ?? [], [summary])
  const [day, setDay] = useState<string | null>(null)
  const [visits, setVisits] = useState<ReplayVisit[] | null>(null)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState<Speed>('60×')
  const [camsOn, setCamsOn] = useState(false)
  const [pipOn, setPipOn] = useState(true)
  const [pipCamName, setPipCamName] = useState(DEFAULT_CAM)
  const [follow, setFollow] = useState<number | null>(null)
  const [tUi, setTUi] = useState(0)
  const [loading, setLoading] = useState(false)
  const [live, setLive] = useState<Live>({ inStore: 0, entered: 0, receipts: 0, revenue: 0, fitting: 0 })
  const [rows, setRows] = useState<Row[]>([])
  const [fInfo, setFInfo] = useState<FollowInfo | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  // mutable replay state read by the render loop
  const S = useRef({
    T: 0, t0: 0, t1: 0, playing: true, speed: 60, follow: null as number | null,
    chunks: new Map<number, Chunk | 'loading'>(), key: '', visits: new Map<number, ReplayVisit>(),
    lastFollow: null as { x: number; z: number } | null, lastTrack: null as number | null, seqLen: 0, uiAt: 0, pipCam: DEFAULT_CAM, pipOn: true,
  })

  useEffect(() => { api.layout().then(setLayoutRes).catch(e => setError(`Layout unavailable: ${e.message}`)) }, [])
  useEffect(() => { setDay(d => (d && days.includes(d) ? d : days[0] ?? null)) }, [days])
  // jump to a moment chosen elsewhere (e.g. a bottleneck in the diagnosis)
  const pending = useRef<TwinFocus | null>(null)
  useEffect(() => {
    if (!focus) return
    pending.current = focus
    if (focus.day === day && S.current.t1 > 0) { S.current.T = focus.t; S.current.seqLen = 0; setTUi(focus.t); setPlaying(true); pending.current = null }
    else setDay(focus.day)
  }, [focus])
  useEffect(() => { S.current.playing = playing }, [playing])
  useEffect(() => { S.current.speed = parseInt(speed) }, [speed])
  useEffect(() => { S.current.follow = follow; S.current.lastFollow = null; S.current.lastTrack = null; S.current.seqLen = 0 }, [follow])
  useEffect(() => { S.current.pipCam = pipCamName }, [pipCamName])
  useEffect(() => { S.current.pipOn = pipOn }, [pipOn])
  useEffect(() => { twinRef.current?.setCamsVisible(camsOn) }, [camsOn])
  useEffect(() => { if (preview && twinRef.current) { twinRef.current.animateLayout(preview.layout); twinRef.current.setStrategy(preview.strategy) } }, [preview])

  const personaName = useCallback((p: number) => summary?.personas.find(x => x.persona === p)?.name ?? `Profile ${p}`, [summary])
  const cams = useMemo(() => (layoutRes ? camsFrom(layoutRes) : []), [layoutRes])

  // the visits of the day give the opening hours; tracks come later, an hour at a time
  useEffect(() => {
    if (!runId || !day) return
    const s = S.current
    s.key = `${runId}|${day}`; s.chunks = new Map(); s.visits = new Map()
    setVisits(null); setFollow(null)
    let alive = true
    api.replay(runId, day, 0, 0).then(r => {
      if (!alive) return
      r.visits.forEach(v => s.visits.set(v.visit_id, v))
      const t0 = Math.min(...r.visits.map(v => v.enter_t)), t1 = Math.max(...r.visits.map(v => v.exit_t))
      s.t0 = Math.floor(t0 / 60) * 60; s.t1 = Math.ceil(t1 / 60) * 60; s.T = s.t0
      if (pending.current && pending.current.day === day) { s.T = Math.max(s.t0, Math.min(s.t1, pending.current.t)); pending.current = null }
      setTUi(s.T); setVisits(r.visits)
    }).catch(e => setError(e.message))
    return () => { alive = false }
  }, [runId, day])

  const ensureChunk = useCallback((h: number) => {
    const s = S.current
    if (!runId || !day || s.chunks.has(h) || h * CHUNK_S > s.t1 || (h + 1) * CHUNK_S < s.t0) return
    s.chunks.set(h, 'loading')
    const key = s.key, [a, b] = chunkRange(h)
    api.replay(runId, day, a, b).then(r => { if (S.current.key === key) S.current.chunks.set(h, buildChunk(r.tracks)) })
      .catch(e => { s.chunks.delete(h); setError(e.message) })
  }, [runId, day])

  // build the scene from /api/layout, using the run's own layout when it differs (what-if variants)
  useEffect(() => {
    if (!layoutRes || !stageRef.current || !canvasRef.current || !labelsRef.current || !pipRef.current || !pipCanvasRef.current) return
    // a run made with a strategy starts from today's store and transforms into the strategy's layout
    const strategy = (summary?.config as { strategy?: StrategyLike | null } | undefined)?.strategy ?? null
    const target = summary?.config.layout?.assign ? summary.config.layout : layoutRes.layout
    const layout = strategy ? { ...layoutRes.layout, cabins_open: target.cabins_open, tills_open: target.tills_open } : target
    const twin = new StoreTwin({ stage: stageRef.current, canvas: canvasRef.current, labels: labelsRef.current, pip: pipRef.current, pipCanvas: pipCanvasRef.current }, layoutRes, layout)
    twinRef.current = twin
    if (strategy) { twin.animateLayout(target); twin.setStrategy(strategy) }
    twin.setCamsVisible(camsOn)
    twin.onPick = vid => { if (vid != null) setFollow(vid) }
    twin.onFrame = dt => frame(twin, dt)
    return () => { twin.dispose(); twinRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutRes, summary])

  const frameRef = useRef<(twin: StoreTwin, dt: number) => void>(() => {})
  const frame = (twin: StoreTwin, dt: number) => frameRef.current(twin, dt)
  frameRef.current = (twin, dt) => {
    const s = S.current
    if (!s.visits.size) { twin.setPeople([], performance.now()); return }
    const h = Math.floor(s.T / CHUNK_S)
    ensureChunk(h)
    if (s.T % CHUNK_S > CHUNK_S - 600) ensureChunk(h + 1)
    // the followed visit may have started in the previous hour: its trail and cameras need it
    const fe = s.follow != null ? s.visits.get(s.follow)?.enter_t : undefined
    if (fe != null && fe < h * CHUNK_S) ensureChunk(h - 1)
    const ch = s.chunks.get(h)
    const ready = ch && ch !== 'loading'
    if (ready && s.playing) {
      s.T = Math.min(s.t1, s.T + dt * s.speed)
      if (s.T >= s.t1) { s.playing = false; setPlaying(false) }
    }
    const T = s.T, poses: Pose[] = []
    let followAt: Pose | null = null
    if (ready) {
      for (const [vid, ser] of ch) {
        const a = poseAt(ser, T)
        if (!a) continue
        const p = { vid, track: a.track, x: a.x, z: a.z, heading: a.heading, persona: s.visits.get(vid)?.persona ?? 0 }
        poses.push(p)
        if (vid === s.follow) followAt = p
      }
    }
    twin.setPeople(poses, performance.now())

    // the followed person: trail so far and the ceiling cameras that saw them, in order
    const seq: string[] = []
    let cam: Cam | null = null, gap = false, inStore = false
    const fv = s.follow != null ? s.visits.get(s.follow) : undefined
    if (fv) {
      inStore = T >= fv.enter_t && T <= fv.exit_t
      const trail: [number, number][] = []
      let lastT = -1
      for (const k of [h - 1, h]) {
        const c = s.chunks.get(k)
        const ser = c && c !== 'loading' ? c.get(fv.visit_id) : undefined
        if (!ser) continue
        const n = idxAt(ser, T)
        // chunks overlap by a few seconds: skip samples already taken from the previous hour
        for (let i = 0; i <= n; i++) if (ser.t[i] > lastT) { trail.push([ser.x[i], ser.z[i]]); lastT = ser.t[i] }
      }
      if (followAt) trail.push([followAt.x, followAt.z])
      let cur: Cam | null = null
      for (const [x, z] of trail) { const c = coverCam(cams, x, z, cur); if (c && c !== cur) { seq.push(c.id); cur = c } }
      const at = followAt ? { x: followAt.x, z: followAt.z } : s.lastFollow
      if (followAt) { s.lastFollow = at; s.lastTrack = followAt.track }
      cam = followAt ? coverCam(cams, followAt.x, followAt.z, cur) : null
      gap = inStore && (!followAt || !cam)
      if (seq.length > s.seqLen && s.seqLen > 0 && seq.length - s.seqLen === 1) {
        setFlash(`HANDOFF ${seq[seq.length - 2]} → ${seq[seq.length - 1]}`)
        window.setTimeout(() => setFlash(null), 1200)
      }
      s.seqLen = seq.length
      twin.setFollow(fv.visit_id, inStore ? at : null, trail, cam)
      const pipCam = cam ?? cur ?? cams.find(c => c.name === s.pipCam) ?? cams[0]
      twin.pip = s.pipOn && pipCam ? { cam: pipCam, follow: inStore && at ? { ...at, label: `#${followAt?.track ?? s.lastTrack ?? '—'} · ${gap ? 'estimated' : 'reconstructed'}`, gap } : null } : null
      if (!cam && cur) cam = cur
    } else {
      twin.setFollow(null, null, [], null)
      const pc = cams.find(c => c.name === s.pipCam) ?? cams[0]
      twin.pip = s.pipOn && pc ? { cam: pc, follow: null } : null
    }

    // fitting rooms: people inside a visit who are not tracked right now
    let fitting = 0
    const now = performance.now()
    if (now - s.uiAt > 200) {
      s.uiAt = now
      let inside = 0, entered = 0, receipts = 0, revenue = 0
      const list: Row[] = []
      const tracked = new Map(poses.map(p => [p.vid, p]))
      for (const v of s.visits.values()) {
        if (v.enter_t <= T) entered++
        if (v.exit_t <= T && v.receipt_no != null) { receipts++; revenue += v.bought_eur }
        if (v.enter_t <= T && T <= v.exit_t) {
          inside++
          const p = tracked.get(v.visit_id)
          if (p) list.push({ vid: v.visit_id, track: p.track, persona: v.persona, enter: v.enter_t })
          else if (v.used_fitting && ready && T - v.enter_t > 10 && v.exit_t - T > 10) fitting++
        }
      }
      twin.setCabinsBusy(fitting)
      list.sort((a, b) => a.enter - b.enter)
      setTUi(T); setRows(list)
      setLive({ inStore: inside, entered, receipts, revenue, fitting })
      setLoading(!ready)
      setFInfo(fv ? { vid: fv.visit_id, track: followAt?.track ?? s.lastTrack, cam, seq, gap, inStore } : null)
    }
  }

  const seek = (t: number) => { S.current.T = t; S.current.seqLen = 0; setTUi(t) }
  const fv = fInfo ? visits?.find(v => v.visit_id === fInfo.vid) : undefined
  const s = S.current
  const cabinsOpen = summary?.config.layout?.cabins_open ?? layoutRes?.layout.cabins_open ?? 0

  if (!runId) return <Empty>Analyse a period to see the store in 3D.</Empty>

  return (
    <>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1>Twin 3D</h1>
            <p>The simulated day replayed in the store: every figure is a visit, labelled with the track ID the cameras give it. Click a person to follow them from camera to camera.</p>
          </div>
          <Source kind="sim" />
        </div>
      )}
      {error && <p className="note" style={{ color: '#ffb4a8' }}>{error}</p>}

      <section className="card twin-controls" aria-label="Replay controls">
        <label className="f">Day
          <select value={day ?? ''} onChange={e => setDay(e.target.value)}>
            {days.map(d => <option key={d} value={d}>{new Date(d + 'T12:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}</option>)}
          </select>
        </label>
        <button className="btn play" type="button" onClick={() => { if (!playing && s.T >= s.t1) seek(s.t0); setPlaying(p => !p) }} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? '❚❚' : '▶'}
        </button>
        <div className="scrub">
          <input type="range" min={s.t0} max={s.t1 || 1} step={10} value={tUi} onChange={e => seek(+e.target.value)} aria-label="Time of day" disabled={!visits} />
          <div className="ticks num"><span>{clock(s.t0).slice(0, 5)}</span><b>{clock(tUi)}</b><span>{clock(s.t1).slice(0, 5)}</span></div>
        </div>
        <div className="speed">
          <span className="note">Speed</span>
          <Toggle options={SPEEDS} value={speed} onChange={setSpeed} />
        </div>
      </section>

      <div className="twin">
        <div className="twin-stage" ref={stageRef}>
          <canvas ref={canvasRef} tabIndex={0} aria-label="3D view of the store with the people of the simulated day" />
          <div className="twin-labels" ref={labelsRef} />
          <div className="twin-kpis">
            <div><span>Time</span><b className="num">{clock(tUi).slice(0, 5)}</b></div>
            <div><span>In store</span><b className="num">{int(live.inStore)}</b></div>
            <div><span>Entries</span><b className="num">{int(live.entered)}</b></div>
            <div><span>Receipts</span><b className="num">{int(live.receipts)}</b></div>
            <div><span>Revenue</span><b className="num">{eur(live.revenue)}</b></div>
            <div><span>Fitting rooms</span><b className="num">{Math.min(live.fitting, cabinsOpen)}/{cabinsOpen}</b></div>
          </div>
          {loading && visits && <div className="twin-loading">Loading tracks…</div>}
          <div className="pip" ref={pipRef} hidden={!pipOn}>
            <canvas ref={pipCanvasRef} />
            <header>
              <span>{fInfo?.cam ? `${fInfo.cam.id} · ${fInfo.cam.name}` : (() => { const c = cams.find(c => c.name === pipCamName); return c ? `${c.id} · ${c.name}` : '' })()}</span>
              <em className={fInfo ? 'rep' : ''}>{fInfo ? '● REPLAY' : '● LIVE'}</em>
            </header>
            {flash && <div className="handoff">{flash}</div>}
            <footer className={fInfo?.gap ? 'gap' : ''}>
              {!fInfo ? 'processed in store · only x, y, t leave the store'
                : !fInfo.inStore ? (fv && tUi < fv.enter_t ? `enters at ${clock(fv.enter_t).slice(0, 5)}` : `left at ${fv ? clock(fv.exit_t).slice(0, 5) : '—'} · track closed`)
                : fInfo.gap ? (s.lastFollow && s.lastFollow.z < -5 ? 'in the fitting room · not filmed' : 'out of coverage · estimated position')
                : `track #${fInfo.track} · ${Math.max(0, fInfo.seq.length - 1)} handoffs`}
            </footer>
          </div>
          <div className="twin-chips">
            <button className="chip" type="button" aria-pressed={camsOn} onClick={() => setCamsOn(v => !v)}>Cameras</button>
            <button className="chip" type="button" aria-pressed={pipOn} onClick={() => setPipOn(v => !v)}>Camera view</button>
            <button className="chip plain" type="button" onClick={() => twinRef.current?.view('top')}>Top view</button>
            <button className="chip plain" type="button" onClick={() => twinRef.current?.view('wide')}>Perspective</button>
          </div>
        </div>

        <aside className="card twin-side">
          {fInfo && fv ? (
            <div className="follow">
              <div className="card-head">
                <h3>Track #{fInfo.track ?? '—'}</h3>
                <button className="btn ghost sm" type="button" onClick={() => setFollow(null)}>Stop following</button>
              </div>
              <p className="sub"><i className="dot" style={{ background: '#' + PERSONA_HEX[fv.persona % 7].toString(16).padStart(6, '0') }} />{personaName(fv.persona)} · {fv.kind === 'buyer' ? 'intends to buy' : 'just browsing'}</p>
              <dl className="facts">
                <div><dt>Entered</dt><dd className="num">{clock(fv.enter_t).slice(0, 5)}</dd></div>
                <div><dt>Left</dt><dd className="num">{clock(fv.exit_t).slice(0, 5)}</dd></div>
                <div><dt>Outcome</dt><dd>{fv.outcome}</dd></div>
                <div><dt>Spent</dt><dd className="num">{fv.bought_eur ? eur(fv.bought_eur, 2) : '—'}</dd></div>
                <div><dt>Fitting room</dt><dd>{fv.used_fitting ? 'yes' : 'no'}</dd></div>
                <div><dt>Visit</dt><dd className="num">{fv.visit_id}</dd></div>
              </dl>
              <h4 className="sec">Cameras crossed</h4>
              <p className="camseq">{fInfo.seq.length ? fInfo.seq.map((c, i) => <span key={i}>{i > 0 && ' → '}{i === fInfo.seq.length - 1 && !fInfo.gap ? <b>{c}</b> : c}</span>) : 'none so far'}</p>
            </div>
          ) : (
            <div className="follow">
              <h3>Follow a person</h3>
              <p className="sub">Click a figure in the store or pick a track below: the camera view switches from camera to camera as they move.</p>
              <label className="f">Camera view
                <select value={pipCamName} onChange={e => setPipCamName(e.target.value)}>
                  {cams.map(c => <option key={c.id} value={c.name}>{c.id} · {c.name}</option>)}
                </select>
              </label>
            </div>
          )}
          <h4 className="sec">In store now · {int(rows.length)} tracked</h4>
          <div className="who">
            {rows.slice(0, 120).map(r => (
              <button key={r.vid} type="button" aria-pressed={r.vid === follow} onClick={() => setFollow(r.vid)}>
                <i className="dot" style={{ background: '#' + PERSONA_HEX[r.persona % 7].toString(16).padStart(6, '0') }} />
                <b className="num">#{r.track}</b>
                <span>{personaName(r.persona)}</span>
                <small className="num">{Math.max(0, Math.round((tUi - r.enter) / 60))} min</small>
              </button>
            ))}
            {!rows.length && <p className="note">{visits ? 'Nobody in store at this time.' : 'Loading the day…'}</p>}
          </div>
        </aside>
      </div>
    </>
  )
}
