import { useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, Bell,
  Check, ChevronRight, CircleHelp, Clock3, CloudLightning, Crosshair,
  Database, Gauge, Info, Layers, Pause, Play, Radio, RotateCcw,
  Shield, ShieldAlert, SlidersHorizontal, Sparkles, TrendingDown, Bug,
  Volume2, VolumeX, Waves, Wind, Zap,
} from 'lucide-react';
import {
  Area, AreaChart, CartesianGrid, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useMarketStream } from './hooks/useMarketStream.js';
import { useActiveSection } from './hooks/useActiveSection.js';
import { useStormSound } from './hooks/useStormSound.js';
import { MARKETS, marketById, normalisePair } from './lib/markets.js';
import RaceComparison from './components/RaceComparison.jsx';

const fmt = (value, digits = 0) => value == null ? '—' : Number(value).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const usd = (value, digits = 0) => value == null ? '—' : `${value < 0 ? '−' : ''}$${fmt(Math.abs(value), digits)}`;
const signedUsd = (value) => value == null ? '—' : `${value >= 0 ? '+' : '−'}$${fmt(Math.abs(value))}`;
const time = (timestamp) => timestamp ? new Date(timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : '—';
const levelLabel = { LOW: 'CALM', ELEVATED: 'ELEVATED', HIGH: 'HIGH RISK', CRITICAL: 'CRITICAL' };

function Badge({ children, tone = 'neutral', pulse = false }) {
  return <span className={`badge badge-${tone}`}><span className={`badge-dot${pulse ? ' pulse' : ''}`} />{children}</span>;
}

function SectionTitle({ eyebrow, title, aside, icon: Icon }) {
  return <div className="section-heading">
    <div className="section-heading-text">
      <div className="section-eyebrow">{Icon && <Icon size={13} />}{eyebrow}</div>
      <h2>{title}</h2>
    </div>
    {aside && <div className="section-aside">{aside}</div>}
  </div>;
}

function Sidebar({ source, backendSource, selectedPair, onPairChange, pairSnapshots, activeSection, onNavigate }) {
  const links = [
    { icon: Gauge, text: 'Overview', id: 'overview' },
    { icon: Activity, text: 'Storm Monitor', id: 'market' },
    { icon: Layers, text: 'Portfolio', id: 'portfolio' },
    { icon: SlidersHorizontal, text: 'Protection Lab', id: 'protection' },
  ];
  return <aside className="sidebar">
    <div className="logo-block">
      <div className="logo-mark"><Bug size={24} strokeWidth={2.5} /><span /></div>
      <div><div className="logo-text">COCCINELLE<span className="logo-flare">.</span></div><div className="logo-sub">THE MARGIN FORECAST</div></div>
    </div>
    <div className="sidebar-section-label">WORKSPACE</div>
    <nav aria-label="Main navigation" className="side-nav">
      {links.map(({ icon: Icon, text, id }) => <button key={id} type="button" aria-current={activeSection === id ? 'location' : undefined} className={`nav-link${activeSection === id ? ' active' : ''}`} onClick={() => onNavigate(id)}>
        <Icon size={18} strokeWidth={1.8} /><span>{text}</span>{activeSection === id && <span className="nav-marker" />}
      </button>)}
    </nav>
    <div className="sidebar-section-label" style={{ marginTop: 33 }}>MARKET WATCH</div>
    <div className="watch-pairs">
      {MARKETS.map((market) => {
        const snapshot = pairSnapshots[market.id];
        return <button type="button" key={market.id} onClick={() => onPairChange(market.id)}
          aria-pressed={selectedPair === market.id}
          className={`watch-pair ${selectedPair === market.id ? 'selected' : ''}`}>
          <div><span className="pair-bullet" />{market.label}</div>
          <span className="watch-pair-quote"><b>{fmt(snapshot?.frame.quote.mid, market.digits)}</b><small>{snapshot?.source === 'live' ? (backendSource === 'mock' ? 'MOCK' : 'ENGINE') : 'REPLAY'}</small></span>
        </button>;
      })}
    </div>
    <div className="sidebar-bottom">
      <div className="system-status"><span className="status-circle" /><div><b>{source === 'live' ? 'Backend connected' : 'Demo environment'}</b><small>{source === 'live' ? (backendSource === 'mock' ? 'Synthetic backend prices' : 'SGX engine historical replay') : 'SGX historical fixture quotes'}</small></div></div>
      <div className="sidebar-caption">DurHack 2026 <span>•</span> SGX Challenge 01</div>
    </div>
  </aside>;
}

function Topbar({ source, backendSource }) {
  return <header className="topbar">
    <div className="breadcrumb"><span>Workspace</span><ChevronRight size={14}/><strong>Command Centre</strong></div>
    <div className="topbar-right">
      <span className="topbar-date"><Clock3 size={14} /> 30 JUL 2026 · UTC</span>
      <Badge tone={source === 'live' ? 'safe' : 'warning'} pulse>{source === 'live' ? (backendSource === 'mock' ? 'MOCK STREAM · CONNECTED' : 'ENGINE REPLAY · CONNECTED') : source === 'connecting' ? 'CONNECTING' : 'HISTORICAL DEMO'}</Badge>
      <div className="avatar">C</div>
    </div>
  </header>;
}

function StormRing({ score, level }) {
  const angle = Math.max(0, Math.min(100, score)) * 3.6;
  const category = level === 'CRITICAL' ? 'critical' : level === 'HIGH' ? 'high' : level === 'ELEVATED' ? 'elevated' : 'safe';
  return <div className={`storm-ring ${category}`} style={{ '--storm-angle': `${angle}deg` }}>
    <div className="storm-ring-inner"><span className="storm-ring-number">{fmt(score)}</span><span className="storm-ring-unit">/ 100</span></div>
  </div>;
}

const WEATHER_LABELS = {
  LOW: 'CLEAR CONDITIONS',
  ELEVATED: 'GUSTS BUILDING',
  HIGH: 'STORM FRONT',
  CRITICAL: 'SEVERE STORM',
};

function StormAtmosphere({ level }) {
  return <div className={`storm-atmosphere storm-${level.toLowerCase()}`} aria-hidden="true">
    <div className="storm-cloud-layer" />
    <div className="storm-radar-sweep" />
    <div className="storm-pressure-grid" />
    <div className="storm-vortex"><span /><span /><span /></div>
    <div className="storm-lightning-glow" />
    {Array.from({ length: 11 }, (_, i) => <i key={`gust-${i}`} className="storm-gust" style={{ '--top': `${8 + i * 8}%`, '--delay': `${-i * 0.3}s`, '--length': `${135 + (i % 4) * 30}px` }} />)}
    {Array.from({ length: 16 }, (_, i) => <i key={`rain-${i}`} className="storm-rain" style={{ '--left': `${(i * 17) % 100}%`, '--delay': `${-i * 0.14}s`, '--duration': `${0.8 + (i % 3) * 0.24}s` }} />)}
  </div>;
}

function StatCards({ current, source }) {
  const p = current.portfolio;
  const prob = current.forecast.breachProbability;
  const score = current.storm.score;
  const bufferRatio = p.maintenanceMargin ? Math.max(0, p.buffer / p.maintenanceMargin) : null;
  return <div className="stat-grid">
    <article className={`stat-card stat-storm weather-${current.storm.level.toLowerCase()}`}><div className="stat-label"><CloudLightning size={16} /> STORM INDEX <CircleHelp size={14} className="info-icon" /></div><div className="stat-main"><strong>{fmt(score)}<small>/100</small></strong><span className={`risk-text risk-${current.storm.level.toLowerCase()}`}>{levelLabel[current.storm.level] || current.storm.level}</span></div><div className="stat-footer">Market volatility & anomaly signals <ArrowUpRight size={15}/></div></article>
    <article className="stat-card"><div className="stat-label"><Shield size={16} /> MARGIN BUFFER</div><div className="stat-main"><strong className={p.buffer < 0 ? 'text-danger' : ''}>{usd(p.buffer)}</strong><span className={p.buffer < 0 ? 'negative-tag' : 'subtle-pill'}>{p.buffer < 0 ? 'BREACH' : 'AVAILABLE'}</span></div><div className="mini-meter"><span style={{ width: `${Math.max(0,Math.min(100,(bufferRatio ?? 0)*130))}%` }} /></div><div className="stat-footer">Equity {usd(p.equity)} <span>·</span> Required {usd(p.maintenanceMargin)}</div></article>
    <article className="stat-card"><div className="stat-label"><Waves size={16} /> MARGIN BREACH RISK</div><div className="stat-main"><strong>{prob == null ? '—' : `${Math.round(prob * 100)}%`}</strong><span className="subtle-pill">24H MODEL</span></div><div className="stat-footer">Estimated across simulated paths <ArrowRight size={14} /></div></article>
    <article className="stat-card"><div className="stat-label"><Zap size={16} /> SIGNAL LATENCY</div><div className="stat-main"><strong>{current.latency.medianMs == null ? '—' : `${fmt(current.latency.medianMs, source === 'live' ? 2 : 0)}ms`}</strong><span className="subtle-pill">P50</span></div><div className="stat-footer">P99: {current.latency.p99Ms == null ? '—' : `${fmt(current.latency.p99Ms, source === 'live' ? 2 : 0)}ms`} <span>·</span> {source === 'live' ? 'Backend-measured sample' : 'Illustrative timing'}</div></article>
  </div>;
}

function MarketChart({ frames, current, source, backendSource }) {
  const market = marketById(current.pair);
  const stormy = current.storm.level === 'HIGH' || current.storm.level === 'CRITICAL';
  const chartColor = stormy ? '#ffaf89' : '#83efd6';
  const data = useMemo(() => frames.map((frame) => ({ at: time(frame.timestamp), mid: frame.quote.mid, score: frame.storm.score, level: frame.storm.level })), [frames]);
  const first = data[0]?.mid ?? current.quote.mid;
  const delta = first ? (current.quote.mid / first - 1) * 100 : 0;
  const firstAlarm = data.find((row) => row.level === 'CRITICAL');
  return <section className={`panel chart-panel weather-${current.storm.level.toLowerCase()}`} id="market">
    <SectionTitle eyebrow="MARKET INTELLIGENCE" title={<>{market.label} <span>Spot Market</span></>} icon={Activity} aside={<Badge tone="safe">SPOT · {source === 'live' ? (backendSource === 'mock' ? 'MOCK FEED' : 'ENGINE REPLAY') : '2-MIN SAMPLE'}</Badge>} />
    <div className="price-headline"><span>{fmt(current.quote.mid, market.digits)}</span><div className={`price-change ${delta < 0 ? 'down' : 'up'}`}>{delta < 0 ? <ArrowDownRight size={17}/> : <ArrowUpRight size={17}/>} {Math.abs(delta) < .005 ? '0.00%' : `${delta > 0 ? '+' : ''}${delta.toFixed(2)}%`} <small>replay window</small></div></div>
    <div className={`market-graph weather-${current.storm.level.toLowerCase()}`} aria-label="Market price chart">
      <div className="market-impact-field" aria-hidden="true"><span className="market-impact-glow"/><span className="market-impact-scan"/><span className="market-impact-wave"/></div>
      {stormy && <div className={`market-strike-banner weather-${current.storm.level.toLowerCase()}`} role="status"><CloudLightning size={15}/><strong>{current.storm.level === 'CRITICAL' ? 'MARKET STORM DETECTED' : 'VOLATILITY FRONT APPROACHING'}</strong><small>{time(current.timestamp)} UTC</small></div>}
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 12, right: 0, bottom: 2, left: -12 }}>
          <defs><linearGradient id="priceShade" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={chartColor} stopOpacity={0.24}/><stop offset="100%" stopColor={chartColor} stopOpacity={0}/></linearGradient></defs>
          <CartesianGrid stroke="#263543" strokeDasharray="3 6" vertical={false}/>
          <XAxis dataKey="at" tick={{ fill: '#718395', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36} dy={12}/>
          <YAxis dataKey="mid" domain={['auto', 'auto']} orientation="right" tick={{ fill: '#718395', fontSize: 11 }} tickFormatter={(v) => v.toFixed(market.axisDigits)} tickLine={false} axisLine={false}/>
          <Tooltip contentStyle={{ background: '#141f2b', border: '1px solid #324455', borderRadius: 12, color: '#e5f2fa' }} labelStyle={{ color: '#aebcca' }} formatter={(v) => [Number(v).toFixed(market.digits), market.label]} />
          <Area type="linear" dataKey="mid" stroke={chartColor} fill="url(#priceShade)" strokeWidth={2.7} dot={false} isAnimationActive={false}/>
          {firstAlarm && <ReferenceLine x={firstAlarm.at} stroke="#ff9f69" strokeDasharray="4 4" strokeOpacity={0.8}/>}
        </AreaChart>
      </ResponsiveContainer>
    </div>
    <div className="chart-bottom"><div className="legend-item"><i className="legend-dot mint"/>{market.label} Mid</div><div className="legend-item"><i className="legend-line amber"/>Critical signal</div><div className="chart-hint">{source === 'live' ? (backendSource === 'mock' ? 'Synthetic API feed · development only' : 'SGX engine historical stream') : 'SGX-supplied historical quotes · 30 Jul 2026'}</div></div>
  </section>;
}

function RiskInsights({ current, source, activeAlert }) {
  const level = current.storm.level;
  const { enabled, toggleSound, previewSound } = useStormSound(level);
  return <section className={`panel insights-panel weather-${level.toLowerCase()}`} data-storm-level={level}>
    <StormAtmosphere level={level}/>
    <SectionTitle eyebrow="LIVE RISK SIGNALS" title="Storm radar" icon={Radio} aside={<div className="storm-meta"><span className={`storm-weather-tag weather-${level.toLowerCase()}`}><Wind size={12}/>{WEATHER_LABELS[level] || 'MARKET WATCH'}</span><button type="button" className={`storm-sound-toggle${enabled ? ' enabled' : ''}`} aria-pressed={enabled} onClick={toggleSound} title={enabled ? 'Mute storm sound effects' : 'Enable optional storm sound effects'}>{enabled ? <Volume2 size={13}/> : <VolumeX size={13}/>}<span>SFX {enabled ? 'ON' : 'OFF'}</span></button>{enabled && <button type="button" className="storm-sound-preview" onClick={previewSound} title="Preview the critical storm cue"><Play size={12}/></button>}<span className="small-clock">{time(current.timestamp)} UTC</span></div>} />
    <div className="radar-layout"><StormRing score={current.storm.score} level={level}/><div className="radar-copy"><div className="muted-label">CURRENT CONDITION</div><strong className={`condition-text risk-${level.toLowerCase()}`}>{levelLabel[level] || level}</strong><span>{level === 'CRITICAL' || level === 'HIGH' ? 'Elevated market stress detected.' : 'Monitoring for unusual price action.'}</span></div></div>
    <div className="alert-box"><div className="alert-box-title"><ShieldAlert size={17}/>{level === 'LOW' ? 'Market monitoring active' : 'Risk signal triggered'}<span className="new-indicator">{level === 'LOW' ? 'WATCHING' : 'ALERT'}</span></div><p>{level === 'LOW' ? `No critical signal for ${marketById(current.pair).label} at this timestamp. Coccinelle is tracking price, spread and volatility changes.` : `The latest ${marketById(current.pair).label} movement may threaten leveraged positions. Review exposure and margin headroom.`}</p></div>
    <div className="drivers-title">SIGNAL DRIVERS <span>{current.storm.drivers?.length || 0}</span></div>
    <div className="driver-list">{(current.storm.drivers || []).slice(0,3).map((driver, i) => <div key={i} className="driver-row"><span className="driver-check"><Check size={11}/></span><span>{driver}</span></div>)}</div>
    {source === 'live' && activeAlert?.explanation?.text && <div className="model-note"><Sparkles size={14}/>{activeAlert.explanation.text} <small>({activeAlert.explanation.source === 'llm' ? 'AI explanation' : 'Rule-based explanation'})</small></div>}
    {source !== 'live' && <div className="model-note"><Info size={14}/> Scores and alerts are illustrative frontend signals.</div>}
  </section>;
}

function Forecast({ current, source }) {
  const cone = current.forecast.cone || [];
  const data = cone.map((row) => ({ ...row, range: Math.max(0,row.upper - row.lower) }));
  return <section className="panel forecast-panel" id="forecast">
    <SectionTitle eyebrow="PROBABILISTIC OUTLOOK" title="Margin forecast" icon={Waves} aside={<span className="forecast-horizon">NEXT {fmt(current.forecast.horizonHours)} HOURS</span>} />
    <div className="forecast-overview"><div><span className="muted-label">MODEL-ESTIMATED BREACH RISK</span><strong>{current.forecast.breachProbability == null ? '—' : `${Math.round(current.forecast.breachProbability * 100)}%`}</strong></div><p>Equity outcomes under sampled FX market paths. The shaded band illustrates uncertainty.</p></div>
    <div className="cone-graph">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 9, left: -18, right: 8, bottom: 5 }}>
          <defs><linearGradient id="coneFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3eaac5" stopOpacity={0.35}/><stop offset="100%" stopColor="#3eaac5" stopOpacity={0.09}/></linearGradient></defs>
          <CartesianGrid vertical={false} stroke="#253441" strokeDasharray="3 6"/>
          <XAxis dataKey="hour" tick={{ fill: '#718395', fontSize: 10 }} tickLine={false} axisLine={false}/>
          <YAxis tickFormatter={(v) => `$${(v/1000).toFixed(0)}k`} tick={{ fill: '#718395', fontSize: 10 }} tickLine={false} axisLine={false}/>
          <Tooltip contentStyle={{ background: '#141f2b', border: '1px solid #324455', borderRadius: 12 }} formatter={(v, key) => [usd(v),key === 'range' ? 'Uncertainty width' : key]}/>
          <Area type="monotone" dataKey="lower" stackId="band" stroke="none" fill="transparent" isAnimationActive={false}/>
          <Area type="monotone" dataKey="range" stackId="band" stroke="#499eb2" strokeWidth={1} fill="url(#coneFill)" isAnimationActive={false}/>
          <Line type="monotone" dataKey="median" stroke="#9ddef4" strokeWidth={2.4} dot={false} isAnimationActive={false}/>
          {current.portfolio.maintenanceMargin != null && <ReferenceLine y={current.portfolio.maintenanceMargin} stroke="#ff9e83" strokeDasharray="5 5"/>}
        </AreaChart>
      </ResponsiveContainer>
    </div>
    <div className="forecast-legend"><span><i className="legend-line blue"/> Median equity</span><span><i className="legend-line coral"/> Maintenance margin</span><span><i className="legend-dot cyan"/> Scenario range</span></div>
    <div className="model-note"><Info size={14}/>{source === 'live' ? (current.forecast.breachProbability == null ? 'Backend forecast unavailable. Generate processed historical Parquet data to enable the model.' : 'Backend statistical model. Not a guaranteed prediction.') : 'Illustrative forecast until backend model is connected.'}</div>
  </section>;
}

function Portfolio({ current, source }) {
  const p = current.portfolio;
  const exposure = p.exposure || [];
  const colors = ['#69e4c5', '#598ed9', '#dcba78', '#a07ed1'];
  return <section className="panel portfolio-panel" id="portfolio">
    <SectionTitle eyebrow="POSITION INTELLIGENCE" title="Exposure X-Ray" icon={Layers} aside={<span className="sample-tag">{source === 'live' ? 'FICTIONAL BACKEND ACCOUNT' : 'SAMPLE PORTFOLIO'}</span>} />
    <div className="exposure-main"><div><span className="muted-label">UNREALISED P&L</span><strong className={p.pnl < 0 ? 'text-danger' : 'text-mint'}>{signedUsd(p.pnl)}</strong></div><div><span className="muted-label">NET LEVERAGE</span><strong>{p.leverage == null ? '—' : `${fmt(p.leverage,2)}×`}</strong></div></div>
    <div className="exposure-bar">{exposure.length ? exposure.map((position, i) => <span key={i} style={{ width: `${position.share == null ? 100/exposure.length : position.share * 100}%`, background: colors[i%colors.length] }} />) : <span style={{ width: '100%', background: colors[0], opacity: 0.5 }}/>}</div>
    <div className="exposure-rows">{exposure.map((position, i) => <div className="exposure-row" key={`${position.pair}-${i}`}><span className="exposure-name"><i style={{ background: colors[i % colors.length] }}/>{position.pair}</span><span className="exposure-side">{position.side}</span><b>{usd(position.notional)}</b></div>)}</div>
    {source === 'live' && !p.breakdownAvailable && <div className="model-note"><Info size={14}/> Aggregate net notional: {usd(p.notional)}. FastAPI does not yet publish per-position breakdowns.</div>}
    <div className="margin-health"><div><ShieldAlert size={17}/><span>Margin utilisation</span></div><strong>{p.equity && p.maintenanceMargin != null ? `${Math.round(p.maintenanceMargin / p.equity * 100)}%` : '—'}</strong></div>
    <div className="margin-health-bar"><span style={{ width: `${p.equity ? Math.min(100,p.maintenanceMargin/p.equity*100) : 0}%`, background: p.buffer < 10000 ? '#ff777d' : '#f3bd69' }} /></div>
  </section>;
}

function TideTable({ current, source }) {
  const rows = current.tideTable || [];
  return <section className="panel tide-panel"><div className="tide-title"><span><Waves size={16}/> MARKET TIDE TABLE</span><small>{source === 'live' ? 'Backend feed' : 'Illustrative window preview'}</small></div>{rows.length ? <div className="tide-hours">{rows.map((r) => <div key={r.hour} className="tide-hour"><span>{r.hour}</span><div className={`tide-cell ${r.risk.toLowerCase()}`}/><small>{r.risk}</small></div>)}</div> : <div className="model-note"><Info size={14}/> Tide-hour risk data is not included in this API stream yet.</div>}</section>;
}

function Protection({ current, source, backendSource, onProtect, onClearResult, onResetRace, protectionResult, protectionRace, actionPending, firstCriticalAt }) {
  const [action, setAction] = useState('reduce_50');
  const [judgeMode, setJudgeMode] = useState(false);
  const [judgeActionAt, setJudgeActionAt] = useState(null);
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => { setJudgeActionAt(null); setSubmitted(false); }, [current.pair]);
  useEffect(() => {
    const ts = protectionResult?.protection?.judge?.acted_at;
    if (protectionResult?.judgeMode && protectionResult.approved && ts) setJudgeActionAt(ts);
  }, [protectionResult]);
  const p = current.portfolio;
  const selectedNotional = p.exposure?.find((position) => normalisePair(position.pair) === normalisePair(current.pair))?.notional ?? p.notional ?? 0;
  const baseLoss = Math.abs(selectedNotional) * 0.02;
  const fraction = action === 'close_pair' ? 1 : action === 'reduce_50' ? 0.5 : 0;
  const adjustedLoss = baseLoss * (1 - fraction);
  const tradeNotional = selectedNotional * fraction;
  const fees = (tradeNotional || 0) * 0.0001;
  const demoBefore = p.buffer - baseLoss;
  const demoAfter = p.buffer - adjustedLoss - fees;
  const redAt = protectionResult?.protection?.red_at || firstCriticalAt;
  const judgeGapMinutes = redAt && judgeActionAt ? Math.round((Date.parse(judgeActionAt) - Date.parse(redAt)) / 60000) : null;
  const backendComparison = source === 'live' && protectionResult?.before && protectionResult?.portfolio;
  const backendVerdict = protectionResult?.review || protectionResult;

  function submit() {
    setSubmitted(true);
    if (judgeMode && source !== 'live' && action !== 'none') setJudgeActionAt(current.timestamp);
    onProtect(action, judgeMode);
  }

  return <section className="panel protection-panel" id="protection">
    <SectionTitle eyebrow="PROTECTION LAB" title="Choose your defence" icon={Shield} aside={<Badge tone="safe">PAPER ONLY</Badge>} />
    <div className="protection-intro"><p>{source === 'live' ? <>Run a <b>paper-only action at the backend's current FX quote</b>. Preview calls the Risk Officer; Judge Mode records your action so the backend can compare it with a first-red-signal strategy.</> : <>Compare paper actions against the <b>same illustrative additional 2% adverse move in {marketById(current.pair).label}</b>.</>}</p><div className="judge-toggle"><span><Crosshair size={16}/> Judge vs Coccinelle</span><button className={`switch ${judgeMode ? 'on' : ''}`} role="switch" aria-checked={judgeMode} onClick={() => {setJudgeMode(!judgeMode);setJudgeActionAt(null);setSubmitted(false);onClearResult();}}><span/></button></div></div>
    <div className="action-row">
      {[{ id: 'none', title: 'Hold position', subtitle: 'No reduction', icon: Shield },{ id: 'reduce_50', title: 'Reduce by 50%', subtitle: 'Selected pair only', icon: SlidersHorizontal },{ id: 'close_pair', title: 'Close selected pair', subtitle: 'Exit this FX exposure', icon: ShieldAlert }].map(({ id, title, subtitle, icon: Icon }) => <button key={id} type="button" disabled={actionPending} className={`strategy-option ${action === id ? 'selected' : ''}`} onClick={() => {setAction(id);setSubmitted(false);onClearResult();}}><Icon size={18}/><b>{title}</b><small>{subtitle}</small><span className="strategy-radio">{action === id && <span/>}</span></button>)}
    </div>
    <div className="compare-head"><span>{backendComparison ? `BACKEND QUOTE · ${time(protectionResult.as_of)} UTC` : `ILLUSTRATIVE STRESS: ADDITIONAL −2.0% ${marketById(current.pair).label} SHOCK`}</span><span>BEFORE / AFTER</span></div>
    <div className="compare-grid"><div className="compare-card"><small>{backendComparison ? 'BUFFER BEFORE ACTION' : 'UNPROTECTED LOSS'}</small><strong className="text-danger">{backendComparison ? usd(protectionResult.before.margin_buffer) : `−${usd(baseLoss)}`}</strong><span>{backendComparison ? `Equity: ${usd(protectionResult.before.equity)}` : `Estimated buffer: ${usd(demoBefore)}`}</span></div><div className="compare-arrow"><ArrowRight size={20}/></div><div className="compare-card protected"><small>{backendComparison ? 'BUFFER AFTER ACTION' : 'WITH SELECTED ACTION'}</small><strong className="text-mint">{backendComparison ? usd(protectionResult.portfolio.margin_buffer) : `−${usd(adjustedLoss + fees)}`}</strong><span>{backendComparison ? `Spread cost: ${usd(protectionResult.cost, 2)}` : `Estimated buffer: ${usd(demoAfter)}`}</span></div></div>
    {backendComparison && protectionResult.forecast && <div className="model-note"><Waves size={14}/> 24h breach probability on shared paths: {Math.round(protectionResult.forecast.before.breach_probability * 100)}% before → {Math.round(protectionResult.forecast.after.breach_probability * 100)}% after.</div>}
    <div className="protection-bottom"><div className="model-note"><Info size={14}/>{backendComparison ? 'Actual paper fills at bid/ask, Risk Officer reviewed.' : source === 'live' && judgeMode ? 'The 2% shock cards are illustrative. Real judge-vs-Coccinelle outcomes are shown in the backend comparison below.' : 'Illustrative linear shock estimate with 1bp trading cost. Backend previews use real quote spreads.'}</div><button className="protect-button" onClick={submit} disabled={actionPending}><Shield size={16}/>{actionPending ? 'CHECKING RISK...' : judgeMode ? 'PROTECT NOW' : 'SIMULATE ACTION'}<ArrowRight size={17}/></button></div>
    {submitted && protectionResult && <div className="action-confirmation">{protectionResult.error ? <ShieldAlert size={16}/> : <Check size={16}/>} {protectionResult.error ? `Backend error: ${protectionResult.error}` : protectionResult.held ? 'Holding position — no trade submitted.' : protectionResult.demo ? 'Demo action recorded; no order placed.' : backendVerdict?.approved ? (protectionResult.judgeMode ? 'Approved: judge paper protection recorded.' : 'Risk officer approved this paper preview. No portfolio mutation.') : `Risk officer vetoed the action: ${(backendVerdict?.reasons || ['See backend response']).join('; ')}`}{judgeActionAt && <span> · Judge acted at {time(judgeActionAt)} UTC</span>}</div>}
    {source !== 'live' && judgeMode && judgeActionAt && <div className="judge-comparison"><div><small>JUDGE REACTION</small><b>{time(judgeActionAt)} UTC</b></div><div><small>FIRST CRITICAL SIGNAL</small><b>{redAt ? `${time(redAt)} UTC` : 'Waiting for red alert'}</b></div><div><small>REACTION DIFFERENCE</small><b className={judgeGapMinutes != null && judgeGapMinutes > 0 ? 'text-danger' : 'text-mint'}>{judgeGapMinutes == null ? 'Pending' : judgeGapMinutes > 0 ? `${judgeGapMinutes} min later` : judgeGapMinutes < 0 ? `${Math.abs(judgeGapMinutes)} min earlier` : 'Same minute'}</b></div></div>}
    {source === 'live' && current.pair === 'USDJPY' && (judgeMode || protectionRace?.red_at || protectionRace?.judge) &&
      <RaceComparison race={protectionRace} backendSource={backendSource} onReset={() => {setJudgeActionAt(null);setSubmitted(false);onResetRace();}} resetting={actionPending}/>}
  </section>;
}

function ReplayControls({ stream }) {
  const { source, current, paused, speed, playPause, changeSpeed, jumpTo, reset, mockIndex, totalMockFrames, seek } = stream;
  return <div className="replay-dock">
    <div className="replay-ident"><div className="replay-icon"><Radio size={18}/></div><div><strong>{source === 'live' ? 'LIVE REPLAY' : 'HISTORICAL REPLAY'}</strong><span>{marketById(current.pair).label} <i/> {time(current.timestamp)} UTC</span></div></div>
    <div className="replay-transport"><button className="icon-control" onClick={reset} title="Restart replay" aria-label="Restart replay"><RotateCcw size={18}/></button><button className="play-control" onClick={playPause} title={paused ? 'Play replay' : 'Pause replay'} aria-label={paused ? 'Play replay' : 'Pause replay'}>{paused ? <Play size={19} fill="currentColor"/> : <Pause size={19} fill="currentColor"/>}</button><div className="replay-slider"><input aria-label="Replay position" type="range" min="0" max={totalMockFrames - 1} value={mockIndex} disabled={source === 'live'} title={source === 'live' ? 'Backend replay uses jump controls' : 'Scrub replay'} onChange={(e) => seek(Number(e.target.value))}/><div className="replay-track-label"><span>12:00</span><span>15:00 UTC</span></div></div></div>
    <div className="replay-options"><button className="jump-button" onClick={() => jumpTo('13:34')}><Zap size={14}/> Before the storm</button><button className="strike-button" onClick={() => { stream.selectPair('USDJPY'); jumpTo('13:38', 'USDJPY'); }} title="Jump to the first real red USD/JPY signal"><CloudLightning size={14}/> Strike moment</button><div className="speed-buttons">{[1, 5, 20].map((v) => <button key={v} className={speed === v ? 'current' : ''} onClick={() => changeSpeed(v)}>{v}×</button>)}</div></div>
  </div>;
}

export default function App() {
  const stream = useMarketStream();
  const { activeSection, navigateTo } = useActiveSection();
  const { current, source, frames } = stream;
  const firstCriticalAt = frames.find((frame) => frame.storm.level === 'CRITICAL')?.timestamp;
  return <div className="app-shell" id="overview">
    <Sidebar source={stream.connectionSource} backendSource={stream.backendSource} selectedPair={stream.selectedPair} onPairChange={stream.selectPair} pairSnapshots={stream.pairSnapshots} activeSection={activeSection} onNavigate={navigateTo}/>
    <main className="app-main">
      <Topbar source={source} backendSource={stream.backendSource}/>
      <div className="dashboard-content">
        <div className="page-intro"><div><div className="eyebrow-line"><span className="eyebrow-accent"/> RISK INTELLIGENCE PLATFORM <span className="sep-dot">•</span> 30 JULY 2026</div><h1>Storm <span>Command Centre</span></h1><p>Monitoring USD/JPY, EUR/USD and USD/SGD — three SGX spot markets, one portfolio.</p></div><div className="page-intro-side"><div className="source-badge"><Database size={15}/> {source === 'live' ? (stream.backendSource === 'mock' ? 'SYNTHETIC API STREAM' : 'SGX ENGINE · HISTORICAL REPLAY') : 'SGX HISTORICAL DATA · DEMO'}</div><small>{source === 'live' ? 'FastAPI historical market replay; fictional account' : 'Real quotes · Illustrative risk & model values'}</small></div></div>
        {stream.controlError && <div className="action-confirmation" role="alert"><ShieldAlert size={15}/> Replay control failed: {stream.controlError}</div>}
        <StatCards current={current} source={source}/>
        <div className="dashboard-grid main-row"><MarketChart frames={frames} current={current} source={source} backendSource={stream.backendSource}/><RiskInsights current={current} source={source} activeAlert={stream.activeAlert}/></div>
        <div className="dashboard-grid secondary-row"><Forecast current={current} source={source}/><Portfolio current={current} source={source}/></div>
        <TideTable current={current} source={source}/>
        <Protection current={current} source={source} backendSource={stream.backendSource} onProtect={stream.protect} onClearResult={stream.clearProtectionResult} onResetRace={stream.resetProtectionRace} protectionResult={stream.protectionResult} protectionRace={stream.protectionRace} actionPending={stream.actionPending} firstCriticalAt={firstCriticalAt}/>
        <footer className="page-footer"><span><Bug size={15}/> COCCINELLE</span><span>Simulated portfolios and margin models are for demonstration only. No live orders are placed.</span><span>DURHACK 2026 · SGX</span></footer>
      </div>
    </main>
    <ReplayControls stream={stream}/>
  </div>;
}
