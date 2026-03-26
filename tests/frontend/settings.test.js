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
    inbox_path: "~/Documents/dead-letter/Inbox",
    cabinet_path: "~/Documents/dead-letter/Cabinet",
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
    inbox_path: "~/Documents/dead-letter/Inbox",
    cabinet_path: "~/Documents/dead-letter/Cabinet",
  });
});
