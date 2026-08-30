/** Every request the studio makes, and the token that proves who is asking.
 *
 * One place that adds the CSRF header, turns a failed response into an Error
 * worth showing, sends an expired session back to the login page, and gives up
 * on a request that hangs. Nothing else in the studio talks to fetch directly.
 */
const csrf = document.querySelector('meta[name="csrf-token"]').content;

/** The header a hand-rolled fetch needs, for the few that upload files. */
export function csrfHeaders(extra = {}) {
  return { ...extra, "X-CSRF-Token": csrf };
}

// Detection rewrites mask.png in place, so every mask URL carries a stamp that
// changes when a detection runs -- otherwise the canvas keeps drawing the mask
// the browser already has.
let maskStamp = 0;

/** The stamp to hang on a mask URL, so a re-detected mask is fetched again. */
export function maskVersion() {
  return maskStamp;
}

export async function api(url, options = {}) {
  const headers = { ...(options.headers || {}), "X-CSRF-Token": csrf };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  
  // Add timeout protection (default 35 seconds)
  const timeoutMs = options.timeout || 35000;
  let timeoutId;
  const controller = new AbortController();
  if (!options.signal) {
    timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    options.signal = controller.signal;
  }

  try {
    const response = await fetch(url, { ...options, headers });
    if (timeoutId) clearTimeout(timeoutId);
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = { error: "The server returned an unreadable response." };
    }
    if (response.status === 401) window.location.href = "/admin/login";
    if (!response.ok) throw new Error(payload.error || "Request failed");
    // Detection rewrites mask.png in place, so the canvas has to stop
    // serving the previous one out of the browser cache.
    if (url.includes("/detect")) maskStamp += 1;
    return payload;
  } catch (err) {
    if (timeoutId) clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s. Please try again.`);
    }
    throw err;
  }
}
