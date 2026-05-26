const CSRF_HEADER_NAME = "X-Dead-Letter-CSRF";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

let csrfTokenPromise = null;

function methodFor(options = {}) {
  return String(options.method || "GET").toUpperCase();
}

function isMutatingRequest(options = {}) {
  return !SAFE_METHODS.has(methodFor(options));
}

function mergeCsrfHeader(headers, csrfToken) {
  if (typeof Headers !== "undefined") {
    const merged = new Headers(headers || {});
    merged.set(CSRF_HEADER_NAME, csrfToken);
    return merged;
  }
  return { ...(headers || {}), [CSRF_HEADER_NAME]: csrfToken };
}

export function resetCsrfTokenForTests() {
  csrfTokenPromise = null;
}

export async function getCsrfToken() {
  if (!csrfTokenPromise) {
    csrfTokenPromise = fetch("/api/session")
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || typeof payload.csrf_token !== "string" || !payload.csrf_token) {
          throw new Error("Unable to establish local API session.");
        }
        return payload.csrf_token;
      })
      .catch((error) => {
        csrfTokenPromise = null;
        throw error;
      });
  }
  return csrfTokenPromise;
}

export async function apiFetch(url, options = {}) {
  if (!isMutatingRequest(options)) {
    return fetch(url, options);
  }

  const csrfToken = await getCsrfToken();
  return fetch(url, {
    ...options,
    headers: mergeCsrfHeader(options.headers, csrfToken),
  });
}
