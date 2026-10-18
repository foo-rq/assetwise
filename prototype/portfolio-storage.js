"use strict";

// Keep the saved format separate from API data so future formats can be migrated explicitly
const portfolioStorage = {
  key: "assetwise.portfolio.v1",

  read(storage, assets, datasetVersion) {
    const raw = storage.getItem(this.key);
    if (raw === null) return null;
    const saved = JSON.parse(raw);

    // Never silently restore weights against a changed simulation or unknown asset universe
    if (!saved || saved.version !== 1 || saved.datasetVersion !== datasetVersion ||
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
