import { test } from "node:test";
import assert from "node:assert/strict";

function makeResponse({ ok = true, status = 200, jsonData = {} } = {}) {
  return {
    ok,
    status,
    async json() {
      return jsonData;
    },
  };
}

function createMockLocalStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem(key) { return store[key] ?? null; },
    setItem(key, value) { store[key] = String(value); },
    removeItem(key) { delete store[key]; },
  };
}

async function createSettingsStore(fetchImpl) {
  const { registerSettingsStore } = await import(
    "../../src/dead_letter/frontend/static/stores/settings.js"
  );
  const stores = {};
  const mockAlpine = {
    store(name, def) {
      if (def !== undefined) stores[name] = def;
      return stores[name];
    },
  };

  stores.job = {
    opInfo: [],
    clearOpMessages() {
      this.opInfo = [];
    },
    setOpInfo(message) {
      this.opInfo = [message];
    },
  };
  stores.watch = { active: false, pathOverride: "" };
  globalThis.fetch = fetchImpl || (() => Promise.resolve(makeResponse()));
  registerSettingsStore(mockAlpine);
  return { settings: stores.settings, stores };
}

test("load() seeds defaults when unconfigured", async () => {
  globalThis.localStorage = createMockLocalStorage();

  const { settings } = await createSettingsStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: {
          configured: false,
          inbox_path: null,
          cabinet_path: null,
        },
      })
    )
  );

  await settings.load();

  assert.equal(settings.configured, false);
  assert.deepEqual(settings.form, {
    inbox_path: "~/letters/Inbox",
    cabinet_path: "~/letters/Cabinet",
  });
});

test("save() persists folders and marks configured", async () => {
  const requests = [];
  const { settings } = await createSettingsStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        jsonData: {
          configured: true,
          inbox_path: "/tmp/Inbox",
          cabinet_path: "/tmp/Cabinet",
        },
      })
    );
  });

  settings.form.inbox_path = "/tmp/Inbox";
  settings.form.cabinet_path = "/tmp/Cabinet";

  await settings.save();

  assert.equal(settings.configured, true);
  assert.equal(settings.form.inbox_path, "/tmp/Inbox");
  assert.equal(settings.form.cabinet_path, "/tmp/Cabinet");
  assert.equal(requests[0].url, "/api/settings");
  assert.equal(requests[0].options.method, "PUT");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    inbox_path: "/tmp/Inbox",
    cabinet_path: "/tmp/Cabinet",
  });
});

test("applyResponse() rejects payloads missing required fields", async () => {
  const { settings } = await createSettingsStore();

  settings.applyResponse({
    configured: true,
    inbox_path: "/tmp/Inbox",
    cabinet_path: null,
  });

  assert.equal(settings.configured, false);
  assert.deepEqual(settings.form, {
    inbox_path: "~/letters/Inbox",
    cabinet_path: "~/letters/Cabinet",
  });
});

test("load() sets needsSetup and showSetupModal when unconfigured and not dismissed", async () => {
  // Clear any localStorage mock state
  globalThis.localStorage = createMockLocalStorage();

  const { settings } = await createSettingsStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: { configured: false, inbox_path: null, cabinet_path: null },
      })
    )
  );

  await settings.load();

  assert.equal(settings.needsSetup, true);
  assert.equal(settings.showSetupModal, true);
});

test("load() sets needsSetup but not showSetupModal when dismissed in localStorage", async () => {
  globalThis.localStorage = createMockLocalStorage({
    "dead-letter:setup-dismissed": "1",
  });

  const { settings } = await createSettingsStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: { configured: false, inbox_path: null, cabinet_path: null },
      })
    )
  );

  await settings.load();

  assert.equal(settings.needsSetup, true);
  assert.equal(settings.showSetupModal, false);
});

test("load() clears needsSetup when configured", async () => {
  globalThis.localStorage = createMockLocalStorage();

  const { settings } = await createSettingsStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: {
          configured: true,
          inbox_path: "/home/user/letters/Inbox",
          cabinet_path: "/home/user/letters/Cabinet",
        },
      })
    )
  );

  await settings.load();

  assert.equal(settings.needsSetup, false);
  assert.equal(settings.showSetupModal, false);
});
