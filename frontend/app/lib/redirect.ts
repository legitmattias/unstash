/**
 * Reduce a post-login redirect target to a same-origin path.
 *
 * The target arrives in a query parameter, so it is attacker-controlled.
 * Only an absolute path on this origin is accepted: it must start with a
 * single "/" and contain no backslash or control character, which browsers
 * normalise into scheme-relative URLs ("//host", "/\\host").
 */
export function safeRedirectPath(target: string | undefined, fallback = "/"): string {
  if (!target || !target.startsWith("/") || target.startsWith("//")) {
    return fallback;
  }
  for (const char of target) {
    if (char === "\\" || char.charCodeAt(0) < 0x20) {
      return fallback;
    }
  }
  return target;
}
