import { test } from "node:test";
import assert from "node:assert/strict";

function makeResponse({ ok = true, status = 200, jsonData = {} } = {}) {
  const response = {
    ok,
    status,
    async json() {
      return jsonData;
    },
  };
  response.clone = () => makeResponse({ ok, status, jsonData });
  return response;
}

function headerValue(headers, name) {
  if (headers instanceof Headers) {
    return headers.get(name) ?? undefined;
  }
  return headers?.[name] ?? headers?.[name.toLowerCase()];
}

async function loadApi() {
  const api = await import("../../src/dead_letter/frontend/static/lib/api.js");
  api.resetCsrfTokenForTests();
  return api;
}

test("safe requests do not fetch a CSRF session", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(makeResponse());
  };

  await apiFetch("/api/settings");

  assert.deepEqual(requests.map((request) => request.url), ["/api/settings"]);
});

test("JSON mutations fetch a session token and preserve Content-Type", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-123" } }));
    }
    return Promise.resolve(makeResponse());
  };

  await apiFetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inbox_path: "/tmp/Inbox", cabinet_path: "/tmp/Cabinet" }),
  });

  assert.deepEqual(requests.map((request) => request.url), ["/api/session", "/api/settings"]);
  assert.equal(headerValue(requests[1].options.headers, "Content-Type"), "application/json");
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-123");
});

test("multipart mutations add CSRF without setting Content-Type", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  const formData = {};
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-123" } }));
    }
    return Promise.resolve(makeResponse());
  };

  await apiFetch("/api/import", { method: "POST", body: formData });

  assert.equal(requests[1].url, "/api/import");
  assert.equal(requests[1].options.body, formData);
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-123");
  assert.equal(headerValue(requests[1].options.headers, "Content-Type"), undefined);
});

test("session token is cached across multiple mutations", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-123" } }));
    }
    return Promise.resolve(makeResponse());
  };

  await apiFetch("/api/jobs", { method: "POST", body: "{}" });
  await apiFetch("/api/watch", { method: "DELETE" });

  assert.deepEqual(requests.map((request) => request.url), [
    "/api/session",
    "/api/jobs",
    "/api/watch",
  ]);
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-123");
  assert.equal(headerValue(requests[2].options.headers, "X-Dead-Letter-CSRF"), "csrf-123");
});

test("refreshes token and retries once after csrf_validation_failed", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  const tokens = ["stale-token", "fresh-token"];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: tokens.shift() } }));
    }
    const sent = headerValue(options.headers, "X-Dead-Letter-CSRF");
    if (sent === "fresh-token") {
      return Promise.resolve(makeResponse({ status: 200 }));
    }
    return Promise.resolve(
      makeResponse({
        ok: false,
        status: 403,
        jsonData: { errors: [{ code: "csrf_validation_failed", message: "stale" }] },
      }),
    );
  };

  const response = await apiFetch("/api/jobs", { method: "POST", body: "{}" });

  assert.equal(response.status, 200);
  assert.deepEqual(requests.map((request) => request.url), [
    "/api/session",
    "/api/jobs",
    "/api/session",
    "/api/jobs",
  ]);
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "stale-token");
  assert.equal(headerValue(requests[3].options.headers, "X-Dead-Letter-CSRF"), "fresh-token");
});

test("does not retry on non-CSRF 403 responses", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-123" } }));
    }
    return Promise.resolve(
      makeResponse({
        ok: false,
        status: 403,
        jsonData: { errors: [{ code: "invalid_request", message: "bad body" }] },
      }),
    );
  };

  const response = await apiFetch("/api/jobs", { method: "POST", body: "{}" });

  assert.equal(response.status, 403);
  assert.deepEqual(requests.map((request) => request.url), ["/api/session", "/api/jobs"]);
});

test("stops after one retry if refreshed token is identical", async () => {
  const { apiFetch } = await loadApi();
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-same" } }));
    }
    return Promise.resolve(
      makeResponse({
        ok: false,
        status: 403,
        jsonData: { errors: [{ code: "csrf_validation_failed", message: "nope" }] },
      }),
    );
  };

  const response = await apiFetch("/api/jobs", { method: "POST", body: "{}" });

  assert.equal(response.status, 403);
  assert.deepEqual(requests.map((request) => request.url), [
    "/api/session",
    "/api/jobs",
    "/api/session",
  ]);
});
