import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Summary } from '../api'
import { AXIS, BarCell, Card, Empty, GRID, HEX, Kpi, Tip, Toggle, eur, int, mmss, pct } from '../ui'

const DAYTYPES = ['weekday', 'saturday', 'sunday'] as const
const DAYTYPE_LABEL = { weekday: 'Weekdays', saturday: 'Saturday', sunday: 'Sunday' } as const
const shortDate = (d: string | number) => new Date(String(d)).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })

export default function Results({ s, embedded }: { s: Summary | null; embedded?: boolean }) {
  const [daytype, setDaytype] = useState<(typeof DAYTYPES)[number]>('saturday')
  const hourly = useMemo(() => (s?.hourly ?? []).filter(h => h.daytype === daytype && h.slot <= 20 * 3600), [s, daytype])
  if (!s) return <Empty>Pick or run an analysis in My store.</Empty>
  const t = s.totals, f = s.fitting, m = s.matching
  const availableTypes = DAYTYPES.filter(d => s.hourly.some(h => h.daytype === d))
  const maxZone = { reach: Math.max(...s.zones.map(z => z.reach)), rev: Math.max(...s.zones.map(z => z.revenue)) }

  return (
    <>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1>Results · {s.config.layout.name}</h1>
            <p>{s.config.start}, {s.config.days} {s.config.days === 1 ? 'day' : 'days'} · {s.store_size ? `${s.store_size.label.toLowerCase()} (${s.store_size.visitors[0]}–${s.store_size.visitors[1]} entries on an average day) · ` : ''}{s.config.layout.cabins_open} fitting rooms · {s.config.layout.tills_open} tills. {s.note}</p>
          </div>
        </div>
      )}

      <div className="kpis">
        <Kpi label="Visits" value={int(t.visits)} hint={`${int(t.browsers)} with no intent to buy`} />
        <Kpi label="Conversion" value={pct(t.conversion)} hint="receipts / visits" />
        <Kpi label="Revenue" value={eur(t.revenue)} hint={`out of ${eur(t.intent_eur)} of real purchase intent`} />
        <Kpi label="Lost sales" value={eur(t.lost_eur)} hint="items picked up and left behind" />
        <Kpi label="Average receipt" value={eur(t.avg_ticket, 2)} />
        <Kpi label="Time in store" value={`${t.avg_minutes.toFixed(1)} min`} />
      </div>

      <div className="grid g2">
        <Card title="Visits and receipts per day" sub="Volume follows the real H&M days: Saturdays and month ends are busier." source="sim">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={s.daily} barGap={2}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} />
                <YAxis {...AXIS} width={40} />
                <Tooltip content={<Tip labelFmt={shortDate} fmt={v => int(v)} />} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="visits" name="Visits" fill={HEX[0]} radius={[4, 4, 0, 0]} />
                <Bar dataKey="receipts" name="Receipts" fill={HEX[2]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Revenue per day" source="sim">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={s.daily}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} />
                <YAxis {...AXIS} width={52} tickFormatter={v => `€${Math.round(v / 1000)}k`} />
                <Tooltip content={<Tip labelFmt={shortDate} fmt={v => eur(v)} />} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
                <Bar dataKey="revenue" name="Revenue" fill={HEX[0]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="People in store through the day" sub="Average per 15-minute slot" source="sim" right={<Toggle options={availableTypes} value={daytype} onChange={setDaytype} labels={DAYTYPE_LABEL} />}>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <LineChart data={hourly}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="time" {...AXIS} interval={7} />
                <YAxis {...AXIS} width={32} />
                <Tooltip content={<Tip fmt={v => v.toFixed(1)} />} />
                <Line dataKey="in_store" name="In store" stroke={HEX[0]} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Queues through the day" sub="People waiting, average per 15-minute slot" source="sim" right={<Toggle options={availableTypes} value={daytype} onChange={setDaytype} labels={DAYTYPE_LABEL} />}>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <LineChart data={hourly}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="time" {...AXIS} interval={7} />
                <YAxis {...AXIS} width={32} />
                <Tooltip content={<Tip fmt={v => v.toFixed(1)} />} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="fit_queue" name="Fitting-room queue" stroke={HEX[1]} strokeWidth={2} dot={false} />
                <Line dataKey="till_queue" name="Till queue" stroke={HEX[0]} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid g3">
        <Card title="Fitting rooms" sub={`${f.cabins_open} rooms open`} source="sim">
          <div className="kpis">
            <Kpi label="Average wait" value={mmss(f.avg_wait_s)} hint={`90th percentile ${mmss(f.p90_wait_s)}`} />
            <Kpi label="Give up" value={pct(f.abandon_share, 1)} hint={`${eur(f.lost_eur)} lost`} />
          </div>
          <p className="sub">Conversion with a wait of up to {mmss(f.long_wait_threshold_s)}: <b className="num">{pct(f.conv_short_wait)}</b>, longer: <b className="num">{pct(f.conv_long_wait)}</b> ({int(f.users)} fitting-room visits).</p>
        </Card>
        <Card title="Tills" sub={`${s.tills.tills_open} tills open`} source="sim">
          <div className="kpis">
            <Kpi label="Average wait" value={mmss(s.tills.avg_wait_s)} hint={`90th percentile ${mmss(s.tills.p90_wait_s)}`} />
            <Kpi label="Walk-outs" value={int(s.tills.abandons)} hint={`${eur(s.tills.lost_eur)} lost`} />
          </div>
        </Card>
        <Card title="Receipt → track matching" sub="Checked against the simulation ground truth" source="sim">
          <div className="kpis">
            <Kpi label="Correct" value={pct(m.correct_share, 1)} hint={`${int(m.receipts)} receipts`} />
            <Kpi label="Camera coverage" value={pct(m.camera_coverage, 1)} hint={`${int(m.id_switches)} identity switches`} />
          </div>
          <p className="sub">A match is correct when at least 90% of the track at the till belongs to the person who paid. Errors come from tracks swapped when two people cross paths.</p>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Store departments" sub="Reach = share of visits that stop in the department; conversion = stops with at least one purchase" source="sim">
          <div className="scroll-x">
            <table className="t">
              <thead><tr><th>Department</th><th>Reach</th><th className="r">Average stop</th><th className="r">Conversion</th><th>Revenue</th></tr></thead>
              <tbody>
                {[...s.zones].sort((a, b) => b.reach - a.reach).map(z => (
                  <tr key={z.zone}>
                    <td>{z.name}</td>
                    <td><BarCell value={z.reach} max={maxZone.reach} label={pct(z.reach)} /></td>
                    <td className="r num">{mmss(z.dwell_s)}</td>
                    <td className="r num">{pct(z.conversion)}</td>
                    <td><BarCell value={z.revenue} max={maxZone.rev} label={eur(z.revenue)} color={HEX[2]} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Customer profiles in store" sub="Profiles derived by clustering H&M customers" source="sim">
          <div className="scroll-x">
            <table className="t">
              <thead><tr><th>Profile</th><th className="r">Visits</th><th className="r">Conversion</th><th className="r">Receipt</th><th className="r">Minutes</th><th className="r">Fitting room</th></tr></thead>
              <tbody>
                {[...s.personas].sort((a, b) => b.visits - a.visits).map(p => (
                  <tr key={p.persona}>
                    <td>{p.name}</td><td className="r num">{int(p.visits)}</td><td className="r num">{pct(p.conversion)}</td>
                    <td className="r num">{eur(p.avg_ticket, 2)}</td><td className="r num">{p.minutes.toFixed(1)}</td><td className="r num">{pct(p.fitting)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card title="Best sellers in the simulation" sub="Real H&M articles sold on the simulated days" source="sim">
        <div className="scroll-x">
          <table className="t">
            <thead><tr><th>Article</th><th>Type</th><th>Department</th><th className="r">Items</th><th className="r">Revenue</th></tr></thead>
            <tbody>
              {s.top_articles.slice(0, 10).map(a => (
                <tr key={a.article_id}><td>{a.name}</td><td>{a.ptype}</td><td>{a.zone}</td><td className="r num">{a.units}</td><td className="r num">{eur(a.revenue)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
