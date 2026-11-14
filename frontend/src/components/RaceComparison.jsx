import { Bug, Clock3, Crosshair, RotateCcw, ShieldAlert, Sparkles, TrendingDown } from 'lucide-react';

const money = (value) => value == null || !Number.isFinite(Number(value))
  ? '—'
  : `${value < 0 ? '−' : ''}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

const signedMoney = (value) => value == null || !Number.isFinite(Number(value))
  ? '—'
  : `${value < 0 ? '−' : '+'}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

const utcTime = (timestamp) => timestamp
  ? `${new Date(timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC`
  : '—';

function RaceParticipant({ title, icon: Icon, variant, result, waiting }) {
  const breached = result?.survived === false;
  return <article className={`race-participant race-${variant}`}>
    <div className="race-participant-heading"><Icon size={17}/><span>{title}</span></div>
    <div className={`race-outcome ${result ? (breached ? 'race-breach' : 'race-survived') : 'race-waiting'}`}>
      {result ? (breached ? 'MARGIN BREACH' : 'NO BREACH OBSERVED') : waiting}
    </div>
    <div className="race-buffer-label">LOWEST OBSERVED MARGIN BUFFER</div>
    <div className={`race-buffer${result?.min_buffer < 0 ? ' negative' : ''}`}>{money(result?.min_buffer)}</div>
    <div className="race-separator" />
    <div className="race-metric"><span>Current equity</span><strong>{money(result?.portfolio?.equity)}</strong></div>
    <div className="race-metric"><span>{variant === 'unprotected' ? 'Protection time' : 'Action time'}</span><strong>{variant === 'unprotected' ? 'None' : utcTime(result?.acted_at)}</strong></div>
    {variant !== 'unprotected' && <div className="race-metric"><span>vs. unprotected equity</span><strong className={result?.vs_unprotected < 0 ? 'negative' : 'positive'}>{signedMoney(result?.vs_unprotected)}</strong></div>}
  </article>;
}

function leadMessage(minutes) {
  if (minutes == null || !Number.isFinite(Number(minutes))) return null;
  if (minutes > 0) return `Coccinelle's simulated action came ${Number(minutes).toFixed(1)} market minutes before the judge's.`;
  if (minutes < 0) return `The judge acted ${Math.abs(Number(minutes)).toFixed(1)} market minutes before the red-signal strategy.`;
  return 'The judge and red-signal strategy acted at the same market timestamp.';
}

export default function RaceComparison({ race, backendSource, onReset, resetting = false }) {
  if (!race?.unprotected) return <div className="race-empty"><Clock3 size={16}/> Waiting for the first backend risk snapshot.</div>;

  const lead = leadMessage(race.sentinel_lead_minutes);
  const sentinelWaiting = race.sentinel_blocked
    ? 'RISK OFFICER VETO'
    : !race.red_at ? 'AWAITING RED SIGNAL'
      : !race.action ? 'AWAITING JUDGE ACTION'
        : 'AWAITING COMPARISON';

  return <div className="race-comparison" data-testid="race-comparison">
    <div className="race-header">
      <div><div className="race-eyebrow"><Sparkles size={13}/> MARKET STORM · OUTCOME COMPARISON</div><h3>Who survives the storm?</h3><p>Three fictional accounts, marked against the same SGX historical price replay.</p></div>
      <div className="race-actions"><span className="race-source">{backendSource === 'mock' ? 'SYNTHETIC API FEED' : 'SGX ENGINE REPLAY'}</span>{race.judge && <button type="button" className="race-reset" onClick={onReset} disabled={resetting}><RotateCcw size={14}/> Reset judge action</button>}</div>
    </div>

    <div className="race-grid">
      <RaceParticipant title="UNPROTECTED" icon={TrendingDown} variant="unprotected" result={race.unprotected}/>
      <RaceParticipant title="THE JUDGE" icon={Crosshair} variant="judge" result={race.judge} waiting="NO ACTION YET"/>
      <RaceParticipant title="COCCINELLE" icon={Bug} variant="sentinel" result={race.sentinel} waiting={sentinelWaiting}/>
    </div>

    {lead && <div className="race-lead"><Clock3 size={17}/><span>{lead}</span></div>}
    {race.sentinel_blocked && <div className="race-warning" role="status"><ShieldAlert size={16}/> The Risk Officer blocked the simulated red-signal action: {(race.sentinel_blocked.reasons || []).join('; ') || 'Action was not approved.'}</div>}
    {!race.red_at && <div className="race-muted"><Clock3 size={15}/> The backend has not observed a red USD/JPY signal on this replay timeline yet.</div>}
    {race.red_at && <div className="race-muted"><ShieldAlert size={15}/> First red signal: {utcTime(race.red_at)}.</div>}
    {!race.replay_exact && <div className="race-warning"><ShieldAlert size={16}/> Historical event log was truncated; this comparison is incomplete.</div>}
    <p className="race-disclaimer">Paper backtest only. Coccinelle's branch retrospectively applies the same judge-selected action at the first red signal; it is not a real or autonomous order. Low-water marks cover each branch's observed history.</p>
  </div>;
}
