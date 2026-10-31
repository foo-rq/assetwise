"use strict";

// Keep the existing allocation validators and scenario rules, changing only their storage adapter
const sqlStorage = {
  // Cache the loaded records in the same string-key/string-value form used by the old browser store
  // Editing helpers can keep their existing validation without talking to SQL directly
  values: new Map(),

  // revision changes only after a confirmed SQL write; ready stays false until the first load succeeds
  revision: 0,
  ready: false,

  // A stale tab must reload after a conflict rather than retry with another tab's newer revision
  conflicted: false,

  // Each write waits on this promise, preventing rapid edits from reaching SQL in the wrong order
  pending: Promise.resolve(),
  key: null,

  // Count local mutations separately from SQL revisions so an older success cannot clear newer edits
  changeNumber: 0,
  dirty: false,

  // Match localStorage's missing-key result so the existing validators can detect an absent save
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) {
    // Rewriting the same JSON is not a new edit and should not trigger an unsaved-changes warning
    if (this.values.get(key) === value) return;
    this.values.set(key, value);
    this.changeNumber++;
    this.dirty = true;
  },
  removeItem(key) {
    // Removing an already absent recovery record likewise does not change the saved collection
    if (!this.values.delete(key)) return;
    this.changeNumber++;
    this.dirty = true;
  },

  async request(method, state) {
    // The private key belongs in a header, keeping it out of URLs and ordinary access logs
    // GET has no body; PUT sends only allocations/scenarios with the last confirmed revision
    const response = await fetch("/api/storage", {
      method,
      headers: { "Content-Type": "application/json", "X-Workspace-Key": this.key },
      body: state ? JSON.stringify(state) : undefined,
      signal: AbortSignal.timeout(10000)
    }).catch(() => {
      // Keep unsaved edits on screen and give a recovery action instead of a browser error
      throw new Error("Cannot reach saving. Keep this page open and retry when the connection returns.");
    });
    const result = await response.json();
    if (!response.ok) {
      // A 409 means another writer won, not a transient timeout that can safely be overwritten
      if (response.status === 409) this.conflicted = true;
      throw new Error(typeof result.detail === "string" ? result.detail : "Saved data was rejected. Export a backup before reloading.");
    }
    return result;
  },

  async open() {
    // This unguessable browser key separates visitors without introducing account management
    this.key = localStorage.getItem("assetwise.workspace-key.v1");
    if (!this.key) {
      // Generate 256 random bits and preserve leading zeroes when converting each byte to hex
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      this.key = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");

      // If browser storage is blocked, fail before creating inaccessible server-side saves
      localStorage.setItem("assetwise.workspace-key.v1", this.key);
    }
    if (!/^[a-f0-9]{64}$/.test(this.key)) throw new Error("The workspace key is invalid. Export a backup before clearing site data.");
    // Do not clear our cache until the server has actually returned this browser's collection
    const state = await this.request("GET");
    this.revision = state.revision;
    this.profile = state.profile || null;
    this.values.clear();
    for (const item of state.current) {
      // Version-specific keys prevent switching data sources from replacing the other current portfolio
      this.setItem(`assetwise.portfolio.v1:${item.datasetVersion}`, JSON.stringify(item));
    }
    // Scenarios stay in 1 list so incompatible versions remain visible rather than disappearing
    this.setItem(scenarioStorage.key, JSON.stringify({ version: 1, items: state.scenarios }));
    if (state.removed) this.setItem(`${scenarioStorage.key}.removed`, JSON.stringify(state.removed));
    // Populating the cache from SQL is a read, even though it uses the same setItem helper as edits
    this.dirty = false;
    this.ready = true;
    if (state.revision === 0) {
      // Copy browser saves once, preserving original keys as a backup if the import fails
      const old = localStorage.getItem("assetwise.portfolio.v1");
      if (old) {
        // The old format already identifies its dataset, so migration need not guess the current mode
        const item = JSON.parse(old);
        if (!item.datasetVersion) throw new Error("The old browser portfolio could not be imported. Its original save is unchanged.");
        this.setItem(`assetwise.portfolio.v1:${item.datasetVersion}`, old);
      }
      for (const key of [scenarioStorage.key, `${scenarioStorage.key}.removed`]) {
        // Copy the named list and its single Undo-removal record as part of the same SQL transaction
        const value = localStorage.getItem(key);
        if (value !== null) this.setItem(key, value);
      }
      if (old || localStorage.getItem(scenarioStorage.key) || localStorage.getItem(`${scenarioStorage.key}.removed`)) {
        // Server validation must accept the entire migration before any SQL success is reported
        await this.flush();
      }
    }
  },

  snapshot() {
    // Convert the compatibility cache into the API's collection shape without saving calculated metrics
    // Parsing makes detached objects, so later edits cannot alter a body already waiting in the queue
    return {
      profile: this.profile || null,
      current: [...this.values].filter(([key]) => key.startsWith("assetwise.portfolio.v1:"))
        .map(([, value]) => JSON.parse(value)),
      scenarios: scenarioStorage.read(this),
      removed: JSON.parse(this.getItem(`${scenarioStorage.key}.removed`) || "null")
    };
  },

  flush() {
    // Capture each accepted edit and send writes in order; failed writes never become confirmations
    const state = this.snapshot();
    const changeNumber = this.changeNumber;
    const task = this.pending.catch(() => {}).then(async () => {
      // Continue the queue after a temporary failure, but never bypass a failed load or stale revision
      if (!this.ready) throw new Error("Saving could not connect. Export a backup before reloading.");
      if (this.conflicted) throw new Error("Another tab saved changes. Export your edits before reloading.");
      // Read revision now, after earlier writes finish, rather than when this edit joined the queue
      const saved = await this.request("PUT", { ...state, revision: this.revision });
      this.revision = saved.revision;
      // Only clear the warning if no newer in-memory change arrived while SQL was saving this snapshot
      if (changeNumber === this.changeNumber) this.dirty = false;
    });
    this.pending = task;
    return task;
  },

  async importBackup(raw) {
    // Import replaces the saved collection only after the server validates the entire backup
    // Cap input before parsing; normal allocation-only backups are much smaller than price history
    if (raw.length > 200000) throw new Error("Backup is too large");
    const backup = JSON.parse(raw);
    if (backup.format !== "assetwise-backup-v1") throw new Error("Choose an Assetwise backup file");
    // Finish earlier edits first so the import cannot be replaced by a previously queued autosave
    await this.pending;
    if (!this.ready || this.conflicted) throw new Error("Reload to reconnect saving before importing a backup");
    // Ignore any unrelated file metadata and never import another browser's private access key
    const { current, scenarios, removed } = backup;
    const profile = backup.profile ?? this.profile ?? null;
    if (!Array.isArray(current) || !Array.isArray(scenarios)) throw new Error("Backup portfolios are missing");
    // SQL validates names, versions and weights before atomically replacing the collection
    const saved = await this.request("PUT", { revision: this.revision, current, scenarios, removed, profile });
    this.revision = saved.revision;
    this.dirty = false;
  },

  exportBackup(current) {
    // Export allocations and survey answers, never the private key or calculated performance
    const state = this.snapshot();
    if (current) {
      // Include the visible allocation without turning an export into an unsaved SQL edit
      state.current = state.current.filter(item => item.datasetVersion !== current.datasetVersion);
      state.current.push(current);
    }
    const raw = JSON.stringify({ format: "assetwise-backup-v1", ...state }, null, 2);

    // Download from a temporary local Blob URL; no backup contents are uploaded to another service
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "assetwise-portfolios.json";
    link.click();

    // Release the temporary bytes after the browser has had time to start its download
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
};
