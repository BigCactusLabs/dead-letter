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
  validateForm,
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

test("validateForm returns errors for missing fields", () => {
  assert.deepEqual(validateForm({ mode: "file", inputPath: "" }), ["Input path is required."]);
  assert.deepEqual(validateForm({ mode: "bad", inputPath: "/tmp/x" }), [
    "Mode must be file or directory.",
  ]);
  assert.deepEqual(validateForm({ mode: "file", inputPath: "/tmp/x" }), []);
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
