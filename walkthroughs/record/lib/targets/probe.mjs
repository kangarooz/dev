/**
 * Reachability probing for the network-backed targets.
 *
 * The naive version — `await fetch(url)` inside a try/catch — reports success against
 * a host that is definitively blocked, because an egress proxy answers the request
 * itself with a 403 explaining the denial. fetch resolves, nothing throws, and the
 * recorder cheerfully films a proxy error page for ten minutes. So a probe has to read
 * the response, not just survive it.
 */

const DEFAULT_TIMEOUT_MS = 8000;

/** Proxy denial bodies are short and say so; real apps do not talk like this. */
const EGRESS_MARKERS = [/not in allowlist/i, /egress/i, /blocked by policy/i, /proxy/i];

/**
 * @returns {Promise<{ok: boolean, status: number|null, reason: string|null, kind: string, body: string}>}
 *   kind: 'ok' | 'egress' | 'auth' | 'http' | 'network'
 */
export async function probe(url, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal, redirect: 'manual' });
    const body = await res.text().catch(() => '');
    const head = body.slice(0, 400);

    if (res.status === 403 && EGRESS_MARKERS.some((re) => re.test(head))) {
      return { ok: false, status: res.status, kind: 'egress', reason: head.trim(), body: head };
    }
    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get('location') || '';
      if (/sign[_-]?in|login|auth|oauth/i.test(location)) {
        return { ok: false, status: res.status, kind: 'auth', reason: `redirects to ${location}`, body: head };
      }
      return { ok: true, status: res.status, kind: 'ok', reason: null, body: head };
    }
    if (res.status === 401 || res.status === 403) {
      return { ok: false, status: res.status, kind: 'auth', reason: `HTTP ${res.status}`, body: head };
    }
    if (res.status >= 400) {
      return { ok: false, status: res.status, kind: 'http', reason: `HTTP ${res.status}`, body: head };
    }
    // A login wall served as a 200 is still a login wall.
    if (/sign[_-]?in|<title>[^<]*log ?in/i.test(head)) {
      return { ok: false, status: res.status, kind: 'auth', reason: 'served a login page', body: head };
    }
    return { ok: true, status: res.status, kind: 'ok', reason: null, body: head };
  } catch (err) {
    const reason = err.name === 'AbortError' ? `timed out after ${timeoutMs}ms` : err.message;
    return { ok: false, status: null, kind: 'network', reason, body: '' };
  } finally {
    clearTimeout(timer);
  }
}

/** Shared tail for preflight errors — the same three things are always what went wrong. */
export function egressNote() {
  return (
    'From a cloud container this always fails: the environment\'s egress policy denies internal hosts,\n' +
    'and the gateway answers with a 403 page rather than refusing the connection.\n' +
    'Record with --target fixture there, and use this target on a machine with the VPN.'
  );
}
