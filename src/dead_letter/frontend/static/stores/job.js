import {
  firstErrorMessage,
  isTerminalStatus,
  normalizeDiagnostics,
  normalizeErrors,
  normalizeRecoveryActions,
} from "../lib/helpers.js";

const REQUIRED_JOB_KEYS = ["status", "cancel_requested", "output_location", "progress", "summary", "errors"];
const MAX_TRANSIENT_POLL_ERRORS = 3;
const RETRY_START_MESSAGES = Object.freeze({
  retry_with_html_repair: "Retry started with HTML repair enabled.",
  retry_with_html_fallback: "Retry started with plain-text fallback enabled.",
});

export function registerJobStore(Alpine) {
  Alpine.store("job", {
    id: "",
    origin: "",
    status: "",
    outputLocation: null,
    diagnostics: null,
    recoveryActions: [],
    diagnosticsOpen: false,
    reportPath: null,
    cancelRequested: false,
    progress: { total: 0, completed: 0, failed: 0, current: null },
    summary: { written: 0, skipped: 0, errors: 0 },
    errors: [],
    timestamps: { created_at: null, started_at: null, finished_at: null },
    pollHandle: null,
    pollSessionId: 0,
    pollInFlight: false,
    activePollController: null,
    cancelInFlight: false,
    activeCancelController: null,
    pollErrorCount: 0,
    isSubmitting: false,
    opErrors: [],
    opInfo: [],
    pendingWatchJobId: "",
    pendingWatchJobStatus: "",

    applyOutputLocation(outputLocation) {
      if (
        outputLocation &&
        outputLocation.strategy === "cabinet" &&
        typeof outputLocation.cabinet_path === "string"
      ) {
        this.outputLocation = {
          strategy: "cabinet",
          cabinet_path: outputLocation.cabinet_path,
          bundle_path: typeof outputLocation.bundle_path === "string" ? outputLocation.bundle_path : null,
        };
        return;
      }
      this.outputLocation = null;
    },

    clearOpMessages() {
      this.opErrors = [];
      this.opInfo = [];
    },

    setOpError(message) {
      this.opErrors = [message];
    },

    setOpInfo(message) {
      this.opInfo = [message];
    },

    resetState() {
      if (this.activeCancelController) {
        this.activeCancelController.abort();
        this.activeCancelController = null;
      }
      this.cancelInFlight = false;
      this.clearOpMessages();
      this.id = "";
      this.origin = "";
      this.status = "";
      this.outputLocation = null;
      this.diagnostics = null;
      this.recoveryActions = [];
      this.diagnosticsOpen = false;
      this.reportPath = null;
      this.cancelRequested = false;
      this.progress = { total: 0, completed: 0, failed: 0, current: null };
      this.summary = { written: 0, skipped: 0, errors: 0 };
      this.errors = [];
      this.timestamps = { created_at: null, started_at: null, finished_at: null };
      this.pendingWatchJobId = "";
      this.pendingWatchJobStatus = "";
    },

    applyStarted(payload, invalidResponseMessage, origin = "manual") {
      if (!payload || typeof payload.id !== "string" || typeof payload.status !== "string") {
        this.setOpError(invalidResponseMessage);
        return false;
      }

      this.id = payload.id;
      this.origin = origin;
      this.status = payload.status;
      this.applyOutputLocation(payload.output_location);
      this.diagnostics = normalizeDiagnostics(payload.diagnostics);
      this.recoveryActions = normalizeRecoveryActions(payload.recovery_actions);
      this.diagnosticsOpen = false;
      this.startPolling();
      return true;
    },

    noteTransientPollFailure(message) {
      this.pollErrorCount += 1;
      if (this.pollErrorCount < MAX_TRANSIENT_POLL_ERRORS) {
        this.setOpError(`${message} Retrying (${this.pollErrorCount}/${MAX_TRANSIENT_POLL_ERRORS - 1}).`);
        return;
      }
      this.setOpError("Polling stopped after repeated failures. Use Resume Polling to retry.");
      this.stopPolling();
    },

    async start(payload) {
      if (this.isSubmitting) {
        return { ok: false };
      }

      this.clearOpMessages();
      this.stopPolling();
      this.resetState();
      this.isSubmitting = true;

      try {
        const response = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          return await this.handleStartFailure(response);
        }

        const body = await response.json();
        if (this.applyStarted(body, "Backend returned an invalid create-job response.", "manual")) {
          return { ok: true };
        }
        return { ok: false };
      } catch (_err) {
        this.setOpError("Network error while creating job. Retry with the same form values.");
        return { ok: false };
      } finally {
        this.isSubmitting = false;
      }
    },

    async handleStartFailure(response) {
      const payload = await response.json().catch(() => ({}));

      if (response.status === 400) {
        const errors = normalizeErrors(payload);
        return {
          ok: false,
          formErrors: errors.length
            ? errors.map((item) => `${item.path || "request"}: ${item.message}`)
            : ["Invalid request payload."],
        };
      }

      if (response.status === 404 || response.status === 409) {
        this.setOpError(firstErrorMessage(payload, "Operation rejected by backend."));
        return { ok: false };
      }

      this.setOpError(firstErrorMessage(payload, "Backend runtime error while creating job."));
      return { ok: false };
    },

    startPolling() {
      this.stopPolling();
      const sessionId = this.pollSessionId;
      this.pollErrorCount = 0;
      this.poll(sessionId);
      this.pollHandle = window.setInterval(() => this.poll(sessionId), 800);
    },

    resumePolling() {
      if (!this.id || this.pollHandle || isTerminalStatus(this.status)) {
        return;
      }
      this.clearOpMessages();
      this.startPolling();
    },

    stopPolling() {
      this.pollSessionId += 1;
      if (this.pollHandle) {
        window.clearInterval(this.pollHandle);
        this.pollHandle = null;
      }
      if (this.activePollController) {
        this.activePollController.abort();
        this.activePollController = null;
      }
      this.pollInFlight = false;
    },

    async poll(sessionId = this.pollSessionId) {
      if (!this.id || this.pollInFlight || sessionId !== this.pollSessionId) {
        return;
      }

      const requestedJobId = this.id;
      this.pollInFlight = true;
      const controller = new AbortController();
      this.activePollController = controller;

      try {
        const response = await fetch(`/api/jobs/${requestedJobId}`, { signal: controller.signal });
        if (sessionId !== this.pollSessionId || this.id !== requestedJobId) {
          return;
        }

        if (!response.ok) {
          if (response.status === 404 || response.status === 409) {
            const payload = await response.json().catch(() => ({}));
            this.setOpError(firstErrorMessage(payload, "Job no longer available. Start a new run."));
            this.stopPolling();
            return;
          }
          if (response.status >= 500) {
            this.noteTransientPollFailure("Backend is temporarily unavailable.");
          } else {
            this.setOpError("Failed to poll job status.");
            this.stopPolling();
          }
          return;
        }

        const payload = await response.json();
        if (sessionId !== this.pollSessionId || this.id !== requestedJobId) {
          return;
        }

        this.pollErrorCount = 0;
        this.opErrors = [];
        const missing = REQUIRED_JOB_KEYS.filter((key) => !(key in payload));
        if (missing.length) {
          this.setOpError(`Backend response missing required keys: ${missing.join(", ")}`);
          this.stopPolling();
          return;
        }

        this.status = payload.status;
        this.cancelRequested = Boolean(payload.cancel_requested);
        this.applyOutputLocation(payload.output_location);
        this.progress = {
          total: Number(payload.progress.total || 0),
          completed: Number(payload.progress.completed || 0),
          failed: Number(payload.progress.failed || 0),
          current: payload.progress.current || null,
        };
        this.summary = {
          written: Number(payload.summary.written || 0),
          skipped: Number(payload.summary.skipped || 0),
          errors: Number(payload.summary.errors || 0),
        };
        this.errors = Array.isArray(payload.errors) ? payload.errors : [];
        this.timestamps = {
          created_at: payload.created_at || null,
          started_at: payload.started_at || null,
          finished_at: payload.finished_at || null,
        };
        this.diagnostics = normalizeDiagnostics(payload.diagnostics);
        this.reportPath = typeof payload.report_path === "string" ? payload.report_path : null;
        this.recoveryActions = normalizeRecoveryActions(payload.recovery_actions);
        if (!this.diagnostics) {
          this.diagnosticsOpen = false;
        }

        if (isTerminalStatus(this.status)) {
          this.stopPolling();
          Alpine.store("history")?.load?.();
          if (this.pendingWatchJobId && this.pendingWatchJobId !== this.id) {
            Alpine.store("watch")?.adoptWatchJob?.(this.pendingWatchJobId, this.pendingWatchJobStatus);
          }
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (sessionId !== this.pollSessionId || this.id !== requestedJobId) {
          return;
        }
        this.noteTransientPollFailure("Polling failed due to network interruption.");
      } finally {
        if (this.activePollController === controller) {
          this.activePollController = null;
        }
        if (sessionId === this.pollSessionId) {
          this.pollInFlight = false;
        }
      }
    },

    async importFile(file, options) {
      if (!file || this.isSubmitting) {
        return null;
      }

      this.clearOpMessages();
      this.stopPolling();
      this.resetState();
      this.isSubmitting = true;

      const formData = new FormData();
      formData.append("file", file);
      formData.append(
        "options",
        JSON.stringify({
          ...options,
          delete_eml: Boolean(options.delete_eml && !options.dry_run),
        })
      );

      try {
        const response = await fetch("/api/import", { method: "POST", body: formData });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          this.setOpError(firstErrorMessage(payload, "Import failed."));
          return null;
        }

        const payload = await response.json();
        const importedPath = typeof payload.imported_path === "string" ? payload.imported_path : "";
        if (!this.applyStarted(payload, "Backend returned an invalid import response.", "import")) {
          return null;
        }
        return { imported_path: importedPath };
      } catch (_err) {
        this.setOpError("Network error while importing file.");
        return null;
      } finally {
        this.isSubmitting = false;
      }
    },

    async importBatch(files, options) {
      if (!files || !files.length || this.isSubmitting) {
        return null;
      }

      this.clearOpMessages();
      this.stopPolling();
      this.resetState();
      this.isSubmitting = true;

      const formData = new FormData();
      for (const file of files) {
        formData.append("files", file);
      }
      formData.append(
        "options",
        JSON.stringify({
          ...options,
          delete_eml: Boolean(options.delete_eml && !options.dry_run),
        })
      );

      try {
        const response = await fetch("/api/import-batch", { method: "POST", body: formData });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          this.setOpError(firstErrorMessage(payload, "Batch import failed."));
          return null;
        }

        const payload = await response.json();
        if (!Array.isArray(payload.imported_paths)) {
          this.setOpError("Backend returned an invalid batch import response.");
          return null;
        }
        if (!this.applyStarted(payload, "Backend returned an invalid batch import response.", "import")) {
          return null;
        }
        return { imported_paths: payload.imported_paths };
      } catch (_err) {
        this.setOpError("Network error while importing batch.");
        return null;
      } finally {
        this.isSubmitting = false;
      }
    },

    async retry(action) {
      if (!this.id || this.isSubmitting || !isTerminalStatus(this.status)) {
        return;
      }
      if (!this.recoveryActions.some((item) => item.kind === action)) {
        return;
      }

      const requestedJobId = this.id;
      const preservedOrigin = this.origin || "manual";
      this.clearOpMessages();
      this.stopPolling();
      this.isSubmitting = true;

      try {
        const response = await fetch(`/api/jobs/${requestedJobId}/retry`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          this.setOpError(firstErrorMessage(payload, "Retry failed."));
          return;
        }

        const payload = await response.json();
        this.resetState();
        this.applyStarted(payload, "Backend returned an invalid retry response.", preservedOrigin);
        this.setOpInfo(RETRY_START_MESSAGES[action] || "Retry started.");
      } catch (_err) {
        this.setOpError("Network error while retrying job.");
      } finally {
        this.isSubmitting = false;
      }
    },

    async cancel() {
      if (!this.id || this.cancelInFlight) {
        return;
      }

      const requestedJobId = this.id;
      this.cancelInFlight = true;
      const controller = new AbortController();
      this.activeCancelController = controller;

      try {
        const response = await fetch(`/api/jobs/${requestedJobId}/cancel`, {
          method: "POST",
          signal: controller.signal,
        });
        if (this.id !== requestedJobId) {
          return;
        }

        if (response.status === 409) {
          const payload = await response.json().catch(() => ({}));
          this.setOpError(firstErrorMessage(payload, "Job is already terminal and cannot be cancelled."));
          return;
        }

        if (response.status === 404) {
          const payload = await response.json().catch(() => ({}));
          this.setOpError(firstErrorMessage(payload, "Job ID not found. Start a new run."));
          this.stopPolling();
          return;
        }

        if (!response.ok) {
          this.setOpError("Cancel request failed.");
          return;
        }

        const payload = await response.json();
        if (this.id !== requestedJobId) {
          return;
        }

        this.status = payload.status;
        if (payload.accepted && !isTerminalStatus(this.status)) {
          this.cancelRequested = true;
          this.setOpInfo("Cancellation requested. In-progress files may still complete before the job becomes cancelled.");
        }
        if (isTerminalStatus(this.status)) {
          this.stopPolling();
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (this.id !== requestedJobId) {
          return;
        }
        this.setOpError("Network error while cancelling job.");
      } finally {
        if (this.activeCancelController === controller) {
          this.activeCancelController = null;
        }
        this.cancelInFlight = false;
      }
    },
  });
}
