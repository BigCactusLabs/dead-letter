import { test } from "node:test";
import assert from "node:assert/strict";

function toPlain(value) {
  return JSON.parse(JSON.stringify(value));
}

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

class FormDataStub {
  constructor() {
    this.entries = [];
  }

  append(name, value) {
    this.entries.push([name, value]);
  }
}

async function createJobStore(fetchImpl) {
  const { registerJobStore } = await import("../../src/dead_letter/frontend/static/stores/job.js");
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
  globalThis.FormData = FormDataStub;

  const mockAlpine = {
    store(name, def) {
      if (def !== undefined) stores[name] = def;
      return stores[name];
    },
  };
  stores.watch = {
    adoptWatchJob() {},
  };
  globalThis.fetch = fetchImpl || (() => Promise.resolve(makeResponse()));
  registerJobStore(mockAlpine);
  return { job: stores.job, stores, intervals };
}

test("start() stores cabinet output_location", async () => {
  const requests = [];
  const { job } = await createJobStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        jsonData: {
          id: "job-1",
          status: "queued",
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/mail",
          },
        },
      })
    );
  });

  job.startPolling = () => {};
  await job.start({
    mode: "file",
    input_path: "/tmp/mail.eml",
    options: {
      strip_signatures: false,
      delete_eml: false,
      dry_run: false,
    },
  });

  assert.equal(job.id, "job-1");
  assert.deepEqual(toPlain(job.outputLocation), {
    strategy: "cabinet",
    cabinet_path: "/tmp/Cabinet",
    bundle_path: "/tmp/Cabinet/mail",
  });

  const body = JSON.parse(requests[0].options.body);
  assert.equal(Object.hasOwn(body, "output_path"), false);
});

test("poll() updates cabinet output_location", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: {
          status: "running",
          cancel_requested: false,
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/mail",
          },
          progress: { total: 1, completed: 0, failed: 0, current: null },
          summary: { written: 0, skipped: 0, errors: 0 },
          errors: [],
          created_at: "2026-03-06T00:00:00Z",
          started_at: null,
          finished_at: null,
        },
      })
    )
  );

  job.id = "job-1";
  await job.poll();

  assert.deepEqual(toPlain(job.outputLocation), {
    strategy: "cabinet",
    cabinet_path: "/tmp/Cabinet",
    bundle_path: "/tmp/Cabinet/mail",
  });
});

test("poll() stores diagnostics", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: {
          status: "succeeded",
          cancel_requested: false,
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/mail",
          },
          progress: { total: 1, completed: 1, failed: 0, current: null },
          summary: { written: 1, skipped: 0, errors: 0 },
          errors: [],
          recovery_actions: [
            {
              kind: "retry_with_html_repair",
              label: "Retry with HTML repair",
              message: "retry me",
            },
          ],
          diagnostics: {
            state: "degraded",
            selected_body: "html",
            segmentation_path: "html",
            client_hint: "gmail",
            confidence: "medium",
            fallback_used: "html_markdown_panic_repaired",
            warnings: [],
          },
          created_at: "2026-03-06T00:00:00Z",
          started_at: "2026-03-06T00:00:01Z",
          finished_at: "2026-03-06T00:00:02Z",
        },
      })
    )
  );

  job.id = "job-1";
  await job.poll();

  assert.equal(job.diagnostics.state, "degraded");
  assert.equal(job.recoveryActions[0].kind, "retry_with_html_repair");
});

test("resetState() clears diagnostics", async () => {
  const { job } = await createJobStore();

  job.diagnostics = {
    state: "review_recommended",
    selected_body: "plain",
    segmentation_path: "plain_fallback",
    client_hint: "generic",
    confidence: "low",
    fallback_used: "plain_text_reply_parser",
    warnings: [],
  };
  job.recoveryActions = [
    {
      kind: "retry_with_html_repair",
      label: "Retry with HTML repair",
      message: "retry me",
    },
  ];
  job.diagnosticsOpen = true;

  job.resetState();

  assert.equal(job.diagnostics, null);
  assert.deepEqual(toPlain(job.recoveryActions), []);
  assert.equal(job.diagnosticsOpen, false);
});

test("retry() posts action and preserves origin", async () => {
  const requests = [];
  const { job } = await createJobStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        status: 202,
        jsonData: {
          id: "retry-job-1",
          status: "queued",
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/mail",
          },
        },
      })
    );
  });

  job.startPolling = () => {};
  job.id = "watch-job-1";
  job.origin = "watch";
  job.status = "failed";
  job.recoveryActions = [{ kind: "retry_with_html_repair", label: "Retry", message: "retry me" }];

  await job.retry("retry_with_html_repair");

  assert.equal(requests[0].url, "/api/jobs/watch-job-1/retry");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), { action: "retry_with_html_repair" });
  assert.equal(job.id, "retry-job-1");
  assert.equal(job.origin, "watch");
  assert.deepEqual(toPlain(job.opInfo), ["Retry started with HTML repair enabled."]);
});

test("retry() uses action-specific copy", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        status: 202,
        jsonData: {
          id: "retry-job-2",
          status: "queued",
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/mail",
          },
        },
      })
    )
  );

  job.startPolling = () => {};
  job.id = "job-2";
  job.origin = "manual";
  job.status = "failed";
  job.recoveryActions = [{ kind: "retry_with_html_fallback", label: "Retry", message: "retry me" }];

  await job.retry("retry_with_html_fallback");

  assert.deepEqual(toPlain(job.opInfo), ["Retry started with plain-text fallback enabled."]);
});

test("poll() ignores stale responses", async () => {
  const first = deferred();
  const { job } = await createJobStore(() => first.promise);

  job.id = "job-1";
  job.diagnostics = {
    state: "normal",
    selected_body: "plain",
    segmentation_path: "plain_fallback",
    client_hint: "generic",
    confidence: "medium",
    fallback_used: null,
    warnings: [],
  };

  const pollPromise = job.poll();
  job.stopPolling();

  first.resolve(
    makeResponse({
      jsonData: {
        status: "succeeded",
        cancel_requested: false,
        output_location: {
          strategy: "cabinet",
          cabinet_path: "/tmp/Cabinet",
          bundle_path: "/tmp/Cabinet/mail",
        },
        progress: { total: 1, completed: 1, failed: 0, current: null },
        summary: { written: 1, skipped: 0, errors: 0 },
        errors: [],
        diagnostics: {
          state: "review_recommended",
          selected_body: "html",
          segmentation_path: "html",
          client_hint: "outlook",
          confidence: "low",
          fallback_used: "plain_text_reply_parser",
          warnings: [],
        },
        created_at: "2026-03-06T00:00:00Z",
        started_at: "2026-03-06T00:00:01Z",
        finished_at: "2026-03-06T00:00:02Z",
      },
    })
  );
  await pollPromise;

  assert.equal(job.diagnostics.state, "normal");
  assert.equal(job.diagnostics.selected_body, "plain");
});

test("importFile() posts to /api/import and returns imported_path", async () => {
  const requests = [];
  const { job } = await createJobStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        status: 202,
        jsonData: {
          imported_path: "/tmp/Inbox/upload.eml",
          id: "job-2",
          status: "queued",
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/upload",
          },
        },
      })
    );
  });

  job.startPolling = () => {};

  const result = await job.importFile(
    { name: "upload.eml" },
    {
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
    }
  );

  assert.equal(requests[0].url, "/api/import");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(toPlain(requests[0].options.body.entries), [
    ["file", { name: "upload.eml" }],
    [
      "options",
      JSON.stringify({
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
      }),
    ],
  ]);
  assert.equal(result.imported_path, "/tmp/Inbox/upload.eml");
  assert.equal(job.origin, "import");
  assert.deepEqual(toPlain(job.outputLocation), {
    strategy: "cabinet",
    cabinet_path: "/tmp/Cabinet",
    bundle_path: "/tmp/Cabinet/upload",
  });
});

test("importBatch() posts to /api/import-batch and returns imported_paths", async () => {
  const requests = [];
  const { job } = await createJobStore((url, options = {}) => {
    requests.push({ url, options });
    return Promise.resolve(
      makeResponse({
        status: 202,
        jsonData: {
          imported_paths: ["/tmp/Inbox/_batch-1/a.eml", "/tmp/Inbox/_batch-1/b.eml"],
          id: "job-3",
          status: "queued",
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: null,
          },
        },
      })
    );
  });

  job.startPolling = () => {};

  const result = await job.importBatch(
    [{ name: "a.eml" }, { name: "b.eml" }],
    { delete_eml: true, dry_run: true }
  );

  assert.equal(requests[0].url, "/api/import-batch");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(toPlain(requests[0].options.body.entries), [
    ["files", { name: "a.eml" }],
    ["files", { name: "b.eml" }],
    ["options", JSON.stringify({ delete_eml: false, dry_run: true })],
  ]);
  assert.deepEqual(result, {
    imported_paths: ["/tmp/Inbox/_batch-1/a.eml", "/tmp/Inbox/_batch-1/b.eml"],
  });
  assert.equal(job.origin, "import");
  assert.deepEqual(toPlain(job.outputLocation), {
    strategy: "cabinet",
    cabinet_path: "/tmp/Cabinet",
    bundle_path: null,
  });
});

test("poll() calls watch.adoptWatchJob after terminal", async () => {
  let adoptedArgs = null;
  const { job, stores } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        jsonData: {
          status: "succeeded",
          cancel_requested: false,
          output_location: {
            strategy: "cabinet",
            cabinet_path: "/tmp/Cabinet",
            bundle_path: "/tmp/Cabinet/manual-job",
          },
          progress: { total: 1, completed: 1, failed: 0, current: null },
          summary: { written: 1, skipped: 0, errors: 0 },
          errors: [],
          diagnostics: null,
          created_at: "2026-03-11T12:00:00Z",
          started_at: "2026-03-11T12:00:01Z",
          finished_at: "2026-03-11T12:00:02Z",
        },
      })
    )
  );

  stores.watch.adoptWatchJob = (jobId, status) => {
    adoptedArgs = { jobId, status };
  };
  job.id = "manual-job";
  job.origin = "manual";
  job.status = "running";
  job.pendingWatchJobId = "watch-job-3";
  job.pendingWatchJobStatus = "queued";

  await job.poll(job.pollSessionId);

  assert.deepEqual(adoptedArgs, { jobId: "watch-job-3", status: "queued" });
});

test("start() returns formErrors on 400", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 400,
        jsonData: {
          errors: [{ path: "input_path", message: "Input path is required." }],
        },
      })
    )
  );

  const result = await job.start({
    mode: "file",
    input_path: "",
    options: { dry_run: false, delete_eml: false },
  });

  assert.deepEqual(result.formErrors, ["input_path: Input path is required."]);
});

test("start() sets opError on 500", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 500,
        jsonData: {
          errors: [{ message: "backend down" }],
        },
      })
    )
  );

  await job.start({
    mode: "file",
    input_path: "/tmp/file.eml",
    options: { dry_run: false, delete_eml: false },
  });

  assert.deepEqual(toPlain(job.opErrors), ["backend down"]);
});

test("importFile() returns null on error", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 500,
        jsonData: { errors: [{ message: "Import failed." }] },
      })
    )
  );

  const result = await job.importFile({ name: "upload.eml" }, { delete_eml: false, dry_run: false });
  assert.equal(result, null);
});

test("importBatch() returns null on error", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        ok: false,
        status: 500,
        jsonData: { errors: [{ message: "Batch import failed." }] },
      })
    )
  );

  const result = await job.importBatch([{ name: "upload.eml" }], { delete_eml: false, dry_run: false });
  assert.equal(result, null);
});

test("cancel() sets cancelRequested on success", async () => {
  const { job } = await createJobStore(() =>
    Promise.resolve(
      makeResponse({
        ok: true,
        status: 200,
        jsonData: {
          status: "running",
          accepted: true,
        },
      })
    )
  );

  job.id = "job-1";
  job.status = "running";
  await job.cancel();

  assert.equal(job.cancelRequested, true);
});

test("clearOpMessages() resets opErrors and opInfo", async () => {
  const { job } = await createJobStore();

  job.opErrors = ["error"];
  job.opInfo = ["info"];
  job.clearOpMessages();

  assert.deepEqual(job.opErrors, []);
  assert.deepEqual(job.opInfo, []);
});
