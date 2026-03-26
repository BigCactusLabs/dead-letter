import Alpine from "/static/vendor/alpine.esm.js";
import { buildPayload, computeGrade, firstErrorMessage, isTerminalStatus, jobDisplayName, jobStatusColor, relativeTime, validateForm } from "/static/lib/helpers.js";
import { registerJobStore } from "/static/stores/job.js";
import { registerSettingsStore } from "/static/stores/settings.js";
import { registerWatchStore } from "/static/stores/watch.js";
import { registerHistoryStore } from "/static/stores/history.js";

registerSettingsStore(Alpine);
registerJobStore(Alpine);
registerWatchStore(Alpine);
registerHistoryStore(Alpine);

const SIZE_WARNING_BYTES = 100 * 1024 * 1024;

Alpine.data("deadLetterApp", () => ({
  mode: "file",
  inputPath: "",
  options: {
    strip_signatures: false,
    strip_disclaimers: false,
    strip_quoted_headers: false,
    strip_signature_images: false,
    strip_tracking_pixels: false,
    embed_inline_images: false,
    include_all_headers: false,
    include_raw_html: false,
    no_calendar_summary: false,
    allow_fallback_on_html_error: false,
    delete_eml: false,
    dry_run: false,
    report: false,
  },
  settingsOpen: false,
  dragActive: false,
  dragDepth: 0,
  dragItemCount: 0,
  errorsExpanded: false,
  liveAnnouncement: "",
  formErrors: [],
  dropFeedback: "",
  batchConfirm: { show: false, emlFiles: [], skipped: [], totalBytes: 0 },
  setupBannerDismissed: false,
  _savedOptions: null,

  init() {
    this.applyDryRunSafety();
    this.$store.settings.load();
    this.$store.watch.poll();
    this.$store.history.load();

    this.$watch("workspaceState", (state) => {
      const job = this.$store.job;
      if (state === "converting") {
        this.liveAnnouncement = `Converting 0 of ${job.progress.total || 0}`;
        this.errorsExpanded = false;
        job.diagnosticsOpen = false;
      } else if (state === "done") {
        this.liveAnnouncement = `Complete - ${job.summary.written} written`;
        this.errorsExpanded = job.summary.errors > 0 || job.errors.length > 0 || job.opInfo.length > 0;
        // Auto-expand diagnostics for Review grade
        if (this.conversionGrade === "review") {
          job.diagnosticsOpen = true;
        }
      } else if (state === "settings") {
        this.liveAnnouncement = "Settings opened";
      } else {
        this.liveAnnouncement = "";
      }
    });

    this.$watch("$store.job.progress", (progress) => {
      if (this.workspaceState !== "converting") {
        return;
      }
      const done = Number(progress?.completed || 0) + Number(progress?.failed || 0);
      const total = Number(progress?.total || 0);
      const msg = `Converting ${done} of ${total}`;
      if (msg !== this.liveAnnouncement) this.liveAnnouncement = msg;
    });
  },

  destroy() {
    const job = this.$store.job;
    this.$store.job.stopPolling();
    this.$store.watch.cancelRequests();
    if (job.activeCancelController) {
      job.activeCancelController.abort();
      job.activeCancelController = null;
    }
    this.dragActive = false;
    this.dragDepth = 0;
    this.dragItemCount = 0;
  },

  get progressPercent() {
    const progress = this.$store.job.progress;
    const total = Math.max(progress.total || 0, 1);
    const done = Math.min(total, (progress.completed || 0) + (progress.failed || 0));
    return done / total;
  },

  get workspaceState() {
    const job = this.$store.job;
    if (this.settingsOpen) return "settings";
    if (job.isSubmitting || (job.id && !isTerminalStatus(job.status))) return "converting";
    if (job.id && isTerminalStatus(job.status)) return "done";
    return "idle";
  },

  get progressCountLabel() {
    const progress = this.$store.job.progress;
    const done = (progress.completed || 0) + (progress.failed || 0);
    return `${done} / ${progress.total || 0}`;
  },

  get inboxStatLabel() {
    const progress = this.$store.job.progress;
    if (this.workspaceState === "converting") {
      const remaining = Math.max(0, (progress.total || 0) - (progress.completed || 0) - (progress.failed || 0));
      return `${remaining} files`;
    }
    return "-";
  },

  get cabinetStatLabel() {
    if (this.workspaceState === "converting") {
      return `${this.$store.job.summary.written} bundles`;
    }
    if (this.workspaceState === "done") {
      const total = this.$store.history.totals.total_written;
      return total > 0 ? `${total} bundles` : "-";
    }
    return "-";
  },

  get doneWrittenCount() {
    const hist = this.$store.history.totals;
    if (hist.jobs_completed > 0) return hist.total_written;
    return this.$store.job.summary.written;
  },

  get doneSkippedCount() {
    const hist = this.$store.history.totals;
    if (hist.jobs_completed > 0) return hist.total_skipped;
    return this.$store.job.summary.skipped;
  },

  get doneErrorsCount() {
    const hist = this.$store.history.totals;
    if (hist.jobs_completed > 0) return hist.total_errors;
    return this.$store.job.summary.errors;
  },

  get lastJobName() {
    const outputLocation = this.$store.job.outputLocation;
    return jobDisplayName(outputLocation);
  },

  get lastJobColor() {
    return jobStatusColor(this.$store.job.status);
  },

  get outputCabinetPath() {
    const outputLocation = this.$store.job.outputLocation;
    if (!outputLocation) return "Cabinet bundle assigned when job starts";
    return outputLocation.cabinet_path;
  },

  get doneTitle() {
    const status = this.$store.job.status;
    if (status === "failed") return "Failed";
    if (status === "cancelled") return "Cancelled";
    if (status === "completed_with_errors") return "Complete";
    return "Complete";
  },

  get doneStateClass() {
    const status = this.$store.job.status;
    if (status === "failed") return "failed";
    if (status === "completed_with_errors") return "with-errors";
    return "";
  },

  get doneDuration() {
    const timestamps = this.$store.job.timestamps;
    if (!timestamps.started_at || !timestamps.finished_at) return "";
    const start = new Date(timestamps.started_at);
    const end = new Date(timestamps.finished_at);
    const ms = end - start;
    if (Number.isNaN(ms)) return "";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  },

  get inboxPathLabel() {
    const settings = this.$store.settings;
    return settings.configured ? settings.form.inbox_path : "Not configured";
  },

  get cabinetPathLabel() {
    const settings = this.$store.settings;
    return settings.configured ? settings.form.cabinet_path : "Not configured";
  },

  get isCurrentFileJob() {
    const outputLocation = this.$store.job.outputLocation;
    return Boolean(outputLocation && outputLocation.bundle_path);
  },

  get showJobDiagnostics() {
    return this.isCurrentFileJob && Boolean(this.$store.job.diagnostics);
  },

  get diagnosticsOrigin() {
    return this.$store.job.origin === "watch" ? "watch" : null;
  },

  get diagnosticsStateLabel() {
    const diagnostics = this.$store.job.diagnostics;
    if (!diagnostics) {
      return "Unavailable";
    }
    return diagnostics.state.replace(/_/g, " ");
  },

  get diagnosticsStateClass() {
    const diagnostics = this.$store.job.diagnostics;
    if (!diagnostics) {
      return "neutral";
    }
    if (diagnostics.state === "normal") {
      return "ok";
    }
    if (diagnostics.state === "degraded") {
      return "warn";
    }
    return "err";
  },

  get diagnosticsStateMessage() {
    const diagnostics = this.$store.job.diagnostics;
    if (!diagnostics) {
      return "";
    }
    if (diagnostics.state === "normal") {
      return "Conversion quality checks completed without review flags.";
    }
    if (diagnostics.state === "degraded") {
      return "Conversion succeeded with recoverable quality warnings.";
    }
    return "Review output before relying on this conversion.";
  },

  get conversionGrade() {
    return computeGrade(this.$store.job.diagnostics, this.$store.job.status);
  },

  get gradeLabel() {
    const grade = this.conversionGrade;
    if (grade === "pass") return "Pass";
    if (grade === "review") return "Review";
    if (grade === "fail") return "Fail";
    return "";
  },

  get gradeClass() {
    const grade = this.conversionGrade;
    if (grade === "pass") return "grade-pass";
    if (grade === "review") return "grade-review";
    if (grade === "fail") return "grade-fail";
    return "";
  },

  get strippedImagesSummary() {
    const diag = this.$store.job.diagnostics;
    if (!diag || !diag.stripped_images || !diag.stripped_images.length) return "";
    const sigs = diag.stripped_images.filter(i => i.category === "signature_image").length;
    const pixels = diag.stripped_images.filter(i => i.category === "tracking_pixel").length;
    const parts = [];
    if (sigs) parts.push(`${sigs} signature image${sigs !== 1 ? "s" : ""}`);
    if (pixels) parts.push(`${pixels} tracking pixel${pixels !== 1 ? "s" : ""}`);
    return parts.length ? parts.join(", ") + " stripped" : "";
  },

  get watchTargetLabel() {
    const watch = this.$store.watch;
    if (watch.active && watch.path) {
      return watch.path;
    }
    if (watch.pathOverride.trim()) {
      return watch.pathOverride.trim();
    }
    if (this.$store.settings.configured) {
      return this.$store.settings.form.inbox_path;
    }
    return "Inbox not configured";
  },

  get watchTargetHint() {
    const watch = this.$store.watch;
    if (watch.active) {
      return "Watcher is active.";
    }
    if (watch.pathOverride.trim()) {
      return "Advanced override. Leave blank to watch Inbox.";
    }
    if (this.$store.settings.configured) {
      return "Default watch target uses the saved Inbox path.";
    }
    return "Save workflow folders to enable watch mode.";
  },

  get watchButtonLabel() {
    const watch = this.$store.watch;
    if (watch.active) {
      return "Stop Watch";
    }
    return watch.pathOverride.trim() ? "Watch Override Folder" : "Watch Inbox";
  },

  get watchCardAriaLabel() {
    const watch = this.$store.watch;
    if (watch.actionInFlight) {
      return "Watch action in progress";
    }
    return watch.active ? "Stop watch" : "Start watch";
  },

  applyDryRunSafety() {
    if (this.options.dry_run) {
      this.options.delete_eml = false;
    }
  },

  get stripJunkState() {
    const keys = ["strip_signatures", "strip_disclaimers", "strip_quoted_headers", "strip_signature_images", "strip_tracking_pixels"];
    const count = keys.filter((k) => this.options[k]).length;
    if (count === keys.length) return "all";
    if (count > 0) return "some";
    return "none";
  },

  get verboseOutputState() {
    const keys = ["include_all_headers", "include_raw_html", "embed_inline_images"];
    const count = keys.filter((k) => this.options[k]).length;
    if (count === keys.length) return "all";
    if (count > 0) return "some";
    return "none";
  },

  toggleStripJunk() {
    const on = this.stripJunkState !== "all";
    this.options.strip_signatures = on;
    this.options.strip_disclaimers = on;
    this.options.strip_quoted_headers = on;
    this.options.strip_signature_images = on;
    this.options.strip_tracking_pixels = on;
  },

  toggleVerboseOutput() {
    const on = this.verboseOutputState !== "all";
    this.options.include_all_headers = on;
    this.options.include_raw_html = on;
    this.options.embed_inline_images = on;
  },

  isTerminalStatus(status) {
    return isTerminalStatus(status);
  },

  ensureSettingsConfigured(message) {
    const settings = this.$store.settings;
    if (settings.loading) {
      this.$store.job.setOpError("Workflow settings are still loading.");
      return false;
    }
    if (!settings.configured) {
      this.$store.job.setOpError(message);
      return false;
    }
    return true;
  },

  async submitJob(overrides = null) {
    this.formErrors = [];
    this.$store.job.clearOpMessages();
    if (!this.ensureSettingsConfigured("Save Inbox and Cabinet before starting manual jobs.")) return;
    const errors = validateForm({ mode: this.mode, inputPath: this.inputPath });
    if (errors.length) {
      this.formErrors = errors;
      return;
    }
    this.applyDryRunSafety();
    const payload = buildPayload(this.mode, this.inputPath, { ...this.options, ...(overrides || {}) });
    this.settingsOpen = false;
    const result = await this.$store.job.start(payload);
    if (result?.formErrors) {
      this.formErrors = result.formErrors;
    }
  },

  async submitImport(file) {
    this.$store.job.clearOpMessages();
    if (!this.ensureSettingsConfigured("Save Inbox and Cabinet before importing mail.")) return;
    if (!file.name.toLowerCase().endsWith(".eml")) {
      this.$store.job.setOpError("Only .eml files can be imported.");
      return;
    }
    this.applyDryRunSafety();
    const options = { ...this.options, delete_eml: Boolean(this.options.delete_eml && !this.options.dry_run) };
    const result = await this.$store.job.importFile(file, options);
    if (result && result.imported_path !== undefined) {
      this.inputPath = result.imported_path;
      this.mode = "file";
    }
  },

  async submitBatchImport(files) {
    this.$store.job.clearOpMessages();
    if (!this.ensureSettingsConfigured("Save Inbox and Cabinet before importing mail.")) return;
    this.applyDryRunSafety();
    const options = { ...this.options, delete_eml: Boolean(this.options.delete_eml && !this.options.dry_run) };
    await this.$store.job.importBatch(files, options);
  },

  processDrop(files) {
    if (!files.length) return;

    const emlFiles = files.filter((file) => file.name.toLowerCase().endsWith(".eml"));
    const skipped = files.filter((file) => !file.name.toLowerCase().endsWith(".eml")).map((file) => file.name);

    if (!emlFiles.length) {
      this.$store.job.setOpError("No .eml files found.");
      this.setDropFeedback("rejected");
      return;
    }

    const totalBytes = emlFiles.reduce((sum, file) => sum + (file.size || 0), 0);
    if (skipped.length > 0 || totalBytes > SIZE_WARNING_BYTES) {
      this.batchConfirm = { show: true, emlFiles, skipped, totalBytes };
      return;
    }

    this.setDropFeedback("accepted");
    if (emlFiles.length === 1) {
      this.submitImport(emlFiles[0]);
      return;
    }
    this.submitBatchImport(emlFiles);
  },

  confirmBatch() {
    const { emlFiles } = this.batchConfirm;
    this.batchConfirm = { show: false, emlFiles: [], skipped: [], totalBytes: 0 };
    this.setDropFeedback("accepted");
    if (emlFiles.length === 1) {
      this.submitImport(emlFiles[0]);
      return;
    }
    this.submitBatchImport(emlFiles);
  },

  cancelBatch() {
    this.batchConfirm = { show: false, emlFiles: [], skipped: [], totalBytes: 0 };
  },

  setDropFeedback(value) {
    this.dropFeedback = value;
    window.setTimeout(() => {
      if (this.dropFeedback === value) {
        this.dropFeedback = "";
      }
    }, 400);
  },

  async handleFileInput(event) {
    const files = Array.from(event.target?.files || []);
    if (event.target) {
      event.target.value = "";
    }
    if (!files.length) return;
    this.processDrop(files);
  },

  async handleDrop(event) {
    event.preventDefault();
    this.dragDepth = 0;
    this.dragActive = false;
    this.dragItemCount = 0;
    if (this.workspaceState === "converting" || this.workspaceState === "settings") return;
    const files = Array.from(event.dataTransfer?.files || []);
    if (!files.length) return;
    this.processDrop(files);
  },

  handleDragEnter(event) {
    event.preventDefault();
    if (this.workspaceState !== "idle" && this.workspaceState !== "done") return;
    this.dragDepth += 1;
    this.dragActive = true;
    this.dragItemCount = event.dataTransfer?.items?.length || 0;
  },

  handleDragOver(event) {
    event.preventDefault();
  },

  handleDragLeave(event) {
    event.preventDefault();
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) {
      this.dragActive = false;
      this.dragItemCount = 0;
    }
  },

  async handleWatchToggle() {
    if (this.$store.watch.active) {
      await this.$store.watch.stop();
      return;
    }

    this.$store.job.clearOpMessages();
    if (!this.ensureSettingsConfigured("Save Inbox and Cabinet before starting watch.")) return;
    this.applyDryRunSafety();
    const options = { ...this.options, delete_eml: Boolean(this.options.delete_eml && !this.options.dry_run) };
    await this.$store.watch.start(options);
  },

  async openCabinet() {
    if (!this.$store.settings.configured) return;
    try {
      const response = await fetch("/api/open-folder", { method: "POST" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        this.$store.job.setOpError(firstErrorMessage(payload, "Failed to open Cabinet folder."));
      }
    } catch (_err) {
      // Network error — not critical
    }
  },

  toggleSettings() {
    if (!this.settingsOpen) {
      this._savedOptions = { ...this.options };
    }
    this.settingsOpen = !this.settingsOpen;
  },

  get settingsDirty() {
    if (this.$store.settings.hasDirtyPaths()) return true;
    if (!this._savedOptions) return false;
    return Object.keys(this.options).some(
      (key) => this.options[key] !== this._savedOptions[key]
    );
  },

  handleEscape() {
    if (this.$store.settings.showSetupModal) {
      this.$store.settings.dismissSetup();
      return;
    }
    if (this.batchConfirm.show) {
      this.cancelBatch();
      return;
    }
    if (this.settingsOpen) {
      this.settingsOpen = false;
    }
  },

  jobDisplayName(outputLocation) {
    return jobDisplayName(outputLocation);
  },

  relativeTime(isoString) {
    return relativeTime(isoString);
  },
}));

Alpine.start();
