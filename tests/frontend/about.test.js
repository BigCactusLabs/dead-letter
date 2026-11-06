import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/index.html");
const STYLES_PATH = path.resolve(__dirname, "../../src/dead_letter/frontend/static/styles.css");

const PROJECT_LINKS = {
  "Documentation": "https://github.com/BigCactusLabs/dead-letter/tree/main/docs",
  "GitHub": "https://github.com/BigCactusLabs/dead-letter",
  "Report an issue": "https://github.com/BigCactusLabs/dead-letter/issues/new/choose",
};

function makeResponse({ ok = true, status = 200, jsonData = {} } = {}) {
  return { ok, status, async json() { return jsonData; } };
}

async function createAboutStore(fetchImpl) {
  const { registerAboutStore } = await import("../../src/dead_letter/frontend/static/stores/about.js");
  const stores = {};
  const requests = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    return fetchImpl(url, options);
  };
  registerAboutStore({ store(name, def) { if (def !== undefined) stores[name] = def; return stores[name]; } });
  return { about: stores.about, requests };
}

function footerHtml() {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  const match = html.match(/<footer class="project-footer"[\s\S]*?<\/footer>/);
  assert.ok(match, "project footer is present");
  return { html, footer: match[0] };
}

test("load() reads the version from GET /api/about", async () => {
  const { about, requests } = await createAboutStore(() =>
    Promise.resolve(makeResponse({ jsonData: { version: "0.4.11.dev0" } })));
  assert.equal(about.version, "");
  await about.load();
  assert.equal(about.version, "0.4.11.dev0");
  assert.deepEqual(requests.map(({ url, options }) => [url, options.method ?? "GET"]), [["/api/about", "GET"]]);
});

test("load() leaves the version empty on HTTP or network failure", async () => {
  const failed = await createAboutStore(() => Promise.resolve(makeResponse({ ok: false, status: 500 })));
  await failed.about.load();
  assert.equal(failed.about.version, "");

  const offline = await createAboutStore(() => Promise.reject(new Error("offline")));
  await offline.about.load();
  assert.equal(offline.about.version, "");
});

test("load() ignores payloads that are not a version string", async () => {
  for (const version of [undefined, 4, "", "<img src=x>", "x".repeat(65)]) {
    const { about } = await createAboutStore(() => Promise.resolve(makeResponse({ jsonData: { version } })));
    await about.load();
    assert.equal(about.version, "", `rejected ${JSON.stringify(version)}`);
  }
});

test("project footer links to docs, repository, and issue reporting in a new tab", () => {
  const { footer } = footerHtml();
  const anchors = [...footer.matchAll(/<a\s+href="([^"]+)"\s+target="_blank"\s+rel="noopener noreferrer">([^<]+)</g)];
  assert.deepEqual(Object.fromEntries(anchors.map(([, href, label]) => [label, href])), PROJECT_LINKS);
  assert.equal((footer.match(/\(opens in a new tab\)/g) || []).length, anchors.length);
  assert.match(footer, /<nav class="project-links" aria-label="dead-letter project">/);
});

test("project footer shows the version only once it has loaded", () => {
  const { footer } = footerHtml();
  assert.match(footer, /class="project-version" x-show="\$store\.about\.version" x-cloak/);
  assert.match(footer, /x-text="\$store\.about\.version"/);
});

test("project footer sits inside the inert-able shell after the paths strip", () => {
  const { html } = footerHtml();
  const shellStart = html.indexOf('<main class="shell"');
  const paths = html.indexOf('<section class="paths-readout"');
  const footer = html.indexOf('<footer class="project-footer"');
  const shellEnd = html.indexOf("</main>");
  assert.ok(shellStart < paths && paths < footer && footer < shellEnd);
});

test("project footer uses Brume tokens with no decorative surface", () => {
  const css = fs.readFileSync(STYLES_PATH, "utf8");
  const rules = css.match(/\/\* ===== Project Footer ===== \*\/[\s\S]*?(?=\/\* =====)/);
  assert.ok(rules, "project footer styles are present");
  assert.doesNotMatch(rules[0], /#[0-9a-f]{3,8}\b|gradient|border-radius|box-shadow/i);
  assert.match(rules[0], /\.project-links a:hover \{\s*color: var\(--ink\);/);
});
