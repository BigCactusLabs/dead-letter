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

const MBOX_OPTION_KEYS = [
  "strip_signatures", "strip_disclaimers", "strip_quoted_headers",
  "strip_signature_images", "strip_tracking_pixels", "embed_inline_images",
  "include_all_headers", "include_raw_html", "no_calendar_summary",
  "allow_fallback_on_html_error", "allow_html_repair_on_panic",
  "thread_mode", "thread_order",
];

export function buildMboxPayload(inputPath, options, timeoutSeconds = 30) {
  const selected = Object.fromEntries(
    MBOX_OPTION_KEYS.filter((key) => key in options).map((key) => [key, options[key]])
  );
  return {
    input_path: inputPath.trim().replace(/\\/g, "/"),
    options: { ...selected, timeout_seconds: timeoutSeconds },
  };
}

export function validateMboxForm({ inputPath, timeoutSeconds = 30 }) {
  const path = inputPath.trim().replace(/\\/g, "/");
  const errors = [];
  if (!path) errors.push("MBOX path is required.");
  else if (!path.toLowerCase().endsWith(".mbox")) errors.push("Choose a single uncompressed .mbox file.");
  if (/^(\/|[a-z]:|~\/)/i.test(path) || path.split("/").includes("..")) {
    errors.push("Use a path within the file browser root, such as Downloads/Takeout.mbox.");
  }
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0 || timeoutSeconds > 300) {
    errors.push("The per-message time limit must be greater than 0 and at most 300 seconds.");
  }
  return errors;
}

function formatBytes(value) {
  if (value < 1024) return value + " B";
  const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return (value / 1024 ** unit).toFixed(1) + " " + ["B", "KiB", "MiB", "GiB"][unit];
}

export function jobProgressView(job) {
  if (job.kind === "mbox") {
    const total = Math.max(0, Number(job.mbox?.bytes_total || 0));
    const value = Math.min(total, Math.max(0, Number(job.mbox?.bytes_processed || 0)));
    const records = Number(job.mbox?.records_processed || 0);
    return {
      value, max: total || 1, fraction: total ? value / total : 0,
      label: records + (records === 1 ? " message processed" : " messages processed"),
      detail: job.mbox ? formatBytes(value) + " of " + formatBytes(total) + " archive bytes processed"
        : "Waiting for archive metadata",
    };
  }
  const progress = job.progress || {};
  const total = Math.max(0, Number(progress.total || 0));
  const done = Number(progress.completed || 0) + Number(progress.failed || 0);
  const value = Math.min(total, done);
  return { value, max: total || 1, fraction: value / (total || 1), label: done + " / " + total, detail: "" };
}

export const OPTION_ENUM_VALIDATORS = {
  thread_mode: (v) => v === "latest" || v === "structured",
  thread_order: (v) => v === "oldest-first" || v === "latest-first",
};

export function applyStoredOptions(saved, currentOptions) {
  const result = { ...currentOptions };
  if (!saved || typeof saved !== "object") return result;
  for (const key of Object.keys(result)) {
    const validator = OPTION_ENUM_VALIDATORS[key];
    if (validator) {
      if (typeof saved[key] === "string" && validator(saved[key])) {
        result[key] = saved[key];
      }
      continue;
    }
    if (typeof saved[key] === "boolean") {
      result[key] = saved[key];
    }
  }
  return result;
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
  if (Number.isNaN(diffMs)) return "";
  if (diffMs < -30000) return "";
  const seconds = Math.max(0, Math.floor(diffMs / 1000));
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
  const path = outputLocation.job_path || outputLocation.bundle_path || outputLocation.cabinet_path || "";
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
