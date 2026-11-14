/** FastAPI replay.start is UTC but omits its zone suffix. */
function asUtcMillis(value) {
  if (!value) return null;
  const raw = String(value);
  const iso = /(?:Z|[+-]\d{2}:\d{2})$/i.test(raw) ? raw : `${raw}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Reject forecasts from a different replay timeline or from the future.
 * An unpositioned (not yet ticked) replay cannot display a model output.
 */
export function forecastMatchesTimeline(asOf, start, position) {
  const forecast = asUtcMillis(asOf);
  const earliest = asUtcMillis(start);
  const latest = asUtcMillis(position);
  return forecast != null && latest != null
    && (earliest == null || forecast >= earliest)
    && forecast <= latest;
}

/** Late LLM explanations belong only to the alert they were generated for. */
export function explanationMatchesAlert(latest, incoming) {
  return Boolean(latest && incoming && latest.ts === incoming.ts
    && latest.pair === incoming.pair && latest.level === incoming.level);
}
