"""Deterministic fictional paths used only by calculation tests"""

from random import Random
from typing import Literal

from backend.history import AssetHistory, History


# Preserve the demo asset name for existing calculation fixtures
SimulatedAsset = AssetHistory


class SimulatedHistory(History):
    # Literal prevents this response model from accepting a real-market source label
    source: Literal["simulated"] = "simulated"

    # Bump this when changing the seed, coefficients or generation rules
    dataset_version: str = "demo-v2"

    # Send the limitation with the numbers so clients can display it beside results
    notice: str = "Simulated data for fictional assets, not market history or forecasts."

    # Values represent an index rather than dollar prices or separate dividend payments
    value_basis: str = "Unitless index starting at 100, with no separate distributions"

    # Session numbers avoid pretending these changes happened on real trading dates
    time_basis: str = "Numbered synthetic daily sessions, not exchange calendar dates"
    sessions: list[int]

    # All paths use the session list above, allowing like-for-like portfolio comparisons
    assets: list[SimulatedAsset]


def build_simulated_history() -> SimulatedHistory:
    # A fresh local generator makes refreshes repeatable without changing global randomness
    rng = Random(20261010)

    # These coefficients are invented examples, not fitted asset-class estimates
    # The columns are symbol, name, daily drift, shared movement and separate movement
    # A drift of 0.0002 adds 0.02% before that session's random movements
    # The negative bond coefficient reverses its shared movement, but its own move can offset it
    specifications = (
        ("DEMO-EQ1", "Simulated equity 1", 0.0002, 0.014, 0.008),
        ("DEMO-EQ2", "Simulated equity 2", 0.0001, 0.010, 0.006),
        ("DEMO-BOND", "Simulated bond fund", 0.00005, -0.001, 0.003),
        ("DEMO-GOLD", "Simulated gold fund", 0.0001, 0.002, 0.009),
    )

    # Make a separate list for each asset so appending to one never changes another
    paths = [[100.0] for _ in specifications]

    # Keep the original shared draws so new assets can use them without changing older paths
    shared_moves = []

    # Session 0 is the starting value, leaving 252 changes for later return calculations
    for _ in range(252):
        # Reusing this draw makes the assets move together to different degrees
        # Convert random's range of 0 to below 1 into -1 to below 1 for gains and losses
        shared_move = 2 * rng.random() - 1
        shared_moves.append(shared_move)

        # Match each path to its coefficients and fail if the list lengths ever disagree
        # The underscores skip the symbol and name because this step only uses numbers
        for path, (_, _, drift, shared_scale, separate_scale) in zip(
            paths, specifications, strict=True
        ):
            # Each asset also gets its own movement so their paths are not identical
            separate_move = 2 * rng.random() - 1

            # Add the baseline change and both scaled movements as a decimal return
            # Shared movement creates dependence without forcing a diversification benefit
            daily_return = (
                drift
                + shared_scale * shared_move
                + separate_scale * separate_move
            )

            # Compound from yesterday's index without rounding each step
            # The bounded draws and these coefficients keep every return above -100%
            path.append(path[-1] * (1 + daily_return))

    # Attach each finished path to its identifier and display name for the API response
    assets = [
        SimulatedAsset(symbol=spec[0], name=spec[1], index_values=path)
        for spec, path in zip(specifications, paths, strict=True)
    ]

    # These labels describe fictional examples, not measured behaviour of real fund categories
    # Different shared exposures and separate movements give comparisons more than 1 risk level
    additional_specs = (
        ("DEMO-GROWTH", "Simulated growth fund", 0.00025, 0.020, 0.010),
        ("DEMO-VALUE", "Simulated value fund", 0.00015, 0.012, 0.007),
        ("DEMO-SMALL", "Simulated small company fund", 0.0002, 0.018, 0.015),
        ("DEMO-GLOBAL", "Simulated global equity fund", 0.00015, 0.009, 0.011),
        ("DEMO-SHORT", "Simulated short bond fund", 0.00004, -0.0005, 0.0015),
        ("DEMO-CREDIT", "Simulated corporate bond fund", 0.00008, 0.004, 0.004),
        ("DEMO-PROPERTY", "Simulated property fund", 0.00012, 0.008, 0.012),
        ("DEMO-COMMODITY", "Simulated commodity fund", 0.00005, -0.003, 0.016),
    )
    for symbol, name, drift, shared_scale, separate_scale in additional_specs:
        # Seed by identifier so adding or reordering extra assets cannot change existing ones
        asset_rng = Random(f"assetwise:{symbol}:1")
        path = [100.0]
        for shared_move in shared_moves:
            # Each new path follows the same sessions but has its own independent draws
            separate_move = 2 * asset_rng.random() - 1
            daily_return = drift + shared_scale * shared_move + separate_scale * separate_move
            path.append(path[-1] * (1 + daily_return))
        assets.append(SimulatedAsset(symbol=symbol, name=name, index_values=path))

    # Include session 0 so the first generated return has a starting value to divide by
    return SimulatedHistory(sessions=list(range(253)), assets=assets)
