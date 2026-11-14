import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { demoFrame, HISTORICAL_QUOTES, REPLAY_LENGTH } from '../lib/demoMarket.js';
import { isSupportedSpot, MARKET_IDS, normalisePair } from '../lib/markets.js';
import { makeCommand, normaliseFrame, SOCKET_URL } from '../lib/protocol.js';

const clamp = (x, min, max) => Math.max(min, Math.min(max, x));
const emptyLiveFrames = () => Object.fromEntries(MARKET_IDS.map((pair) => [pair, []]));

export function useMarketStream() {
  const [connectionSource, setConnectionSource] = useState('connecting');
  const [selectedPair, setSelectedPair] = useState('USDJPY');
  const [mockIndex, setMockIndex] = useState(12);
  const [paused, setPaused] = useState(true);
  const [speed, setSpeed] = useState(5);
  const [liveFramesByPair, setLiveFramesByPair] = useState(emptyLiveFrames);
  const [protectionResult, setProtectionResult] = useState(null);
  const socketRef = useRef(null);
  const selectedLiveFrames = liveFramesByPair[selectedPair];
  const source = connectionSource === 'live' && selectedLiveFrames.length > 0
    ? 'live'
    : connectionSource === 'connecting' ? 'connecting' : 'demo';

  useEffect(() => {
    let active = true;
    let ws;
    // A socket with no valid spot messages is not a live market feed.
    const fallback = () => {
      if (active) setConnectionSource((previous) => previous === 'live' ? previous : 'demo');
    };
    const timeout = window.setTimeout(fallback, 1800);

    try {
      ws = new WebSocket(SOCKET_URL);
      socketRef.current = ws;
      ws.onmessage = (event) => {
        if (!active) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'protection_result') {
            setProtectionResult(data.payload || data);
            return;
          }
          const frame = normaliseFrame(data);
          if (!frame || !isSupportedSpot(frame)) return;
          const pair = normalisePair(frame.pair);
          window.clearTimeout(timeout);
          setConnectionSource('live');
          setLiveFramesByPair((previous) => ({
            ...previous,
            [pair]: [...previous[pair].slice(-239), frame],
          }));
        } catch (error) {
          console.warn('Unrecognised WebSocket message:', error);
        }
      };
      ws.onerror = fallback;
      ws.onclose = () => {
        if (!active) return;
        socketRef.current = null;
        setConnectionSource('demo');
        setLiveFramesByPair(emptyLiveFrames());
      };
    } catch {
      fallback();
    }
    return () => {
      active = false;
      window.clearTimeout(timeout);
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
  const current = frames.at(-1) || demoFrame(selectedPair, 0);

  const pairSnapshots = useMemo(() => Object.fromEntries(MARKET_IDS.map((pair) => {
    const live = connectionSource === 'live' && liveFramesByPair[pair].length > 0;
    return [pair, {
      frame: live ? liveFramesByPair[pair].at(-1) : demoFrame(pair, mockIndex),
      source: live ? 'live' : 'demo',
    }];
  })), [connectionSource, liveFramesByPair, mockIndex]);

  const send = useCallback((command, payload = {}) => {
    if (socketRef.current?.readyState === WebSocket.OPEN && connectionSource === 'live') {
      socketRef.current.send(makeCommand(command, payload));
      return true;
    }
    return false;
  }, [connectionSource]);

  const playPause = useCallback(() => {
    const nextPaused = !paused;
    setPaused(nextPaused);
    send(nextPaused ? 'pause' : 'play');
  }, [paused, send]);

  const changeSpeed = useCallback((multiplier) => {
    setSpeed(multiplier);
    send('speed', { multiplier });
  }, [send]);

  const jumpTo = useCallback((clock = '13:24') => {
    const timestamp = `2026-07-30T${clock}:00Z`;
    const index = HISTORICAL_QUOTES.USDJPY.findIndex((quote) => quote.timestamp >= timestamp);
    if (index >= 0) setMockIndex(index);
    setPaused(true);
    if (send('jump', { timestamp })) setLiveFramesByPair(emptyLiveFrames());
  }, [send]);

  const reset = useCallback(() => {
    setMockIndex(0);
    setPaused(true);
    setProtectionResult(null);
    if (send('reset')) setLiveFramesByPair(emptyLiveFrames());
  }, [send]);

  const protect = useCallback((action) => {
    setProtectionResult(null);
    return send('protect', { action, timestamp: current.timestamp, pair: selectedPair });
  }, [send, current.timestamp, selectedPair]);

  return {
    source, connectionSource, selectedPair, selectPair: setSelectedPair,
    pairSnapshots, current, frames, paused, speed, mockIndex,
    totalMockFrames: REPLAY_LENGTH, protectionResult,
    playPause, changeSpeed, jumpTo, reset, protect,
    seek: (index) => { setMockIndex(clamp(index, 0, REPLAY_LENGTH - 1)); setPaused(true); },
  };
}
