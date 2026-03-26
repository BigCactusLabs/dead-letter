const TERMINAL_STATUSES = new Set([
  "succeeded",
  "completed_with_errors",
  "failed",
  "cancelled",
]);

export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(status);
}

export function normalizeErrors(payload) {
  if (!payload) return [];
  if (Array.isArray(payload.errors)) return payload.errors;
  if (payload.detail && Array.isArray(payload.detail.errors)) return payload.detail.errors;
  return [];
}

export function firstErrorMessage(payload, fallback) {
  const errors = normalizeErrors(payload);
  if (!errors.length) return fallback;
  const first = errors[0];
  return first.path ? `${first.path}: ${first.message}` : first.message;
}

export function formatErrorItem(item) {
  if (!item) return "";
  return item.path ? `${item.path}: ${item.message}` : item.message;
}

export function normalizeDiagnostics(payload) {
  if (!payload || typeof payload !== "object") return null;
  if (
    typeof payload.state !== "string" ||
    typeof payload.selected_body !== "string" ||
    typeof payload.segmentation_path !== "string" ||
    typeof payload.confidence !== "string"
  ) {
    return null;
  }
  return {
    state: payload.state,
    selected_body: payload.selected_body,
    segmentation_path: payload.segmentation_path,
    client_hint: typeof payload.client_hint === "string" ? payload.client_hint : null,
    confidence: payload.confidence,
    fallback_used: typeof payload.fallback_used === "string" ? payload.fallback_used : null,
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    stripped_images: Array.isArray(payload.stripped_images) ? payload.stripped_images : [],
  };
}

export function computeGrade(diagnostics, jobStatus) {
  if (jobStatus === "failed") return "fail";
  if (!diagnostics) return null;
  if (diagnostics.state === "normal") return "pass";
  if (diagnostics.state === "degraded" || diagnostics.state === "review_recommended") return "review";
  return null;
}

export function normalizeRecoveryActions(payload) {
  if (!Array.isArray(payload)) return [];
  return payload.filter(
    (item) =>
      item &&
      typeof item === "object" &&
      typeof item.kind === "string" &&
      typeof item.label === "string" &&
      typeof item.message === "string"
  );
}

export function buildPayload(mode, inputPath, options) {
  return {
    mode,
    input_path: inputPath.trim(),
    options: {
      ...options,
      delete_eml: Boolean(options.delete_eml && !options.dry_run),
    },
  };
}

export function validateForm({ mode, inputPath }) {
  const errors = [];
  if (!inputPath.trim()) errors.push("Input path is required.");
  if (mode !== "file" && mode !== "directory") errors.push("Mode must be file or directory.");
  return errors;
}

export function relativeTime(isoString) {
  if (!isoString) return "";
  const then = new Date(isoString);
  const now = Date.now();
  const diffMs = now - then.getTime();
  if (Number.isNaN(diffMs) || diffMs < 0) return "";
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function jobDisplayName(outputLocation) {
  if (!outputLocation) return "\u2014";
  const path = outputLocation.bundle_path || outputLocation.cabinet_path || "";
  const parts = path.replace(/\\/g, "/").split("/");
  const basename = parts[parts.length - 1] || "\u2014";
  const dot = basename.lastIndexOf(".");
  return dot > 0 ? basename.substring(0, dot) : basename;
}

export function jobStatusColor(status) {
  if (status === "succeeded") return "ok";
  if (status === "completed_with_errors") return "warn";
  return "err";
}
