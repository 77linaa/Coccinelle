import { useCallback, useEffect, useRef, useState } from 'react';

const RANK = { LOW: 0, ELEVATED: 1, HIGH: 2, CRITICAL: 3 };

function noiseBuffer(context) {
  const buffer = context.createBuffer(1, Math.round(context.sampleRate * 2.5), context.sampleRate);
  const samples = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) samples[i] = Math.random() * 2 - 1;
  return buffer;
}

// Layered market-storm cue: sweeping wind, a low rumble, and a restrained alert tone.
// Each layer has its own envelope; overall output remains below full-scale volume.
function playStormCue(context, buffer, level = 'HIGH', preview = false) {
  const critical = level === 'CRITICAL' || preview;
  const duration = critical ? 1.85 : 1.35;
  const start = context.currentTime + 0.015;
  const master = context.createGain();
  master.gain.value = 0.62;
  master.connect(context.destination);

  const wind = context.createBufferSource();
  wind.buffer = buffer;
  const windFilter = context.createBiquadFilter();
  windFilter.type = 'bandpass';
  windFilter.Q.value = 0.6;
  windFilter.frequency.setValueAtTime(180, start);
  windFilter.frequency.exponentialRampToValueAtTime(1350, start + duration * 0.52);
  windFilter.frequency.exponentialRampToValueAtTime(270, start + duration);
  const windGain = context.createGain();
  windGain.gain.setValueAtTime(0.0001, start);
  windGain.gain.linearRampToValueAtTime(critical ? 0.24 : 0.18, start + duration * 0.37);
  windGain.gain.linearRampToValueAtTime(0.0001, start + duration);
  wind.connect(windFilter).connect(windGain).connect(master);
  wind.start(start);
  wind.stop(start + duration);

  const air = context.createBufferSource();
  air.buffer = buffer;
  const airFilter = context.createBiquadFilter();
  airFilter.type = 'highpass';
  airFilter.frequency.value = 680;
  const airGain = context.createGain();
  airGain.gain.setValueAtTime(0.0001, start);
  airGain.gain.linearRampToValueAtTime(critical ? 0.075 : 0.045, start + duration * 0.54);
  airGain.gain.linearRampToValueAtTime(0.0001, start + duration);
  air.connect(airFilter).connect(airGain).connect(master);
  air.start(start);
  air.stop(start + duration);

  const rumble = context.createOscillator();
  rumble.type = 'triangle';
  rumble.frequency.setValueAtTime(82, start);
  rumble.frequency.exponentialRampToValueAtTime(43, start + duration);
  const rumbleGain = context.createGain();
  rumbleGain.gain.setValueAtTime(0.0001, start);
  rumbleGain.gain.linearRampToValueAtTime(critical ? 0.105 : 0.07, start + 0.22);
  rumbleGain.gain.linearRampToValueAtTime(0.0001, start + duration);
  rumble.connect(rumbleGain).connect(master);
  rumble.start(start);
  rumble.stop(start + duration);

  if (critical) {
    const alert = context.createOscillator();
    alert.type = 'sine';
    alert.frequency.setValueAtTime(670, start + 0.36);
    alert.frequency.exponentialRampToValueAtTime(430, start + 0.94);
    const alertGain = context.createGain();
    alertGain.gain.setValueAtTime(0.0001, start + 0.36);
    alertGain.gain.linearRampToValueAtTime(0.065, start + 0.44);
    alertGain.gain.exponentialRampToValueAtTime(0.0001, start + 1.04);
    alert.connect(alertGain).connect(master);
    alert.start(start + 0.36);
    alert.stop(start + 1.04);
  }
  wind.onended = () => { master.disconnect(); };
}

export function useStormSound(level) {
  const [enabled, setEnabled] = useState(false);
  const previousLevel = useRef(level);
  const contextRef = useRef(null);
  const bufferRef = useRef(null);

  const triggerCue = useCallback(async (cueLevel, preview = false) => {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    try {
      if (!contextRef.current || contextRef.current.state === 'closed') {
        contextRef.current = new AudioContextClass();
        bufferRef.current = noiseBuffer(contextRef.current);
      }
      const context = contextRef.current;
      if (context.state === 'suspended') await context.resume();
      playStormCue(context, bufferRef.current, cueLevel, preview);
    } catch (error) {
      console.warn('Optional storm audio unavailable:', error);
    }
  }, []);

  useEffect(() => {
    const previous = previousLevel.current;
    previousLevel.current = level;
    if (enabled && (RANK[level] ?? 0) >= 2 && (RANK[level] ?? 0) > (RANK[previous] ?? 0)) {
      void triggerCue(level);
    }
  }, [enabled, level, triggerCue]);

  const toggleSound = useCallback(() => {
    if (!enabled) void triggerCue('HIGH', true);
    setEnabled(!enabled);
  }, [enabled, triggerCue]);

  const previewSound = useCallback(() => {
    if (enabled) void triggerCue('CRITICAL', true);
  }, [enabled, triggerCue]);

  useEffect(() => () => {
    const context = contextRef.current;
    if (context && context.state !== 'closed') void context.close().catch(() => {});
  }, []);

  return { enabled, toggleSound, previewSound };
}
