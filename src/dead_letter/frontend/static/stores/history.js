export function registerHistoryStore(Alpine) {
  Alpine.store("history", {
    jobs: [],
    totals: {
      jobs_completed: 0,
      total_written: 0,
      total_skipped: 0,
      total_errors: 0,
    },
    loading: false,
    open: false,

    async load() {
      if (this.loading) return;
      this.loading = true;
      try {
        const response = await window.fetch("/api/jobs/history");
        if (!response.ok) return;
        const payload = await response.json();
        if (Array.isArray(payload.jobs)) {
          const expandedIds = new Set(this.jobs.filter(j => j._expanded).map(j => j.id));
          this.jobs = payload.jobs.map((j) => ({ ...j, _expanded: expandedIds.has(j.id) }));
        }
        if (payload.totals && typeof payload.totals === "object") {
          this.totals = {
            jobs_completed: Number(payload.totals.jobs_completed || 0),
            total_written: Number(payload.totals.total_written || 0),
            total_skipped: Number(payload.totals.total_skipped || 0),
            total_errors: Number(payload.totals.total_errors || 0),
          };
        }
      } catch (_err) {
        // Silent — history is supplementary
      } finally {
        this.loading = false;
      }
    },
  });
}
