import test from "node:test";
import assert from "node:assert/strict";

import {
  OPTION_ENUM_VALIDATORS,
  applyStoredOptions,
} from "../../src/dead_letter/frontend/static/lib/helpers.js";


const DEFAULTS = {
  strip_signatures: false,
  thread_mode: "latest",
  thread_order: "oldest-first",
};


test("applyStoredOptions accepts valid enum value", () => {
  const out = applyStoredOptions({ thread_mode: "structured" }, DEFAULTS);
  assert.equal(out.thread_mode, "structured");
});


test("applyStoredOptions rejects unknown enum value", () => {
  const out = applyStoredOptions({ thread_mode: "garbage" }, DEFAULTS);
  assert.equal(out.thread_mode, "latest");
});


test("applyStoredOptions rejects non-string enum value", () => {
  const out = applyStoredOptions({ thread_mode: 42 }, DEFAULTS);
  assert.equal(out.thread_mode, "latest");
});


test("applyStoredOptions accepts thread_order latest-first", () => {
  const out = applyStoredOptions({ thread_order: "latest-first" }, DEFAULTS);
  assert.equal(out.thread_order, "latest-first");
});


test("applyStoredOptions rejects unknown thread_order", () => {
  const out = applyStoredOptions({ thread_order: "middle" }, DEFAULTS);
  assert.equal(out.thread_order, "oldest-first");
});


test("applyStoredOptions still merges booleans for non-enum fields", () => {
  const out = applyStoredOptions({ strip_signatures: true }, DEFAULTS);
  assert.equal(out.strip_signatures, true);
});


test("applyStoredOptions ignores non-boolean for boolean field", () => {
  const out = applyStoredOptions({ strip_signatures: "yes" }, DEFAULTS);
  assert.equal(out.strip_signatures, false);
});


test("applyStoredOptions returns a fresh object (no mutation)", () => {
  const defaults = { ...DEFAULTS };
  const out = applyStoredOptions({ thread_mode: "structured" }, defaults);
  assert.notStrictEqual(out, defaults);
  assert.equal(defaults.thread_mode, "latest");
});


test("applyStoredOptions tolerates null / non-object input", () => {
  assert.deepEqual(applyStoredOptions(null, DEFAULTS), DEFAULTS);
  assert.deepEqual(applyStoredOptions("garbage", DEFAULTS), DEFAULTS);
  assert.deepEqual(applyStoredOptions(42, DEFAULTS), DEFAULTS);
});


test("OPTION_ENUM_VALIDATORS export covers exactly the expected enum fields", () => {
  assert.deepEqual(Object.keys(OPTION_ENUM_VALIDATORS).sort(), ["thread_mode", "thread_order"]);
});
