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

function headerValue(headers, name) {
  if (headers instanceof Headers) {
    return headers.get(name) ?? undefined;
  }
  return headers?.[name] ?? headers?.[name.toLowerCase()];
}

class FormDataStub {
  constructor() {
    this.entries = [];
  }

  append(name, value) {
    this.entries.push([name, value]);
  }
}

async function createJobStore(fetchImpl, requestLog = null) {
  const { registerJobStore } = await import("../../src/dead_letter/frontend/static/stores/job.js");
  const { resetCsrfTokenForTests } = await import("../../src/dead_letter/frontend/static/lib/api.js");
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
  resetCsrfTokenForTests();

  const mockAlpine = {
    store(name, def) {
      if (def !== undefined) stores[name] = def;
      return stores[name];
    },
  };
  stores.watch = {
    adoptWatchJob() {},
  };
  globalThis.fetch = (url, options = {}) => {
    requestLog?.push({ url, options });
    if (url === "/api/session") {
      return Promise.resolve(makeResponse({ jsonData: { csrf_token: "csrf-test-token" } }));
    }
    return fetchImpl ? fetchImpl(url, options) : Promise.resolve(makeResponse());
  };
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

test("start() fetches CSRF session before mutating request", async () => {
  const requests = [];
  const { job } = await createJobStore(
    () =>
      Promise.resolve(
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
      ),
    requests
  );

  job.startPolling = () => {};
  await job.start({
    mode: "file",
    input_path: "/tmp/mail.eml",
    options: { delete_eml: false, dry_run: false },
  });

  assert.deepEqual(requests.map((request) => request.url), ["/api/session", "/api/jobs"]);
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-test-token");
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

function mboxStatus(overrides = {}) {
  return {
    id: "archive-1", kind: "mbox", status: "running", cancel_requested: false,
    output_location: {
      strategy: "cabinet", cabinet_path: "/tmp/Cabinet", bundle_path: null,
      job_path: "/tmp/Cabinet/mbox-archive-1",
    },
    mbox: {
      source_path: "Downloads/mail.mbox", records_total: null, records_processed: 3,
      bytes_total: 8192, bytes_processed: 2048, failures_omitted: 0, phase: "converting",
    },
    progress: { total: 0, completed: 2, failed: 1, current: null },
    summary: { written: 2, skipped: 0, errors: 1 },
    errors: [{ path: "mail.mbox#message-00000002", code: "conversion_error", message: "Message failed" }],
    report_path: null, created_at: "2026-10-27T10:00:00Z",
    started_at: "2026-10-27T10:00:01Z", finished_at: null,
    ...overrides,
  };
}

test("startMbox() uses the archive route with CSRF and retains its job folder", async () => {
  const requests = [];
  const started = mboxStatus({ status: "queued" });
  const { job } = await createJobStore(() => Promise.resolve(makeResponse({ jsonData: started })), requests);
  let polls = 0;
  job.startPolling = () => { polls += 1; };
  const payload = { input_path: "Downloads/mail.mbox", options: { timeout_seconds: 30 } };

  assert.deepEqual(await job.startMbox(payload), { ok: true });

  assert.deepEqual(requests.map(({ url }) => url), ["/api/session", "/api/mbox/jobs"]);
  assert.equal(requests[1].options.method, "POST");
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-test-token");
  assert.deepEqual(JSON.parse(requests[1].options.body), payload);
  assert.equal(job.id, "archive-1");
  assert.equal(job.kind, "mbox");
  assert.equal(job.origin, "mbox");
  assert.equal(job.status, "queued");
  assert.deepEqual(job.mbox, started.mbox);
  assert.equal(job.outputLocation.job_path, "/tmp/Cabinet/mbox-archive-1");
  assert.equal(polls, 1);
});

test("startMbox() returns backend validation errors without starting polling", async () => {
  const { job } = await createJobStore(() => Promise.resolve(makeResponse({
    ok: false, status: 400,
    jsonData: { errors: [{ path: "input_path", message: "Archive is outside the browser root" }] },
  })));
  job.startPolling = () => assert.fail("A rejected import must not start polling");
  const result = await job.startMbox({ input_path: "mail.mbox", options: {} });
  assert.deepEqual(result, { ok: false, formErrors: ["input_path: Archive is outside the browser root"] });
  assert.equal(job.id, "");
  assert.equal(job.isSubmitting, false);
});

test("archive polling preserves partial counts, errors, report and job folder through failure", async () => {
  const running = mboxStatus();
  const finished = mboxStatus({
    status: "failed", report_path: "/tmp/Cabinet/mbox-archive-1/report.json",
    mbox: { ...running.mbox, phase: "finished" },
    errors: [...running.errors, { code: "mbox_storage_error", message: "Storage exhausted" }],
  });
  const snapshots = [running, finished];
  const requests = [];
  const { job, stores, intervals } = await createJobStore(
    () => Promise.resolve(makeResponse({ jsonData: snapshots.shift() })), requests
  );
  let historyLoads = 0;
  stores.history = { load() { historyLoads += 1; } };
  job.id = "archive-1";
  job.pollHandle = window.setInterval(() => {}, 800);
  const activeHandle = job.pollHandle;

  await job.poll();
  assert.equal(job.kind, "mbox");
  assert.equal(job.status, "running");
  assert.equal(job.mbox.records_total, null);
  assert.equal(job.mbox.bytes_processed, 2048);
  assert.deepEqual(job.errors, running.errors);
  assert.deepEqual(job.summary, running.summary);
  assert.equal(job.outputLocation.job_path, running.output_location.job_path);
  assert.equal(job.pollHandle, activeHandle);
  assert.equal(historyLoads, 0);

  await job.poll();
  assert.deepEqual(requests.map(({ url }) => url), ["/api/jobs/archive-1", "/api/jobs/archive-1"]);
  assert.equal(job.status, "failed");
  assert.deepEqual(job.errors, finished.errors);
  assert.deepEqual(job.summary, running.summary);
  assert.equal(job.reportPath, finished.report_path);
  assert.equal(job.outputLocation.job_path, running.output_location.job_path);
  assert.equal(job.pollHandle, null);
  assert.equal(intervals.size, 0);
  assert.equal(historyLoads, 1);
});

test("archive cancellation stays nonterminal and keeps polling until the partial report is final", async () => {
  const requests = [];
  const final = mboxStatus({
    status: "cancelled", cancel_requested: true,
    report_path: "/tmp/Cabinet/mbox-archive-1/report.json",
  });
  const { job, intervals } = await createJobStore((url) => Promise.resolve(makeResponse({
    jsonData: url.endsWith("/cancel") ? { accepted: true, status: "running" } : final,
  })), requests);
  job.id = "archive-1";
  job.kind = "mbox";
  job.status = "running";
  job.pollHandle = window.setInterval(() => {}, 800);
  const activeHandle = job.pollHandle;

  await job.cancel();
  assert.deepEqual(requests.map(({ url }) => url), ["/api/session", "/api/jobs/archive-1/cancel"]);
  assert.equal(requests[1].options.method, "POST");
  assert.equal(headerValue(requests[1].options.headers, "X-Dead-Letter-CSRF"), "csrf-test-token");
  assert.equal(job.cancelRequested, true);
  assert.equal(job.status, "running");
  assert.equal(job.pollHandle, activeHandle);
  assert.equal(intervals.size, 1);
  assert.match(job.opInfo[0], /partial report/);
  assert.match(job.opInfo[0], /Completed bundles are kept/);

  await job.poll();
  assert.equal(job.status, "cancelled");
  assert.equal(job.pollHandle, null);
  assert.equal(intervals.size, 0);
  assert.equal(job.reportPath, final.report_path);
  assert.equal(job.summary.written, 2);
});

test("manual starts and uploads cannot replace an active or cancelling archive", async (t) => {
  const operations = {
    start: (job) => job.start({ mode: "file", input_path: "mail.eml", options: {} }),
    importFile: (job) => job.importFile({ name: "mail.eml" }, {}),
    importBatch: (job) => job.importBatch([{ name: "mail.eml" }], {}),
  };
  for (const [name, run] of Object.entries(operations)) {
    for (const status of ["queued", "running", "cancelling"]) {
      await t.test(`${name}: ${status}`, async () => {
        const requests = [];
        const { job } = await createJobStore(null, requests);
        job.id = "archive-1";
        job.kind = "mbox";
        job.status = status === "cancelling" ? "running" : status;
        job.cancelRequested = status === "cancelling";
        job.mbox = mboxStatus().mbox;
        job.outputLocation = mboxStatus().output_location;
        job.reportPath = "/tmp/Cabinet/mbox-archive-1/report.json";
        job.pollHandle = window.setInterval(() => {}, 800);
        job.activePollController = new AbortController();
        const previous = {
          id: job.id, status: job.status, mbox: job.mbox, outputLocation: job.outputLocation,
          reportPath: job.reportPath, pollHandle: job.pollHandle, pollSessionId: job.pollSessionId,
        };
        const controller = job.activePollController;

        await run(job);

        assert.equal(requests.length, 0);
        for (const [key, value] of Object.entries(previous)) assert.equal(job[key], value, key);
        assert.equal(job.cancelRequested, status === "cancelling");
        assert.equal(controller.signal.aborted, false);
        assert.match(job.opErrors[0], /archive import to finish/);
      });
    }
  }
});

test("startMbox() cannot replace an active EML job", async () => {
  const requests = [];
  const { job } = await createJobStore(null, requests);
  job.id = "eml-1";
  job.status = "running";
  assert.deepEqual(await job.startMbox({ input_path: "mail.mbox", options: {} }), { ok: false });
  assert.equal(job.id, "eml-1");
  assert.equal(requests.length, 0);
});

test("an EML start after a terminal archive clears archive metadata and report", async () => {
  const { job } = await createJobStore(() => Promise.resolve(makeResponse({
    jsonData: { id: "eml-next", status: "queued" },
  })));
  Object.assign(job, { id: "archive-1", kind: "mbox", status: "cancelled",
    mbox: mboxStatus().mbox, reportPath: "/tmp/old-report.json" });
  job.startPolling = () => {};
  assert.deepEqual(await job.start({ mode: "file", input_path: "mail.eml", options: {} }), { ok: true });
  assert.equal(job.id, "eml-next");
  assert.equal(job.kind, "eml");
  assert.equal(job.mbox, null);
  assert.equal(job.reportPath, null);
});
