import { firstErrorMessage, normalizeErrors } from "../lib/helpers.js";

const DEFAULT_SETTINGS = Object.freeze({
  inbox_path: "~/Documents/dead-letter/Inbox",
  cabinet_path: "~/Documents/dead-letter/Cabinet",
});

function cloneDefaults() {
  return {
    inbox_path: DEFAULT_SETTINGS.inbox_path,
    cabinet_path: DEFAULT_SETTINGS.cabinet_path,
  };
}

export function registerSettingsStore(Alpine) {
  Alpine.store("settings", {
    configured: false,
    loading: false,
    saving: false,
    form: cloneDefaults(),
    errors: [],

    applyResponse(payload) {
      if (
        payload &&
        payload.configured === true &&
        typeof payload.inbox_path === "string" &&
        typeof payload.cabinet_path === "string"
      ) {
        this.configured = true;
        this.form = {
          inbox_path: payload.inbox_path,
          cabinet_path: payload.cabinet_path,
        };
        this.errors = [];
        return;
      }

      this.configured = false;
      this.form = cloneDefaults();
    },

    async load() {
      this.loading = true;
      this.errors = [];

      try {
        const response = await fetch("/api/settings");
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          this.errors = [firstErrorMessage(payload, "Unable to load workflow settings.")];
          this.configured = false;
          return;
        }

        const payload = await response.json();
        this.applyResponse(payload);
      } catch (_err) {
        this.errors = ["Network error while loading workflow settings."];
        this.configured = false;
      } finally {
        this.loading = false;
      }
    },

    async save() {
      if (this.saving) {
        return;
      }

      const jobStore = Alpine.store("job");
      const watchStore = Alpine.store("watch");
      jobStore?.clearOpMessages?.();
      this.errors = [];
      this.saving = true;

      try {
        const response = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            inbox_path: this.form.inbox_path.trim(),
            cabinet_path: this.form.cabinet_path.trim(),
          }),
        });
        const payload = await response.json().catch(() => ({}));

        if (!response.ok) {
          const errors = normalizeErrors(payload);
          this.errors = errors.length
            ? errors.map((item) => `${item.path || "request"}: ${item.message}`)
            : [firstErrorMessage(payload, "Unable to save workflow settings.")];
          return;
        }

        this.applyResponse(payload);
        if (watchStore?.active && !watchStore.pathOverride.trim() && watchStore.path !== payload.inbox_path) {
          jobStore?.setOpInfo?.("Workflow folders saved. Restart watch to switch to the new Inbox path.");
        } else {
          jobStore?.setOpInfo?.("Workflow folders saved.");
        }
      } catch (_err) {
        this.errors = ["Network error while saving workflow settings."];
      } finally {
        this.saving = false;
      }
    },
  });
}
