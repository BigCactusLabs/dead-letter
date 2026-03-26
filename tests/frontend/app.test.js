import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/static/app.js");
const INDEX_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/index.html");
const STYLES_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/static/styles.css");

test("watch card uses conic-gradient border animation", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  const css = fs.readFileSync(STYLES_PATH, "utf8");

  assert.doesNotMatch(html, /watch-trace-line/);
  assert.doesNotMatch(html, /<svg[^>]*class="[^"]*watch-trace/);
  assert.match(css, /@property\s+--border-angle/);
  assert.match(css, /@keyframes\s+border-rotate/);
  assert.match(css, /conic-gradient/);
});

test("index.html loads app.js as ES module", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /type="module"\s+src="\/static\/app\.js"/);
  assert.doesNotMatch(html, /htmx/);
  assert.doesNotMatch(html, /alpine\.min\.js/);
});

test("app.js imports Alpine and all stores", () => {
  const source = fs.readFileSync(APP_PATH, "utf8");
  assert.match(source, /import Alpine from/);
  assert.match(source, /import.*registerSettingsStore/);
  assert.match(source, /import.*registerJobStore/);
  assert.match(source, /import.*registerWatchStore/);
  assert.match(source, /Alpine\.start\(\)/);
});

test("template uses $store references for store state", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /\$store\.settings\.configured/);
  assert.match(html, /\$store\.job\.progress/);
  assert.match(html, /\$store\.watch\.active/);
  assert.doesNotMatch(html, /x-(?:show|text|bind)[^>]*="[^"]*(?<!\$store\.)settingsConfigured/);
});

test("workspace panels are inert when inactive and file pickers are multi-select", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /:inert="workspaceState !== 'idle'"/);
  assert.match(html, /:inert="workspaceState !== 'converting'"/);
  assert.match(html, /:inert="workspaceState !== 'done'"/);
  assert.match(html, /:inert="workspaceState !== 'settings'"/);
  const matches = [...html.matchAll(/accept="\.eml"\s+multiple/g)];
  assert.equal(matches.length, 2);
});

test("batch import UI is wired in template and app state", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  const app = fs.readFileSync(APP_PATH, "utf8");

  assert.match(html, /class="batch-confirm workspace-panel"/);
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
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /conversionGrade/);
  assert.match(html, /grade-badge/);
  assert.match(html, /polyline x-show="conversionGrade === 'pass'"/);
  assert.match(html, /polygon x-show="conversionGrade === 'review'"/);
  assert.match(html, /<g x-show="conversionGrade === 'fail'">/);
  assert.doesNotMatch(html, /<template x-if="conversionGrade === 'pass'">/);
});

test("app.js imports computeGrade from helpers", () => {
  const appContent = fs.readFileSync(APP_PATH, "utf8");
  assert.match(appContent, /computeGrade/);
});

test("diagnostics disclosure shows stripped images section", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /stripped-images/);
  assert.match(html, /stripped_images/);
});

test("settings panel has report checkbox", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /options\.report/);
  assert.match(html, /[Gg]enerate.*report/i);
});

test("done panel has report path element", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  assert.match(html, /reportPath/);
  assert.doesNotMatch(html, /\.dead-letter-report\.json/);
});
