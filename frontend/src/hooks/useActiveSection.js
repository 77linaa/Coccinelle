import { useCallback, useEffect, useRef, useState } from 'react';

const SECTION_IDS = ['market', 'portfolio', 'protection'];

// Select the last section whose heading has reached the reading area.
// Overview is the default while the user is at the top of the dashboard.
export function sectionAtViewport(getTop, viewportHeight, atBottom = false) {
  let active = 'overview';
  const threshold = Math.min(220, viewportHeight * 0.32);
  for (const id of SECTION_IDS) {
    const top = getTop(id);
    if (top != null && top <= threshold) active = id;
  }
  if (atBottom) active = 'protection';
  return active;
}

export function useActiveSection() {
  const [activeSection, setActiveSection] = useState('overview');
  const navigateLockUntil = useRef(0);
  const pendingTimer = useRef(null);

  const syncActiveSection = useCallback(() => {
    if (typeof window === 'undefined') return;
    const documentHeight = document.documentElement.scrollHeight;
    const atBottom = window.scrollY + window.innerHeight >= documentHeight - 80;
    const next = sectionAtViewport(
      (id) => document.getElementById(id)?.getBoundingClientRect().top,
      window.innerHeight,
      atBottom,
    );
    setActiveSection(next);
  }, []);

  useEffect(() => {
    let frame = 0;
    const scheduleSync = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (performance.now() >= navigateLockUntil.current) syncActiveSection();
      });
    };
    window.addEventListener('scroll', scheduleSync, { passive: true });
    window.addEventListener('resize', scheduleSync);
    scheduleSync();
    return () => {
      window.removeEventListener('scroll', scheduleSync);
      window.removeEventListener('resize', scheduleSync);
      window.cancelAnimationFrame(frame);
      if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
    };
  }, [syncActiveSection]);

  const navigateTo = useCallback((id) => {
    const target = document.getElementById(id);
    if (!target) return;
    setActiveSection(id); // Immediate click feedback, before smooth scrolling settles.
    navigateLockUntil.current = performance.now() + 750;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
    pendingTimer.current = window.setTimeout(syncActiveSection, 800);
  }, [syncActiveSection]);

  return { activeSection, navigateTo };
}
