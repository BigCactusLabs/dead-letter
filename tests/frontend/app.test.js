import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import * as helpers from "../../src/dead_letter/frontend/static/lib/helpers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/static/app.js");
const INDEX_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/index.html");
const STYLES_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/static/styles.css");

function readHtml() {
  return fs.readFileSync(INDEX_PATH, "utf8");
}

function readApp() {
  return fs.readFileSync(APP_PATH, "utf8");
}

function readStyles() {
  return fs.readFileSync(STYLES_PATH, "utf8");
}

function createApp(jobOverrides = {}) {
  let factory;
  const calls = [];
  const watchers = new Map();
  const noop = () => {};
  const Alpine = { data(_name, create) { factory = create; }, start: noop };
  // The browser uses absolute module URLs. Evaluate the same component body
  // with real helpers and capture its Alpine factory, without a DOM/CDN runtime.
  const source = readApp().split("\n").filter((line) => !line.startsWith("import ")).join("\n");
  vm.runInNewContext(source, {
    ...helpers, Alpine, apiFetch: () => assert.fail("Unexpected app API request"),
    registerSettingsStore: noop, registerJobStore: noop,
    registerWatchStore: noop, registerHistoryStore: noop,
  }, { filename: APP_PATH });
  const app = factory();
  app.$store = {
    job: {
      id: "", kind: "eml", status: "", isSubmitting: false, cancelRequested: false,
      mbox: null, diagnostics: null, errors: [], opErrors: [], opInfo: [],
      progress: { total: 0, completed: 0, failed: 0 }, summary: { written: 0, skipped: 0, errors: 0 },
      clearOpMessages() { this.opErrors = []; this.opInfo = []; },
      setOpError(message) { this.opErrors = [message]; },
      async start(payload) { calls.push({ kind: "eml", payload }); return { ok: true }; },
      async startMbox(payload) { calls.push({ kind: "mbox", payload }); return { ok: true }; },
      ...jobOverrides,
    },
    settings: { configured: true, loading: false, load: noop },
    watch: { active: false, poll: noop },
    history: { load: noop, totals: { jobs_completed: 20, total_written: 100, total_skipped: 10, total_errors: 5 } },
  };
  app.$watch = (key, callback) => watchers.set(key, callback);
  return { app, calls, watchers, job: app.$store.job };
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function assertWorkspacePanelHasInertBinding(html, panelClass) {
  assert.match(
    html,
    new RegExp(
      `class="(?=[^"]*${escapeRegex(panelClass)})(?=[^"]*workspace-panel)[^"]*"[\\s\\S]*?:inert="[^"]+"`
    )
  );
}

test("watch card uses conic-gradient border animation", () => {
  const html = readHtml();
  const css = readStyles();

  assert.doesNotMatch(html, /watch-trace-line/);
  assert.doesNotMatch(html, /<svg[^>]*class="[^"]*watch-trace/);
  assert.match(css, /@property\s+--border-angle/);
  assert.match(css, /@keyframes\s+border-rotate/);
  assert.match(css, /conic-gradient/);
});

test("index.html loads app.js as ES module", () => {
  const html = readHtml();
  assert.match(html, /type="module"\s+src="\/static\/app\.js"/);
  assert.doesNotMatch(html, /htmx/);
  assert.doesNotMatch(html, /alpine\.min\.js/);
});

test("app.js imports Alpine and all stores", () => {
  const source = readApp();
  assert.match(source, /import Alpine from/);
  assert.match(source, /import\s+\{\s*apiFetch\s*\}\s+from\s+"\/static\/lib\/api\.js"/);
  assert.match(source, /import.*registerSettingsStore/);
  assert.match(source, /import.*registerJobStore/);
  assert.match(source, /import.*registerWatchStore/);
  assert.match(source, /Alpine\.start\(\)/);
});

test("template uses $store references for store state", () => {
  const html = readHtml();
  assert.match(html, /\$store\.settings\.configured/);
  assert.match(html, /\$store\.job\.progress/);
  assert.match(html, /\$store\.watch\.active/);
  assert.doesNotMatch(html, /x-(?:show|text|bind)[^>]*="[^"]*(?<!\$store\.)settingsConfigured/);
});

test("workspace panels are inert when inactive and file pickers are multi-select", () => {
  const html = readHtml();
  for (const panelClass of ["drop-zone", "converting-panel", "done-panel", "settings-panel"]) {
    assertWorkspacePanelHasInertBinding(html, panelClass);
  }
  const matches = [...html.matchAll(/<input[^>]*type="file"[^>]*accept="\.eml"[^>]*multiple/g)];
  assert.equal(matches.length, 2);
});

test("batch import UI is wired in template and app state", () => {
  const html = readHtml();
  const app = readApp();

  assert.match(html, /class="(?=[^"]*batch-confirm)(?=[^"]*workspace-panel)[^"]*"/);
  assert.match(html, /x-show="batchConfirm\.show"/);
  assert.match(html, /dragItemCount > 1/);
  assert.match(app, /const SIZE_WARNING_BYTES = 100 \* 1024 \* 1024/);
  assert.match(app, /batchConfirm:\s*\{\s*show:\s*false,\s*emlFiles:\s*\[\],\s*skipped:\s*\[\],\s*totalBytes:\s*0\s*\}/);
  assert.match(app, /dragItemCount:\s*0/);
  assert.match(app, /submitBatchImport\(files\)/);
  assert.match(app, /processDrop\(files\)/);
  assert.match(app, /confirmBatch\(\)/);
  assert.match(app, /cancelBatch\(\)/);
});

test("done header contains grade badge markup", () => {
  const html = readHtml();
  assert.match(html, /conversionGrade/);
  assert.match(html, /grade-badge/);
  assert.match(html, /conversionGrade === 'pass'/);
  assert.match(html, /conversionGrade === 'review'/);
  assert.match(html, /conversionGrade === 'fail'/);
  assert.doesNotMatch(html, /<template x-if="conversionGrade === 'pass'">/);
});

test("app.js imports computeGrade from helpers", () => {
  const appContent = readApp();
  assert.match(appContent, /computeGrade/);
});

test("openCabinet uses CSRF-aware API helper", () => {
  const appContent = readApp();
  assert.match(appContent, /apiFetch\("\/api\/open-folder",\s*\{\s*method:\s*"POST"\s*\}\)/);
});

test("diagnostics disclosure shows stripped images section", () => {
  const html = readHtml();
  assert.match(html, /stripped-images/);
  assert.match(html, /stripped_images/);
});

test("settings panel has report checkbox", () => {
  const html = readHtml();
  assert.match(html, /options\.report/);
  assert.match(html, /[Gg]enerate.*report/i);
});

test("done panel has report path element", () => {
  const html = readHtml();
  assert.match(html, /reportPath/);
  assert.doesNotMatch(html, /\.dead-letter-report\.json/);
});

test("unconfigured banner is present in template", () => {
  const html = readHtml();
  assert.match(html, /class="setup-banner"/);
  assert.match(html, /Workspace not configured/);
  assert.match(html, /Set up now/);
});

test("watch card and import buttons have unconfigured disabled states", () => {
  const html = readHtml();

  // Watch card has disabled-when-unconfigured tooltip
  assert.match(html, /watch-card[\s\S]*?Configure inbox/);

  // File input disabled when unconfigured (already exists - verify it stays)
  assert.match(html, /:disabled="!\$store\.settings\.configured/);
});

test("setup modal markup is present with required elements", () => {
  const html = readHtml();

  // Modal container
  assert.match(html, /class="setup-modal-overlay"/);
  assert.match(html, /\$store\.settings\.showSetupModal/);

  // Onboarding blurb
  assert.match(html, /class="setup-blurb"/);

  // Path fields
  assert.match(html, /x-model="\$store\.settings\.setupInboxPath"/);
  assert.match(html, /x-model="\$store\.settings\.setupCabinetPath"/);

  // Actions
  assert.match(html, /submitSetup\(\)/);
  assert.match(html, /dismissSetup\(\)/);
  assert.match(html, /Create & Get Started/);
  assert.match(html, /Skip for now/);
});


test("options defaults include thread_mode=latest and thread_order=oldest-first", () => {
  const source = readApp();
  assert.match(source, /thread_mode:\s*"latest"/);
  assert.match(source, /thread_order:\s*"oldest-first"/);
});


test("_loadOptions delegates to applyStoredOptions", () => {
  const source = readApp();
  assert.match(source, /import\s*{[^}]*\bapplyStoredOptions\b[^}]*}\s*from\s*"\/static\/lib\/helpers\.js"/);
  assert.match(source, /applyStoredOptions\(/);
});

test("archive form submits its normalized payload through startMbox", async () => {
  const { app, calls } = createApp();
  app.mode = "mbox";
  app.settingsOpen = true;
  app.inputPath = " Downloads\\Takeout.MBOX ";
  app.mboxTimeout = 45;
  Object.assign(app.options, { delete_eml: true, dry_run: true, report: true, thread_mode: "structured" });

  await app.submitJob();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].kind, "mbox");
  assert.equal(calls[0].payload.input_path, "Downloads/Takeout.MBOX");
  assert.equal(calls[0].payload.options.timeout_seconds, 45);
  assert.equal(calls[0].payload.options.thread_mode, "structured");
  for (const key of ["delete_eml", "dry_run", "report"]) {
    assert.equal(Object.hasOwn(calls[0].payload.options, key), false, key);
  }
  assert.equal(app.settingsOpen, false);
  assert.equal(app.formErrors.length, 0);
});

test("archive form keeps invalid paths and time limits local before submission", async () => {
  for (const input of [
    { inputPath: "../mail.mbox", mboxTimeout: 30 },
    { inputPath: "mail.mbox", mboxTimeout: 0 },
  ]) {
    const { app, calls } = createApp();
    Object.assign(app, { mode: "mbox", settingsOpen: true, ...input });
    await app.submitJob();
    assert.equal(calls.length, 0);
    assert.ok(app.formErrors.length > 0);
    assert.equal(app.settingsOpen, true);
    assert.equal(app.inputPath, input.inputPath);
  }
});

test("archive admission errors reopen the form without discarding its input", async () => {
  const { app } = createApp({
    async startMbox() { return { ok: false, formErrors: ["input_path: Archive changed before starting"] }; },
  });
  Object.assign(app, { mode: "mbox", settingsOpen: true, inputPath: "Downloads/mail.mbox" });
  await app.submitJob();
  assert.equal(app.settingsOpen, true);
  assert.equal(app.formErrors[0], "input_path: Archive changed before starting");
  assert.equal(app.inputPath, "Downloads/mail.mbox");
});

test("manual form protects an active archive and archive form protects an active EML job", async () => {
  for (const [mode, existingKind] of [["file", "mbox"], ["mbox", "eml"]]) {
    const { app, calls, job } = createApp({ id: "active-job", kind: existingKind, status: "running" });
    Object.assign(app, { mode, inputPath: mode === "mbox" ? "mail.mbox" : "mail.eml", settingsOpen: true });
    await app.submitJob();
    assert.equal(calls.length, 0);
    assert.equal(job.id, "active-job");
    assert.match(app.formErrors[0], /current job to finish/);
    assert.equal(app.settingsOpen, true);
  }
});

test("archive views use byte progress and this job's counts while EML views retain their totals", () => {
  const { app, job } = createApp({
    id: "archive-1", kind: "mbox", status: "running",
    progress: { total: 0, completed: 2, failed: 1 },
    summary: { written: 2, skipped: 0, errors: 1 },
    mbox: { records_total: null, records_processed: 3, bytes_total: 8192, bytes_processed: 2048, phase: "converting" },
    outputLocation: { cabinet_path: "/tmp/Cabinet", job_path: "/tmp/Cabinet/archive-1" },
  });
  assert.equal(app.workspaceState, "converting");
  assert.equal(app.progressPercent, 0.25);
  assert.equal(app.progressCountLabel, "3 messages processed");
  assert.equal(app.inboxStatLabel, "3 processed");
  assert.equal(app.cabinetStatLabel, "2 bundles");
  assert.equal(app.outputCabinetPath, "/tmp/Cabinet/archive-1");
  assert.match(app.mboxNotice, /source archive is kept/);

  job.mbox.phase = "publishing";
  assert.match(app.mboxNotice, /Finishing the import report/);
  job.cancelRequested = true;
  assert.match(app.mboxNotice, /Stopping the message worker/);
  job.status = "cancelled";
  job.mbox.phase = "finished";
  assert.equal(app.workspaceState, "done");
  assert.match(app.mboxNotice, /Completed bundles stay/);
  assert.equal(app.doneWrittenCount, 2);
  assert.equal(app.doneSkippedCount, 0);
  assert.equal(app.doneErrorsCount, 1);
  job.status = "completed_with_errors";
  assert.match(app.mboxNotice, /Some messages could not be converted/);

  job.kind = "eml";
  job.status = "running";
  job.progress = { total: 8, completed: 3, failed: 1 };
  assert.equal(app.progressPercent, 0.5);
  assert.equal(app.progressCountLabel, "4 / 8");
  assert.equal(app.inboxStatLabel, "4 files");
  assert.equal(app.mboxNotice, "");
  job.status = "succeeded";
  assert.equal(app.doneWrittenCount, 100);
  assert.equal(app.doneSkippedCount, 10);
  assert.equal(app.doneErrorsCount, 5);
});

test("archive progress announcements update without inventing a record total", () => {
  const { app, job, watchers } = createApp({
    id: "archive-1", kind: "mbox", status: "running",
    mbox: { records_total: null, records_processed: 0, bytes_total: 8192, bytes_processed: 0 },
  });
  app.init();
  watchers.get("workspaceState")("converting");
  assert.equal(app.liveAnnouncement, "Starting archive import");
  Object.assign(job.mbox, { records_processed: 1, bytes_processed: 1024 });
  watchers.get("$store.job.mbox")();
  assert.equal(app.liveAnnouncement, "1 message processed. 1.0 KiB of 8.0 KiB archive bytes processed");
});
