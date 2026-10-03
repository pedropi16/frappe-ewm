// Shared mechanics for the two multi-step confirmation wizards (task.js's generic task
// confirmation, move.js's ad-hoc Internal Move) - the parts that were byte-for-byte identical
// logic wearing different variable names, not the per-screen field rendering, which stays
// genuinely different between the two and is left alone.

// Guards against landing on a step whose earlier steps are not done (deep link, stale history
// entry, reload): redirects to the first incomplete step, or `steps`'s last entry (always
// "review" today) once everything earlier is done. Returns a {redirect} for the screen's own
// enter() to return directly, or null when `step` is already fine to render as-is.
export function guardStep(steps, step, done, toHref) {
  const last = steps[steps.length - 1];
  const firstOpen = steps.find((s) => s !== last && !done(s)) || last;
  const wanted = steps.includes(step) ? step : firstOpen;
  if (steps.indexOf(wanted) > steps.indexOf(firstOpen)) return { redirect: toHref(firstOpen) };
  if (wanted !== step) return { redirect: toHref(wanted) };
  return null;
}
