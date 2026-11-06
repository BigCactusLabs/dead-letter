// Package versions are PEP 440 strings; anything else is ignored rather than shown.
const VERSION_PATTERN = /^[0-9A-Za-z.+!-]{1,64}$/;

export function registerAboutStore(Alpine) {
  Alpine.store("about", {
    version: "",

    async load() {
      try {
        const response = await fetch("/api/about");
        if (!response.ok) return;
        const payload = await response.json();
        if (typeof payload?.version === "string" && VERSION_PATTERN.test(payload.version)) {
          this.version = payload.version;
        }
      } catch (_err) {
        // Silent — the version label is supplementary and must not block conversion.
      }
    },
  });
}
