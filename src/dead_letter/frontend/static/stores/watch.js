import { firstErrorMessage, isTerminalStatus } from "../lib/helpers.js";

export function registerWatchStore(Alpine) {
  Alpine.store("watch", {
    active: false,
    path: "",
    pathOverride: "",
    stats: { files_detected: 0, jobs_created: 0, failed_events: 0 },
    lastError: null,
    latestJobId: "",
    latestJobStatus: "",
    pollHandle: null,
    sessionId: 0,
    pollInFlight: false,
    pollErrorCount: 0,
    actionInFlight: false,
    activePollController: null,
    activeActionController: null,

    cancelRequests() {
      this.sessionId += 1;
      this.stopPolling();
      if (this.activeActionController) {
        this.activeActionController.abort();
        this.activeActionController = null;
      }
      this.actionInFlight = false;
    },

    applyStatus(payload) {
      this.active = Boolean(payload.active);
      this.path = typeof payload.path === "string" ? payload.path : "";
      this.stats = {
        files_detected: Number(payload.files_detected || 0),
        jobs_created: Number(payload.jobs_created || 0),
        failed_events: Number(payload.failed_events || 0),
      };
      this.lastError = payload.last_error || null;
      this.latestJobId = typeof payload.latest_job_id === "string" ? payload.latest_job_id : "";
      this.latestJobStatus = typeof payload.latest_job_status === "string" ? payload.latest_job_status : "";
      this.maybeSurfaceLatestWatchJob();
    },

    maybeSurfaceLatestWatchJob() {
      const jobStore = Alpine.store("job");
      if (!this.latestJobId || this.latestJobId === jobStore?.id) {
        return;
      }
      if (jobStore?.id && !isTerminalStatus(jobStore.status)) {
        jobStore.pendingWatchJobId = this.latestJobId;
        jobStore.pendingWatchJobStatus = this.latestJobStatus;
        return;
      }
      this.adoptWatchJob(this.latestJobId, this.latestJobStatus);
    },

    adoptWatchJob(jobId = this.latestJobId, initialStatus = this.latestJobStatus) {
      const jobStore = Alpine.store("job");
      if (!jobId || jobId === jobStore?.id) {
        return;
      }
      jobStore?.stopPolling?.();
      if (jobStore?.activeCancelController) {
        jobStore.activeCancelController.abort();
        jobStore.activeCancelController = null;
      }
      jobStore.cancelInFlight = false;
      jobStore?.clearOpMessages?.();
      jobStore.id = jobId;
      jobStore.origin = "watch";
      jobStore.status = typeof initialStatus === "string" ? initialStatus : "";
      jobStore.outputLocation = null;
      jobStore.diagnostics = null;
      jobStore.recoveryActions = [];
      jobStore.diagnosticsOpen = false;
      jobStore.cancelRequested = false;
      jobStore.progress = { total: 0, completed: 0, failed: 0, current: null };
      jobStore.summary = { written: 0, skipped: 0, errors: 0 };
      jobStore.errors = [];
      jobStore.timestamps = { created_at: null, started_at: null, finished_at: null };
      jobStore.pendingWatchJobId = "";
      jobStore.pendingWatchJobStatus = "";
      jobStore?.setOpInfo?.("New watch job detected.");
      jobStore?.startPolling?.();
      if (isTerminalStatus(initialStatus)) {
        Alpine.store("history")?.load?.();
      }
    },

    async toggle(options) {
      if (this.actionInFlight) {
        return;
      }
      if (this.active) {
        await this.stop();
        return;
      }
      await this.start(options);
    },

    async start(options = {}) {
      if (this.actionInFlight) {
        return;
      }
      const jobStore = Alpine.store("job");
      jobStore?.clearOpMessages?.();

      const sessionId = this.sessionId + 1;
      this.sessionId = sessionId;
      this.stopPolling();
      if (this.activeActionController) {
        this.activeActionController.abort();
      }
      const controller = new AbortController();
      this.activeActionController = controller;
      this.actionInFlight = true;
      try {
        const response = await fetch("/api/watch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            path: this.pathOverride.trim(),
            options: {
              ...options,
              delete_eml: Boolean(options.delete_eml && !options.dry_run),
            },
          }),
        });
        if (sessionId !== this.sessionId) {
          return;
        }

        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          if (sessionId !== this.sessionId) {
            return;
          }
          jobStore?.setOpError?.(firstErrorMessage(payload, "Failed to start watch."));
          return;
        }

        const payload = await response.json();
        if (sessionId !== this.sessionId) {
          return;
        }
        this.applyStatus(payload);
        if (this.active) {
          const notice = this.pathOverride.trim()
            ? "Watching the override folder. Imported Inbox files are suppressed to avoid duplicate jobs."
            : "Watching Inbox. Imported Inbox files are suppressed to avoid duplicate jobs.";
          jobStore?.setOpInfo?.(notice);
          this.startPolling(sessionId);
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (sessionId !== this.sessionId) {
          return;
        }
        jobStore?.setOpError?.("Network error while starting watch.");
      } finally {
        if (this.activeActionController === controller) {
          this.activeActionController = null;
        }
        if (sessionId === this.sessionId) {
          this.actionInFlight = false;
        }
      }
    },

    async stop() {
      if (this.actionInFlight) {
        return;
      }
      const jobStore = Alpine.store("job");
      const previousState = {
        active: this.active,
        path: this.path,
        stats: { ...this.stats },
        lastError: this.lastError,
        latestJobId: this.latestJobId,
        latestJobStatus: this.latestJobStatus,
      };
      const sessionId = this.sessionId + 1;
      this.sessionId = sessionId;
      this.stopPolling();
      if (this.activeActionController) {
        this.activeActionController.abort();
      }
      const controller = new AbortController();
      this.activeActionController = controller;
      this.actionInFlight = true;
      try {
        const response = await fetch("/api/watch", { method: "DELETE", signal: controller.signal });
        if (sessionId !== this.sessionId) {
          return;
        }
        if (response.ok) {
          const payload = await response.json();
          if (sessionId !== this.sessionId) {
            return;
          }
          this.applyStatus(payload);
          if (this.active) {
            this.startPolling(sessionId);
          }
        } else {
          const payload = await response.json().catch(() => ({}));
          if (sessionId !== this.sessionId) {
            return;
          }
          this.active = previousState.active;
          this.path = previousState.path;
          this.stats = { ...previousState.stats };
          this.lastError = previousState.lastError;
          this.latestJobId = previousState.latestJobId;
          this.latestJobStatus = previousState.latestJobStatus;
          jobStore?.setOpError?.(firstErrorMessage(payload, "Failed to stop watch. Watch is still active."));
          if (this.active) {
            this.startPolling(sessionId);
          }
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (sessionId !== this.sessionId) {
          return;
        }
        this.active = previousState.active;
        this.path = previousState.path;
        this.stats = { ...previousState.stats };
        this.lastError = previousState.lastError;
        this.latestJobId = previousState.latestJobId;
        this.latestJobStatus = previousState.latestJobStatus;
        jobStore?.setOpError?.("Network error while stopping watch. Watch is still active.");
        if (this.active) {
          this.startPolling(sessionId);
        }
      } finally {
        if (this.activeActionController === controller) {
          this.activeActionController = null;
        }
        if (sessionId === this.sessionId) {
          this.actionInFlight = false;
        }
      }
    },

    startPolling(sessionId = this.sessionId) {
      this.stopPolling();
      this.pollErrorCount = 0;
      this.pollHandle = window.setInterval(() => this.poll(sessionId), 2000);
    },

    stopPolling() {
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

    noteTransientPollFailure(message) {
      this.pollErrorCount += 1;
      const jobStore = Alpine.store("job");
      if (this.pollErrorCount < 3) {
        jobStore?.setOpInfo?.(`${message} Retrying (${this.pollErrorCount}/2).`);
        return;
      }
      jobStore?.setOpError?.("Watch polling stopped after repeated failures. Toggle Watch to retry.");
      this.stopPolling();
    },

    async poll(sessionId = this.sessionId) {
      if (sessionId !== this.sessionId || this.pollInFlight) {
        return;
      }
      this.pollInFlight = true;
      const controller = new AbortController();
      this.activePollController = controller;
      try {
        const response = await fetch("/api/watch", { signal: controller.signal });
        if (sessionId !== this.sessionId) {
          return;
        }
        if (!response.ok) {
          if (response.status >= 500) {
            this.noteTransientPollFailure("Watch status is temporarily unavailable.");
          } else {
            Alpine.store("job")?.setOpError?.("Failed to poll watch status.");
            this.stopPolling();
          }
          return;
        }

        const payload = await response.json();
        if (sessionId !== this.sessionId) {
          return;
        }
        this.pollErrorCount = 0;
        this.applyStatus(payload);

        if (this.active && !this.pollHandle) {
          this.pollHandle = window.setInterval(() => this.poll(sessionId), 2000);
        }
        if (!this.active) {
          this.stopPolling();
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (sessionId !== this.sessionId) {
          return;
        }
        this.noteTransientPollFailure("Watch polling failed due to network interruption.");
      } finally {
        if (this.activePollController === controller) {
          this.activePollController = null;
        }
        if (sessionId === this.sessionId) {
          this.pollInFlight = false;
        }
      }
    },
  });
}
