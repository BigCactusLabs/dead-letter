import { test } from "node:test";
import assert from "node:assert/strict";

const {
  isTerminalStatus,
  normalizeErrors,
  firstErrorMessage,
  formatErrorItem,
  normalizeDiagnostics,
  normalizeRecoveryActions,
  buildPayload,
  buildMboxPayload,
  validateForm,
  validateMboxForm,
  jobProgressView,
} = await import("../../src/dead_letter/frontend/static/lib/helpers.js");

test("isTerminalStatus recognises all terminal statuses", () => {
  assert.equal(isTerminalStatus("succeeded"), true);
  assert.equal(isTerminalStatus("completed_with_errors"), true);
  assert.equal(isTerminalStatus("failed"), true);
  assert.equal(isTerminalStatus("cancelled"), true);
  assert.equal(isTerminalStatus("running"), false);
  assert.equal(isTerminalStatus("queued"), false);
});

test("normalizeErrors extracts errors from payload variants", () => {
  assert.deepEqual(normalizeErrors(null), []);
  assert.deepEqual(normalizeErrors({}), []);
  assert.deepEqual(normalizeErrors({ errors: [{ message: "a" }] }), [{ message: "a" }]);
  assert.deepEqual(
    normalizeErrors({ detail: { errors: [{ message: "b" }] } }),
    [{ message: "b" }]
  );
});

test("firstErrorMessage returns first error or fallback", () => {
  assert.equal(firstErrorMessage(null, "fallback"), "fallback");
  assert.equal(firstErrorMessage({}, "fallback"), "fallback");
  assert.equal(
    firstErrorMessage({ errors: [{ path: "file.eml", message: "bad" }] }, "x"),
    "file.eml: bad"
  );
  assert.equal(firstErrorMessage({ errors: [{ message: "no path" }] }, "x"), "no path");
});

test("formatErrorItem formats path:message or message-only", () => {
  assert.equal(formatErrorItem(null), "");
  assert.equal(formatErrorItem({ message: "oops" }), "oops");
  assert.equal(formatErrorItem({ path: "a.eml", message: "oops" }), "a.eml: oops");
});

test("normalizeDiagnostics validates required string fields", () => {
  assert.equal(normalizeDiagnostics(null), null);
  assert.equal(normalizeDiagnostics({}), null);
  assert.equal(normalizeDiagnostics({ state: "normal" }), null);

  const valid = normalizeDiagnostics({
    state: "normal",
    selected_body: "html",
    segmentation_path: "html",
    confidence: "high",
  });
  assert.equal(valid.state, "normal");
  assert.equal(valid.client_hint, null);
  assert.deepEqual(valid.warnings, []);
});

test("normalizeRecoveryActions filters invalid entries", () => {
  assert.deepEqual(normalizeRecoveryActions(null), []);
  assert.deepEqual(normalizeRecoveryActions("bad"), []);
  assert.deepEqual(normalizeRecoveryActions([null, { kind: "a", label: "b", message: "c" }]), [
    { kind: "a", label: "b", message: "c" },
  ]);
});

test("buildPayload constructs job payload with delete_eml safety", () => {
  const result = buildPayload("file", " /tmp/mail.eml ", {
    strip_signatures: false,
    delete_eml: true,
    dry_run: true,
  });
  assert.equal(result.input_path, "/tmp/mail.eml");
  assert.equal(result.options.delete_eml, false);
});


test("buildPayload propagates thread_mode and thread_order defaults", () => {
  const result = buildPayload("file", "/tmp/mail.eml", {
    strip_signatures: false,
    thread_mode: "latest",
    thread_order: "oldest-first",
  });
  assert.equal(result.options.thread_mode, "latest");
  assert.equal(result.options.thread_order, "oldest-first");
});


test("buildPayload propagates user-modified thread_mode", () => {
  const result = buildPayload("file", "/tmp/mail.eml", {
    thread_mode: "structured",
    thread_order: "latest-first",
  });
  assert.equal(result.options.thread_mode, "structured");
  assert.equal(result.options.thread_order, "latest-first");
});

test("validateForm returns errors for missing fields", () => {
  assert.deepEqual(validateForm({ mode: "file", inputPath: "" }), ["Input path is required."]);
  assert.deepEqual(validateForm({ mode: "bad", inputPath: "/tmp/x" }), [
    "Mode must be file or directory.",
  ]);
  assert.deepEqual(validateForm({ mode: "file", inputPath: "/tmp/x" }), []);
});

test("buildMboxPayload selects supported options and normalizes a root-relative path", () => {
  const allowed = {
    strip_signatures: true,
    strip_disclaimers: false,
    strip_quoted_headers: true,
    strip_signature_images: true,
    strip_tracking_pixels: true,
    embed_inline_images: false,
    include_all_headers: true,
    include_raw_html: false,
    no_calendar_summary: true,
    allow_fallback_on_html_error: true,
    allow_html_repair_on_panic: true,
    thread_mode: "structured",
    thread_order: "latest-first",
  };
  const options = Object.freeze({
    ...allowed, delete_eml: true, dry_run: true, report: true,
    source_handling: "move", resume: true, timeout_seconds: 999, output_path: "/outside",
  });

  assert.deepEqual(buildMboxPayload("  Downloads\\Takeout.MBOX  ", options, 45), {
    input_path: "Downloads/Takeout.MBOX",
    options: { ...allowed, timeout_seconds: 45 },
  });
  assert.deepEqual(buildMboxPayload("mail.mbox", {}), {
    input_path: "mail.mbox", options: { timeout_seconds: 30 },
  });
});

test("validateMboxForm accepts only root-relative flat MBOX paths and bounded numeric timeouts", () => {
  for (const inputPath of ["mail.mbox", " Downloads/Takeout.MBOX ", "Mail\\Export.mbox"]) {
    for (const timeoutSeconds of [0.5, 30, 300]) {
      assert.deepEqual(validateMboxForm({ inputPath, timeoutSeconds }), [], inputPath);
    }
  }
  for (const inputPath of [
    "", "mail.eml", "mail.mbox.gz", "/tmp/mail.mbox", "C:\\Mail\\mail.mbox",
    "C:mail.mbox", "\\\\server\\mail.mbox", "~/mail.mbox", "../mail.mbox", "Mail/../mail.mbox",
  ]) {
    assert.ok(validateMboxForm({ inputPath }).length > 0, inputPath);
  }
  for (const timeoutSeconds of [0, -1, 300.1, NaN, Infinity, -Infinity, "30", null, true]) {
    assert.match(
      validateMboxForm({ inputPath: "mail.mbox", timeoutSeconds }).join(" "),
      /time limit/,
      String(timeoutSeconds)
    );
  }
});

test("jobProgressView measures archive bytes while the record total is unknown", () => {
  assert.deepEqual(jobProgressView({
    kind: "mbox",
    progress: { total: 0, completed: 2, failed: 1 },
    mbox: { bytes_total: 8192, bytes_processed: 2048, records_processed: 3, records_total: null },
  }), {
    value: 2048, max: 8192, fraction: 0.25,
    label: "3 messages processed", detail: "2.0 KiB of 8.0 KiB archive bytes processed",
  });
  const waiting = jobProgressView({ kind: "mbox", mbox: null });
  assert.deepEqual(waiting, {
    value: 0, max: 1, fraction: 0, label: "0 messages processed", detail: "Waiting for archive metadata",
  });
  assert.deepEqual(jobProgressView({
    kind: "mbox", mbox: { bytes_total: 0, bytes_processed: 0, records_processed: 0, records_total: null },
  }), {
    value: 0, max: 1, fraction: 0, label: "0 messages processed", detail: "0 B of 0 B archive bytes processed",
  });
  assert.equal(jobProgressView({
    kind: "mbox", mbox: { bytes_total: 10, bytes_processed: 20, records_processed: 1 },
  }).fraction, 1);
});

test("jobProgressView preserves EML completed-plus-failed progress", () => {
  for (const kind of ["eml", undefined]) {
    assert.deepEqual(jobProgressView({ kind, progress: { total: 8, completed: 3, failed: 1 } }), {
      value: 4, max: 8, fraction: 0.5, label: "4 / 8", detail: "",
    });
  }
});

test("normalizeDiagnostics preserves stripped_images", async () => {
  const { normalizeDiagnostics } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  const result = normalizeDiagnostics({
    state: "normal",
    selected_body: "html",
    segmentation_path: "html",
    confidence: "high",
    warnings: [],
    stripped_images: [
      { category: "tracking_pixel", reason: "dimension_heuristic", reference: "https://t.example.com/pixel.gif" },
    ],
  });
  assert.equal(result.stripped_images.length, 1);
  assert.equal(result.stripped_images[0].category, "tracking_pixel");
});

test("normalizeDiagnostics defaults stripped_images to empty array", async () => {
  const { normalizeDiagnostics } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  const result = normalizeDiagnostics({
    state: "normal",
    selected_body: "html",
    segmentation_path: "html",
    confidence: "high",
    warnings: [],
  });
  assert.deepEqual(result.stripped_images, []);
});

test("computeGrade returns pass for normal state", async () => {
  const { computeGrade } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  assert.equal(computeGrade({ state: "normal" }, "succeeded"), "pass");
});

test("computeGrade returns review for degraded state", async () => {
  const { computeGrade } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  assert.equal(computeGrade({ state: "degraded" }, "succeeded"), "review");
});

test("computeGrade returns review for review_recommended state", async () => {
  const { computeGrade } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  assert.equal(computeGrade({ state: "review_recommended" }, "succeeded"), "review");
});

test("computeGrade returns fail for failed job status", async () => {
  const { computeGrade } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  assert.equal(computeGrade({ state: "normal" }, "failed"), "fail");
});

test("computeGrade returns null when no diagnostics", async () => {
  const { computeGrade } = await import("../../src/dead_letter/frontend/static/lib/helpers.js");
  assert.equal(computeGrade(null, "succeeded"), null);
});
