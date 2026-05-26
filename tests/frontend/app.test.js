import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
