import { useMemo, useState } from 'react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Pair, Real } from '../api'
import { AXIS, Card, DOW, Empty, GRID, HEX, Kpi, Tip, Toggle, eur, int, pct } from '../ui'

const SEASONS = ['Winter', 'Spring', 'Summer', 'Autumn'] as const
const GROUP_ORDER = ['Ladieswear', 'Divided', 'Menswear', 'Baby/Children', 'Sport']
const monthFmt = (d: string | number) => new Date(String(d)).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })

function PairTable({ rows, names, title }: { rows: Pair[]; names?: Record<string, string>; title: string }) {
  const n = (x: string | number) => names?.[String(x)] ?? String(x)
  return (
    <div className="scroll-x">
      <table className="t">
        <thead><tr><th>{title}</th><th className="r">Receipts</th><th className="r">If A, also B</th><th className="r">Lift</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{n(r.a)} <span style={{ color: 'var(--muted)' }}>+</span> {n(r.b)}</td>
              <td className="r num">{int(r.baskets)}</td>
              <td className="r num">{pct(r.conf_a_to_b)}</td>
              <td className="r num">×{r.lift.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Market({ r, embedded }: { r: Real | null; embedded?: boolean }) {
  const [season, setSeason] = useState<(typeof SEASONS)[number]>('Winter')
  const [pairLevel, setPairLevel] = useState<'Categories' | 'Articles'>('Categories')
  const [pairSort, setPairSort] = useState<'Strongest link' | 'Most frequent'>('Strongest link')

  const monthly = useMemo(() => {
    if (!r) return []
    const by: Record<string, Record<string, number | string>> = {}
    for (const m of r.monthly_by_group) (by[m.month] ??= { month: m.month })[m.igroup] = m.revenue_eur
    return Object.values(by)
  }, [r])
  if (!r) return <Empty>Loading real data…</Empty>
  const o = r.overview
  const lift = r.season_lift[season]
  const liftRows = [...lift.up.map(x => ({ ...x, d: x.lift - 1 })), ...lift.down.map(x => ({ ...x, d: x.lift - 1 }))].sort((a, b) => b.d - a.d)
  const trendRows = [...r.trends.rising.slice(0, 7), ...r.trends.falling.slice(0, 7)].sort((a, b) => b.share_change - a.share_change)
  const pairs = pairLevel === 'Categories' ? r.basket_types : r.basket_articles
  const days = r.weekday.days.map(d => ({ ...d, name: DOW[d.dow] }))

  return (
    <>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1>Market · what the real receipts say</h1>
            <p>Analysis of H&amp;M store-channel transactions ({o.start} → {o.end}). These insights do not depend on the simulation. {r.price_note}.</p>
          </div>
        </div>
      )}

      <div className="kpis">
        <Kpi label="Receipts" value={int(o.baskets)} />
        <Kpi label="Items sold" value={int(o.units)} />
        <Kpi label="Customers" value={int(o.customers)} />
        <Kpi label="Articles" value={int(o.articles)} />
        <Kpi label="Average receipt" value={eur(o.avg_ticket_eur, 2)} hint={`${o.avg_basket_units} items`} />
      </div>

      <Card title="Weekly revenue" sub="Seasonal peaks and sale periods; the COVID effect shows in spring 2020" source="real">
        <div style={{ height: 240 }}>
          <ResponsiveContainer>
            <AreaChart data={r.weekly}>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="week" tickFormatter={monthFmt} {...AXIS} minTickGap={30} />
              <YAxis {...AXIS} width={56} tickFormatter={v => `€${(v / 1e6).toFixed(1)}M`} />
              <Tooltip content={<Tip labelFmt={l => `Week of ${new Date(String(l)).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`} fmt={v => eur(v)} />} />
              <Area dataKey="revenue_eur" name="Revenue" stroke={HEX[0]} strokeWidth={2} fill={HEX[0]} fillOpacity={.15} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="grid g2">
        <Card title="Monthly revenue by line" source="real">
          <div style={{ height: 250 }}>
            <ResponsiveContainer>
              <AreaChart data={monthly}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="month" tickFormatter={monthFmt} {...AXIS} minTickGap={24} />
                <YAxis {...AXIS} width={56} tickFormatter={v => `€${(v / 1e6).toFixed(1)}M`} />
                <Tooltip content={<Tip labelFmt={monthFmt} fmt={v => eur(v)} />} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {GROUP_ORDER.map((g, i) => <Area key={g} type="monotone" dataKey={g} stackId="1" stroke="#151c24" strokeWidth={2} fill={HEX[i]} fillOpacity={.9} />)}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Markdowns: average weekly discount" sub="Price paid relative to the highest price seen for the same article" source="real">
          <div style={{ height: 250 }}>
            <ResponsiveContainer>
              <LineChart data={r.markdowns}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="week" tickFormatter={monthFmt} {...AXIS} minTickGap={30} />
                <YAxis {...AXIS} width={40} tickFormatter={v => pct(v)} />
                <Tooltip content={<Tip labelFmt={l => `Week of ${new Date(String(l)).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`} fmt={v => pct(v, 1)} />} />
                <Line dataKey="avg_discount" name="Average discount" stroke={HEX[1]} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <Card title="What sells more (and less) each season" sub="The category's share in the season relative to its yearly share: +40% means it weighs 40% more than usual" source="real"
        right={<Toggle options={SEASONS} value={season} onChange={setSeason} />}>
        <div style={{ height: 30 * liftRows.length + 30 }}>
          <ResponsiveContainer>
            <BarChart data={liftRows} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid {...GRID} horizontal={false} vertical />
              <XAxis type="number" {...AXIS} tickFormatter={v => (v > 0 ? '+' : '') + pct(v)} />
              <YAxis type="category" dataKey="cat" {...AXIS} width={160} />
              <ReferenceLine x={0} stroke="#8c99a4" />
              <Tooltip content={<Tip fmt={v => (v > 0 ? '+' : '') + pct(v)} />} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
              <Bar dataKey="d" name="Versus average" radius={4}>
                {liftRows.map((x, i) => <Cell key={i} fill={x.d >= 0 ? HEX[0] : '#e66767'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="grid g2">
        <Card title="Receipts by day of the week" source="real">
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={days}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="name" {...AXIS} />
                <YAxis {...AXIS} width={52} tickFormatter={v => `${Math.round(v / 1000)}k`} />
                <Tooltip content={<Tip fmt={v => int(v)} />} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
                <Bar dataKey="baskets" name="Receipts" fill={HEX[0]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Payday effect" sub="Average revenue by day of the month, mean = 100" source="real">
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <LineChart data={r.month_day}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="dom" {...AXIS} />
                <YAxis {...AXIS} width={36} domain={['dataMin - 5', 'dataMax + 5']} tickFormatter={v => String(Math.round(v))} />
                <ReferenceLine y={100} stroke="#8c99a4" strokeDasharray="4 4" />
                <Tooltip content={<Tip labelFmt={l => `Day ${l}`} fmt={v => v.toFixed(0)} />} />
                <Line dataKey="index" name="Index" stroke={HEX[0]} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Bought together" sub="Lift = how much more often the pair shows up on the same receipt than by chance" source="real"
          right={<><Toggle options={['Categories', 'Articles'] as const} value={pairLevel} onChange={setPairLevel} /><Toggle options={['Strongest link', 'Most frequent'] as const} value={pairSort} onChange={setPairSort} /></>}>
          <PairTable rows={(pairSort === 'Strongest link' ? pairs.top_lift : pairs.most_common).slice(0, 12)} names={pairLevel === 'Articles' ? r.basket_articles.names : undefined} title={pairLevel === 'Categories' ? 'Category pair' : 'Article pair'} />
        </Card>
        <Card title={`Trends: last ${r.trends.weeks} weeks vs a year earlier`} sub="Change in sales share by product type" source="real">
          <div style={{ height: 30 * trendRows.length + 30 }}>
            <ResponsiveContainer>
              <BarChart data={trendRows} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid {...GRID} horizontal={false} vertical />
                <XAxis type="number" {...AXIS} tickFormatter={v => (v > 0 ? '+' : '') + pct(v)} />
                <YAxis type="category" dataKey="ptype" {...AXIS} width={140} />
                <ReferenceLine x={0} stroke="#8c99a4" />
                <Tooltip content={<Tip fmt={v => (v > 0 ? '+' : '') + pct(v)} />} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
                <Bar dataKey="share_change" name="Share change" radius={4}>
                  {trendRows.map((x, i) => <Cell key={i} fill={x.share_change >= 0 ? HEX[0] : '#e66767'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <Card title={`Best sellers · ${season}`} source="real" right={<Toggle options={SEASONS} value={season} onChange={setSeason} />}>
        <div className="scroll-x">
          <table className="t">
            <thead><tr><th>Article</th><th>Type</th><th>Line</th><th className="r">Items</th><th className="r">Revenue</th></tr></thead>
            <tbody>{r.top_by_season[season].map(a => <tr key={a.product_code}><td>{a.name}</td><td>{a.ptype}</td><td>{a.igroup}</td><td className="r num">{int(a.units)}</td><td className="r num">{eur(a.revenue_eur)}</td></tr>)}</tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
