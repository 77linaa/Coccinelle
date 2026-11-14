import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { demoFrame, HISTORICAL_QUOTES, REPLAY_LENGTH } from '../lib/demoMarket.js';
import { isSupportedSpot, MARKET_IDS, normalisePair } from '../lib/markets.js';
import { getBackend, normaliseForecast, normaliseFrame, normalisePortfolio, postBackend, SOCKET_URL } from '../lib/protocol.js';
import { explanationMatchesAlert, forecastMatchesTimeline } from '../lib/timeline.js';

const clamp = (x, min, max) => Math.max(min, Math.min(max, x));
const emptyLiveFrames = () => Object.fromEntries(MARKET_IDS.map((pair) => [pair, []]));

export function useMarketStream() {
  const [connectionSource, setConnectionSource] = useState('connecting');
  const [selectedPair, setSelectedPair] = useState('USDJPY');
  const [mockIndex, setMockIndex] = useState(12);
  const [paused, setPaused] = useState(true);
  const [speed, setSpeed] = useState(5);
  const [liveFramesByPair, setLiveFramesByPair] = useState(emptyLiveFrames);
  const [backendSource, setBackendSource] = useState(null);
  const [livePortfolio, setLivePortfolio] = useState(null);
  const [liveForecast, setLiveForecast] = useState(null);
  const [alertsByPair, setAlertsByPair] = useState({});
  const [controlError, setControlError] = useState(null);
  const [actionPending, setActionPending] = useState(false);
  const [protectionResult, setProtectionResult] = useState(null);
  const [protectionRace, setProtectionRace] = useState(null);
  const requestIdRef = useRef(0);
  const timelineEpochRef = useRef(0);
  const replayStartRef = useRef(null);
  const lastTickRef = useRef(null);
  const latencyRef = useRef(Object.fromEntries(MARKET_IDS.map((pair) => [pair, []])));
  const selectedLiveFrames = liveFramesByPair[selectedPair];
  const source = connectionSource === 'live' && selectedLiveFrames.length > 0
    ? 'live'
    : connectionSource === 'connecting' ? 'connecting' : 'demo';

  useEffect(() => {
    let active = true;
    let ws;
    let retry;
    let fallback;
    const connect = () => {
      if (!active) return;
      fallback = window.setTimeout(() => setConnectionSource((old) => old === 'live' ? old : 'demo'), 1800);
      try { ws = new WebSocket(SOCKET_URL); } catch { scheduleReconnect(); return; }
      ws.onmessage = (event) => {
        if (!active) return;
        let data;
        try { data = JSON.parse(event.data); } catch { return; }
        if (data.source) setBackendSource(data.source);
        if (data.type === 'hello' || data.type === 'replay') {
          if (data.replay) {
            const startChanged = replayStartRef.current && replayStartRef.current !== data.replay.start;
            const restarted = lastTickRef.current && data.replay.position == null && data.replay.status === 'playing';
            if (startChanged || restarted) {
              timelineEpochRef.current += 1;
              lastTickRef.current = null;
              latencyRef.current = Object.fromEntries(MARKET_IDS.map((pair) => [pair, []]));
              setLiveFramesByPair(emptyLiveFrames());
              setLiveForecast(null);
              setProtectionRace(null);
              setAlertsByPair({});
            }
            replayStartRef.current = data.replay.start;
            setPaused(data.replay.status !== 'playing');
            if (data.replay.speed) setSpeed(Math.max(1, Math.round(data.replay.speed / 60)));
          }
          if (data.type === 'hello') {
            lastTickRef.current = data.replay?.position || null;
            if (data.portfolio) setLivePortfolio(normalisePortfolio(data.portfolio));
            if (data.forecast?.forecast && forecastMatchesTimeline(
              data.forecast.as_of, replayStartRef.current, lastTickRef.current,
            )) setLiveForecast(normaliseForecast(data.forecast));
            const epoch = timelineEpochRef.current;
            getBackend('/protect').then((race) => {
              if (active && epoch === timelineEpochRef.current) setProtectionRace(race);
            }).catch(() => { /* WebSocket ticks also carry race state. */ });
          }
          return;
        }
        if (data.type === 'protection') {
          setProtectionRace(data.protection || null);
          return;
        }
        if (data.type === 'forecast') {
          if (forecastMatchesTimeline(data.as_of, replayStartRef.current, lastTickRef.current)) {
            setLiveForecast(data.forecast ? normaliseForecast(data) : null);
          }
          return;
        }
        if (data.type === 'alert') {
          const pair = normalisePair(data.alert?.pair);
          if (MARKET_IDS.includes(pair)) {
            setAlertsByPair((old) => ({ ...old, [pair]: { ...data.alert, explanation: null } }));
          }
          return;
        }
        if (data.type === 'explanation') {
          const pair = normalisePair(data.alert?.pair);
          if (MARKET_IDS.includes(pair)) {
            setAlertsByPair((old) => explanationMatchesAlert(old[pair], data.alert)
              ? { ...old, [pair]: { ...old[pair], explanation: data.explanation } }
              : old);
          }
          return;
        }
        if (data.type !== 'tick') return;
        const frame = normaliseFrame(data);
        if (!frame || !isSupportedSpot(frame)) return;
        const pair = normalisePair(frame.pair);
        lastTickRef.current = frame.timestamp;
        window.clearTimeout(fallback);
        setConnectionSource('live');
        if (frame.portfolio) setLivePortfolio(frame.portfolio);
        if (data.protection) setProtectionRace(data.protection);
        const samples = [...latencyRef.current[pair].slice(-199), frame.latency.latestMs]
          .filter((value) => Number.isFinite(value));
        latencyRef.current[pair] = samples;
        const sorted = [...samples].sort((a, b) => a - b);
        const quantile = (q) => sorted.length ? sorted[Math.ceil(q * sorted.length) - 1] : null;
        frame.latency.medianMs = quantile(0.5);
        frame.latency.p99Ms = quantile(0.99);
        setLiveFramesByPair((previous) => ({
          ...previous, [pair]: [...previous[pair].slice(-239), frame],
        }));
      };
      ws.onerror = () => { /* onclose performs fallback and reconnect */ };
      ws.onclose = () => scheduleReconnect();
    };
    const scheduleReconnect = () => {
      if (!active) return;
      window.clearTimeout(fallback);
      setConnectionSource('demo');
      setBackendSource(null);
      setLiveFramesByPair(emptyLiveFrames());
      setLivePortfolio(null);
      setLiveForecast(null);
      setProtectionRace(null);
      setAlertsByPair({});
      requestIdRef.current += 1;
      setProtectionResult(null);
      setActionPending(false);
      timelineEpochRef.current += 1;
      replayStartRef.current = null;
      lastTickRef.current = null;
      retry = window.setTimeout(connect, 2500);
    };
    connect();
    return () => {
      active = false;
      window.clearTimeout(retry);
      window.clearTimeout(fallback);
      if (ws) ws.close();
    };
  }, []);

  useEffect(() => {
    if (source !== 'demo' || paused || mockIndex >= REPLAY_LENGTH - 1) return;
    const timer = window.setInterval(() => {
      setMockIndex((index) => Math.min(REPLAY_LENGTH - 1, index + 1));
    }, Math.max(65, 1000 / speed));
    return () => window.clearInterval(timer);
  }, [source, paused, speed, mockIndex]);

  useEffect(() => {
    if (source === 'demo' && mockIndex >= REPLAY_LENGTH - 1) setPaused(true);
  }, [source, mockIndex]);
  const frames = useMemo(() => source === 'live'
    ? selectedLiveFrames
    : HISTORICAL_QUOTES[selectedPair].slice(0, mockIndex + 1).map((_, index) => demoFrame(selectedPair, index)),
  [source, selectedLiveFrames, selectedPair, mockIndex]);
  const lastFrame = frames.at(-1) || demoFrame(selectedPair, 0);
  const current = source === 'live' ? {
    ...lastFrame,
    portfolio: livePortfolio || lastFrame.portfolio,
    forecast: liveForecast || { breachProbability: null, horizonHours: 24, cone: [] },
  } : lastFrame;

  const pairSnapshots = useMemo(() => Object.fromEntries(MARKET_IDS.map((pair) => {
    const live = connectionSource === 'live' && liveFramesByPair[pair].length > 0;
    return [pair, {
      frame: live ? liveFramesByPair[pair].at(-1) : demoFrame(pair, mockIndex),
      source: live ? 'live' : 'demo',
    }];
  })), [connectionSource, liveFramesByPair, mockIndex]);

  const hasBackendPair = useCallback((pair) => Boolean(backendSource)
    && (pair === 'USDJPY' || liveFramesByPair[pair]?.length > 0), [backendSource, liveFramesByPair]);

  const control = useCallback(async (path) => {
    setControlError(null);
    try { return await postBackend(path); } catch (error) {
      setControlError(error.message);
      return null;
    }
  }, []);

  const clearProtectionResult = useCallback(() => {
    requestIdRef.current += 1;
    setProtectionResult(null);
    setActionPending(false);
  }, []);

  const selectPair = useCallback((pair) => {
    clearProtectionResult();
    setSelectedPair(pair);
  }, [clearProtectionResult]);

  const playPause = useCallback(() => {
    const nextPaused = !paused;
    setPaused(nextPaused);
    if (hasBackendPair(selectedPair)) control(nextPaused ? '/replay/pause' : '/replay/start');
  }, [paused, control, hasBackendPair, selectedPair]);

  const changeSpeed = useCallback((multiplier) => {
    setSpeed(multiplier);
    // 1 UI unit = 1 replayed market minute per wall-clock second.
    if (hasBackendPair(selectedPair)) control(`/replay/speed?value=${multiplier * 60}`);
  }, [control, hasBackendPair, selectedPair]);

  const jumpTo = useCallback(async (clock = '13:24', targetPair = selectedPair) => {
    clearProtectionResult();
    setProtectionRace(null);
    const timestamp = `2026-07-30T${clock}:00Z`;
    const index = HISTORICAL_QUOTES.USDJPY.findIndex((quote) => quote.timestamp >= timestamp);
    if (index >= 0) setMockIndex(index);
    setPaused(true);
    if (hasBackendPair(targetPair)) {
      timelineEpochRef.current += 1;
      lastTickRef.current = null;
      latencyRef.current = Object.fromEntries(MARKET_IDS.map((pair) => [pair, []]));
      setLiveFramesByPair(emptyLiveFrames());
      setLiveForecast(null);
      setAlertsByPair({});
      // A paused backend emits no initial tick after jump, so resume from the
      // selected moment to populate the screen with genuine engine data.
      if (await control('/replay/pause')) {
        const jumped = await control(`/replay/jump?date=${encodeURIComponent(timestamp)}`);
        if (jumped) await control('/replay/start');
      }
    }
  }, [clearProtectionResult, control, hasBackendPair, selectedPair]);

  const reset = useCallback(async () => {
    setMockIndex(0);
    setPaused(true);
    clearProtectionResult();
    setProtectionRace(null);
    if (hasBackendPair(selectedPair)) {
      timelineEpochRef.current += 1;
      lastTickRef.current = null;
      latencyRef.current = Object.fromEntries(MARKET_IDS.map((pair) => [pair, []]));
      setLiveFramesByPair(emptyLiveFrames());
      setLiveForecast(null);
      setAlertsByPair({});
      if (await control('/replay/pause')) {
        const jumped = await control('/replay/jump?date=2026-07-30T13%3A00');
        if (jumped) await control('/replay/start');
      }
    }
  }, [clearProtectionResult, control, hasBackendPair, selectedPair]);

  const protect = useCallback(async (action, judgeMode = false) => {
    const requestId = ++requestIdRef.current;
    setProtectionResult(null);
    if (action === 'none') {
      setProtectionResult({ held: true });
      return;
    }
    if (!hasBackendPair(selectedPair) || source !== 'live') {
      setProtectionResult({ demo: true });
      return;
    }
    const body = action === 'reduce_50'
      ? { kind: 'reduce', percent: 50, pair: selectedPair }
      : { kind: 'close', percent: 100, pair: selectedPair };
    setActionPending(true);
    try {
      const result = await postBackend(judgeMode ? '/protect' : '/simulate', body);
      if (requestId === requestIdRef.current) {
        setProtectionResult({ ...result, judgeMode });
        if (judgeMode && result.protection) setProtectionRace(result.protection);
      }
    } catch (error) {
      if (requestId === requestIdRef.current) setProtectionResult({ error: error.message, judgeMode });
    } finally {
      if (requestId === requestIdRef.current) setActionPending(false);
    }
  }, [hasBackendPair, selectedPair, source]);

  const resetProtectionRace = useCallback(async () => {
    clearProtectionResult();
    if (!backendSource) return;
    try {
      const race = await postBackend('/protect/reset');
      setProtectionRace(race);
    } catch (error) {
      setControlError(error.message);
    }
  }, [backendSource, clearProtectionResult]);

  return {
    source, connectionSource, backendSource, selectedPair, selectPair,
    pairSnapshots, current, frames, paused, speed, mockIndex,
    totalMockFrames: REPLAY_LENGTH, protectionResult, protectionRace, actionPending, controlError,
    activeAlert: alertsByPair[selectedPair] || null,
    playPause, changeSpeed, jumpTo, reset, protect, resetProtectionRace, clearProtectionResult,
    seek: (index) => { setMockIndex(clamp(index, 0, REPLAY_LENGTH - 1)); setPaused(true); },
  };
}
