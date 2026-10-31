"use strict";

// Keep the saved format separate from API data so future formats can be migrated explicitly
const portfolioStorage = {
  key: "assetwise.portfolio.v1",

  read(storage, assets, datasetVersion) {
    // The price correction retains the same symbols; preserve the old save until the user edits
    const raw = storage.getItem(this.key) ?? (datasetVersion === "archive-2017-v2"
      ? storage.getItem("assetwise.portfolio.v1:archive-2017-v1") : null);
    if (raw === null) return null;
    const saved = JSON.parse(raw);

    // Never silently restore weights against a changed simulation or unknown asset universe
    const correctedArchive = saved?.datasetVersion === "archive-2017-v1" && datasetVersion === "archive-2017-v2";
    if (!saved || saved.version !== 1 || (saved.datasetVersion !== datasetVersion && !correctedArchive) ||
        !saved.weights || typeof saved.weights !== "object" || Array.isArray(saved.weights)) {
      throw new Error("Saved portfolio is incompatible");
    }
    let invested = 0;
    for (const [symbol, weight] of Object.entries(saved.weights)) {
      // Storage can be edited outside the app, so validate numbers before rendering them
      if (!Object.hasOwn(assets, symbol) || typeof weight !== "number" ||
          !Number.isFinite(weight) || weight < 0 || weight > 100) {
        throw new Error("Invalid saved allocation");
      }
      invested += weight;
    }
    if (invested > 100 + 1e-8) throw new Error("Saved allocation exceeds 100%");

    // An empty object is a valid all-cash portfolio, not a missing save
    return { ...saved.weights };
  },

  write(storage, weights, datasetVersion) {
    // Save only holdings, never an unapplied preview or calculated results that can become stale
    storage.setItem(this.key, JSON.stringify({ version: 1, datasetVersion, weights }));
  }
};

// Named snapshots use a different key so ordinary edits never overwrite a saved scenario
const scenarioStorage = {
  key: "assetwise.scenarios.v1",

  read(storage) {
    const raw = storage.getItem(this.key);
    if (raw === null) return [];
    const saved = JSON.parse(raw);

    // Refuse an unreadable collection instead of replacing it with an empty list
    if (!saved || saved.version !== 1 || !Array.isArray(saved.items) ||
        saved.items.some(item => !item || typeof item.name !== "string" ||
          !item.name.trim() || item.name.length > 60)) {
      throw new Error("Saved scenarios could not be read");
    }
    return saved.items;
  },

  add(storage, name, weights, datasetVersion) {
    const trimmed = name.trim();
    if (!trimmed || trimmed.length > 60) throw new Error("Enter a name with 1 to 60 characters");
    const items = this.read(storage);

    // Match the server limit before mutating the in-memory collection
    if (items.length >= 100) throw new Error("You can save up to 100 scenarios. Remove 1 before saving another");

    // A repeated name must not silently replace a scenario the user wanted to keep
    if (items.some(item => item.name.toLowerCase() === trimmed.toLowerCase())) {
      throw new Error("That name is already saved. Choose another name");
    }
    items.push({ name: trimmed, version: 1, datasetVersion, weights: { ...weights } });
    storage.setItem(this.key, JSON.stringify({ version: 1, items }));
  },

  rename(storage, oldName, newName) {
    const name = newName.trim();
    if (!name || name.length > 60) throw new Error("Enter a name with 1 to 60 characters");
    const items = this.read(storage);
    const item = items.find(entry => entry.name === oldName);

    // Re-read storage before writing so renaming preserves snapshots saved since the list opened
    if (!item) throw new Error("Scenario no longer exists. Reopen the list");
    if (items.some(entry => entry !== item && entry.name.toLowerCase() === name.toLowerCase())) {
      throw new Error("That name is already saved. Choose another name");
    }
    item.name = name;
    storage.setItem(this.key, JSON.stringify({ version: 1, items }));
  },

  remove(storage, name) {
    const items = this.read(storage);
    const item = items.find(entry => entry.name === name);
    if (!item) throw new Error("Scenario no longer exists");

    // Save one recoverable removal before changing the collection
    storage.setItem(`${this.key}.removed`, JSON.stringify(item));
    storage.setItem(this.key, JSON.stringify({ version: 1, items: items.filter(entry => entry !== item) }));
  },

  restore(storage) {
    const raw = storage.getItem(`${this.key}.removed`);
    if (!raw) throw new Error("No removed scenario to restore");
    const item = JSON.parse(raw);
    this.add(storage, item.name, item.weights, item.datasetVersion);
    storage.removeItem(`${this.key}.removed`);
  },

  allocation(item, assets, datasetVersion) {
    // Use the autosave validator so loading a scenario has the same allocation checks
    return portfolioStorage.read({ getItem: () => JSON.stringify(item) }, assets, datasetVersion);
  }
};
