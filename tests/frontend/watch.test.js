import { test } from "node:test";
import assert from "node:assert/strict";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeResponse({ ok = true, status = 200, jsonData = {} } = {}) {
  return {
    ok,
    status,
    async json() {
      return jsonData;
    },
  };
}

async function createWatchStore(fetchImpl) {
  const { registerWatchStore } = await import("../../src/dead_letter/frontend/static/stores/watch.js");
  const stores = {};
  const intervals = new Map();
  let nextIntervalId = 1;
  globalThis.window = {
    setInterval(callback, delay) {
      const id = nextIntervalId++;
      intervals.set(id, { callback, delay });
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
    },
  };

  const mockAlpine = {
    store(name, def) {
      if (def !== undefined) stores[name] = def;
      return stores[name];
    },
  };
  stores.job = {
    id: "",
    origin: "",
    status: "",
    pendingWatchJobId: "",
    pendingWatchJobStatus: "",
    outputLocation: null,
    diagnostics: null,
    recoveryActions: [],
    diagnosticsOpen: false,
    cancelRequested: false,
    progress: { total: 0, completed: 0, failed: 0, current: null },
    summary: { written: 0, skipped: 0, errors: 0 },
    errors: [],
    timestamps: { created_at: null, started_at: null, finished_at: null },
    activeCancelController: null,
    cancelInFlight: false,
    opErrors: [],
    opInfo: [],
    stopPolling() {},
    startPolling() {},
    resetState() {},
    clearOpMessages() {},
    setOpError(message) {
      this.opErrors = [message];
    },
    setOpInfo(message) {
      this.opInfo = [message];
    },
    isSubmitting: false,
  };
  globalThis.fetch = fetchImpl || (() => Promise.resolve(makeResponse()));
  registerWatchStore(mockAlpine);
  return { watch: stores.watch, stores, intervals };
}

test("stop() ignores stale poll responses", async () => {
  const poll = deferred();
  const stop = deferred();
  let callCount = 0;

  const { watch } = await createWatchStore(() => {
    callCount += 1;
    if (callCount === 1) {
      return poll.promise;
    }
    if (callCount === 2) {
      return stop.promise;
    }
    throw new Error(`unexpected fetch call ${callCount}`);
  });

  watch.active = true;
  watch.pollHandle = 42;

  const pollPromise = watch.poll();

  stop.resolve(
    makeResponse({
      jsonData: {
        active: false,
        path: null,
        files_detected: 0,
        jobs_created: 0,
        failed_events: 0,
        last_error: null,
      },
    })
  );
  await watch.stop();

  poll.resolve(
    makeResponse({
      jsonData: {
        active: true,
        path: "mail",
        files_detected: 1,
        jobs_created: 1,
        failed_events: 0,
        last_error: null,
      },
    })
  );
  await pollPromise;

  assert.equal(watch.active, false);
  assert.equal(watch.pollHandle, null);
  assert.equal(watch.path, "");
});

test("stop() preserves active watch state when backend stop fails", async () => {
  const { watch, stores } = await createWatchStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 500,
        jsonData: {
          errors: [{ message: "watch stop failed" }],
        },
      })
    )
  );

  watch.active = true;
  watch.path = "/tmp/Inbox";
  watch.stats = { files_detected: 3, jobs_created: 2, failed_events: 1 };
  watch.startPolling();

  await watch.stop();

  assert.equal(watch.active, true);
  assert.equal(watch.path, "/tmp/Inbox");
  assert.notEqual(watch.pollHandle, null);
  assert.deepEqual(stores.job.opErrors, ["watch stop failed"]);
});

test("toggle() routes to start or stop", async () => {
  const { watch } = await createWatchStore();
  let started = 0;
  let stopped = 0;

  watch.start = async () => {
    started += 1;
  };
  watch.stop = async () => {
    stopped += 1;
  };

  watch.active = false;
  await watch.toggle({});

  watch.active = true;
  await watch.toggle({});

  assert.equal(started, 1);
  assert.equal(stopped, 1);
});

test("applyStatus() adopts latest watch job", async () => {
  const { watch, stores } = await createWatchStore();
  let startPollingCalls = 0;
  let stopPollingCalls = 0;

  stores.job.startPolling = () => {
    startPollingCalls += 1;
  };
  stores.job.stopPolling = () => {
    stopPollingCalls += 1;
  };

  watch.applyStatus({
    active: true,
    path: "/tmp/Inbox",
    files_detected: 1,
    jobs_created: 1,
    failed_events: 0,
    last_error: null,
    latest_job_id: "watch-job-1",
    latest_job_status: "failed",
  });

  assert.equal(stores.job.id, "watch-job-1");
  assert.equal(stores.job.origin, "watch");
  assert.equal(stores.job.status, "failed");
  assert.equal(stores.job.opInfo[0], "New watch job detected.");
  assert.equal(stores.job.pendingWatchJobId, "");
  assert.equal(startPollingCalls, 1);
  assert.equal(stopPollingCalls, 1);
});

test("applyStatus() queues when job running", async () => {
  const { watch, stores } = await createWatchStore();

  stores.job.id = "manual-job";
  stores.job.origin = "manual";
  stores.job.status = "running";

  watch.applyStatus({
    active: true,
    path: "/tmp/Inbox",
    files_detected: 1,
    jobs_created: 1,
    failed_events: 0,
    last_error: null,
    latest_job_id: "watch-job-2",
    latest_job_status: "queued",
  });

  assert.equal(stores.job.id, "manual-job");
  assert.equal(stores.job.pendingWatchJobId, "watch-job-2");
});

test("start() sends empty path when no override", async () => {
  const requests = [];
  const { watch } = await createWatchStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        jsonData: {
          active: true,
          path: "/tmp/Inbox",
          files_detected: 0,
          jobs_created: 0,
          failed_events: 0,
          last_error: null,
          latest_job_id: null,
          latest_job_status: null,
        },
      })
    );
  });

  await watch.start({
    strip_signatures: false,
    strip_disclaimers: false,
    strip_quoted_headers: false,
    embed_inline_images: false,
    include_all_headers: false,
    include_raw_html: false,
    no_calendar_summary: false,
    allow_fallback_on_html_error: false,
    delete_eml: false,
    dry_run: false,
  });

  const body = JSON.parse(requests[0].options.body);
  assert.equal(requests[0].url, "/api/watch");
  assert.equal(body.path, "");
  assert.equal(watch.active, true);
  assert.equal(watch.path, "/tmp/Inbox");
});

test("poll() surfaces repeated transient failures", async () => {
  const { watch, stores } = await createWatchStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 503,
        jsonData: {},
      })
    )
  );

  await watch.poll();
  await watch.poll();
  await watch.poll();

  assert.equal(watch.pollErrorCount, 3);
  assert.deepEqual(stores.job.opErrors, ["Watch polling stopped after repeated failures. Toggle Watch to retry."]);
});

test("poll() reports non-transient failure and stops polling", async () => {
  const { watch, stores, intervals } = await createWatchStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 400,
        jsonData: {},
      })
    )
  );

  watch.startPolling();
  const before = intervals.size;
  await watch.poll();

  assert.equal(before > 0, true);
  assert.equal(watch.pollHandle, null);
  assert.deepEqual(stores.job.opErrors, ["Failed to poll watch status."]);
});
