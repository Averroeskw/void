/* Shared, side-effect-free idle policy for the desktop companion. */
(function (root) {
  function shouldReturn(s) {
    return !s.hidden && !s.blocked && !s.hovered && s.now - s.lastUse >= s.timeout;
  }
  const api = { shouldReturn };
  if (typeof module !== 'undefined') module.exports = api;
  else root.VoidIdleReturn = api;
})(typeof window !== 'undefined' ? window : globalThis);
