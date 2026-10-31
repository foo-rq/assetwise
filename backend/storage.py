import hashlib
import os
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.analytics import AllocationRequest
from backend.profile import InvestorProfile


@lru_cache(maxsize=8)
def dataset_symbols(version):
    # Keep obsolete versions preservable while checking identifiers for datasets we can calculate
    from backend.historical import build_historical_history
    if version == "demo-v2":
        # Preserve validation of old saves without shipping or running the retired price generator
        return {"DEMO-EQ1", "DEMO-EQ2", "DEMO-BOND", "DEMO-GOLD", "DEMO-GROWTH",
                "DEMO-VALUE", "DEMO-SMALL", "DEMO-GLOBAL", "DEMO-SHORT", "DEMO-CREDIT",
                "DEMO-PROPERTY", "DEMO-COMMODITY"}
    factories = {"archive-2017-v1": build_historical_history,
                 "archive-2017-v2": build_historical_history}
    factory = factories.get(version)

    # None means an old version cannot be checked here, not that it has an empty asset universe
    # Retaining those snapshots lets the UI list them as unavailable instead of deleting user data
    return {asset.symbol for asset in factory().assets} if factory else None


class Snapshot(AllocationRequest):
    # Inherit strict numeric weights and the at-most-100% total from the calculation request
    # Keep the existing browser field names so migrating a snapshot does not change its meaning
    version: Literal[1] = 1
    datasetVersion: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")]
    # Current allocations need no name; the collection validator requires names for saved scenarios
    name: Annotated[str, Field(max_length=60)] = ""

    @model_validator(mode="after")
    def bounded_snapshot(self):
        # Older dataset versions remain preservable, but arbitrary unbounded records do not
        if len(self.weights) > 64 or any(
            not symbol or len(symbol) > 32 or not all(c.isalnum() or c in ".-" for c in symbol)
            for symbol in self.weights
        ):
            raise ValueError("A saved portfolio supports at most 64 valid asset identifiers")
        # Trim only the label; preserve the actual asset weights and dataset version exactly
        self.name = self.name.strip()
        symbols = dataset_symbols(self.datasetVersion)
        if symbols is not None and self.weights.keys() - symbols:
            # A valid-looking ticker is still invalid when it does not belong to this dataset
            raise ValueError("Unknown asset identifier for the saved dataset")
        return self


class SavedState(BaseModel):
    # Unknown collection fields are errors, preventing misspellings from becoming empty saves
    model_config = ConfigDict(extra="forbid")

    # The client sends its last confirmed revision; the SQL transaction compares it before writing
    revision: Annotated[int, Field(strict=True, ge=0)] = 0

    # Keep current allocations for multiple data versions without mixing their assets or results
    current: Annotated[list[Snapshot], Field(max_length=8)] = []
    scenarios: Annotated[list[Snapshot], Field(max_length=100)] = []

    # Store 1 removed scenario separately so Undo removal also survives a normal reload
    removed: Snapshot | None = None

    # Survey answers share the same owner and revision as saved portfolios
    profile: InvestorProfile | None = None

    @model_validator(mode="after")
    def unique_snapshots(self):
        # Two current allocations for 1 version would make loading depend on accidental row order
        if len({item.datasetVersion for item in self.current}) != len(self.current):
            raise ValueError("Only 1 current portfolio is allowed per dataset")
        # Casefold treats differently capitalised scenario names as the same saved name
        names = [item.name.casefold() for item in self.scenarios]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("Scenario names must be nonempty and unique")
        if self.removed is not None and not self.removed.name:
            raise ValueError("A removed scenario needs a name")
        return self


class SaveConflict(Exception):
    # Keep stale revisions distinct from disk/SQL failures so the API can return HTTP 409
    pass


def database_path():
    # Keep personal allocations outside the served interface and exclude them from Git/images
    default = Path(__file__).resolve().parents[1] / ".assetwise" / "portfolios.sqlite3"
    return Path(os.environ.get("ASSETWISE_DB_PATH", str(default)))


@contextmanager
def connection():
    # Create the private folder on first use, not inside the publicly served prototype directory
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Wait briefly for another short write rather than fail immediately with "database is locked"
    db = sqlite3.connect(path, timeout=10)

    # Named columns keep reconstruction readable when the query contains several text fields
    db.row_factory = sqlite3.Row
    try:
        # SQLite needs this per connection for portfolio deletion to cascade into holding rows
        db.execute("PRAGMA foreign_keys = ON")
        # Short transactions work with SQLite's single writer and need no ORM dependency
        db.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                -- Store only a hash of the browser key alongside the latest accepted revision
                owner TEXT PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS profiles (
                owner TEXT PRIMARY KEY REFERENCES workspaces(owner),
                answers TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolios (
                -- Keep the portfolio even when its holdings are empty, meaning 100% cash
                id INTEGER PRIMARY KEY,
                owner TEXT NOT NULL REFERENCES workspaces(owner),
                kind TEXT NOT NULL CHECK(kind IN ('current', 'scenario', 'removed')),
                -- Preserve the order of current allocations and named scenarios from the request
                position INTEGER NOT NULL,
                name TEXT NOT NULL,
                dataset_version TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS portfolios_owner ON portfolios(owner);
            CREATE TABLE IF NOT EXISTS holdings (
                -- Each holding belongs to 1 portfolio, so scenarios never share mutable weights
                portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 100),
                PRIMARY KEY(portfolio_id, symbol)
            );
        """)
        with db:
            # Commit when the caller finishes; any raised error rolls the whole operation back
            yield db
    finally:
        # Close even after validation, conflict or disk errors so locks do not outlive the request
        db.close()


def read_state(db, owner):
    # An unseen browser starts at revision 0 without creating a database row just for a page view
    row = db.execute("SELECT revision FROM workspaces WHERE owner = ?", (owner,)).fetchone()
    state = SavedState(revision=row["revision"] if row else 0)
    profile = db.execute("SELECT answers FROM profiles WHERE owner = ?", (owner,)).fetchone()
    if profile:
        # Validate stored categories before returning them to the survey
        state.profile = InvestorProfile.model_validate_json(profile["answers"])
    rows = db.execute(
        "SELECT * FROM portfolios WHERE owner = ? ORDER BY position, id", (owner,)
    ).fetchall()
    for row in rows:
        # Convert (symbol, weight) rows back into the dictionary expected by the existing editor
        weights = dict(db.execute(
            "SELECT symbol, weight FROM holdings WHERE portfolio_id = ? ORDER BY rowid", (row["id"],)
        ).fetchall())
        item = Snapshot(name=row["name"], datasetVersion=row["dataset_version"], weights=weights)
        if row["kind"] == "removed":
            # Removed scenarios stay recoverable but are not shown as ordinary saved choices
            state.removed = item
        else:
            # The owner filter above applies to every kind, including saved and current portfolios
            (state.current if row["kind"] == "current" else state.scenarios).append(item)
    return state


def write_state(db, owner, state):
    # Check the revision inside the write lock so 2 tabs cannot silently overwrite each other
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR IGNORE INTO workspaces(owner) VALUES (?)", (owner,))
    revision = db.execute("SELECT revision FROM workspaces WHERE owner = ?", (owner,)).fetchone()[0]
    if revision != state.revision:
        # Raising before deletion preserves the newer tab's complete portfolio collection
        raise SaveConflict()

    # Older clients omit profile; preserve existing answers rather than silently erase them
    if state.profile is not None:
        db.execute("INSERT OR REPLACE INTO profiles(owner, answers) VALUES (?, ?)",
                   (owner, state.profile.model_dump_json()))
    elif "profile" in state.model_fields_set:
        db.execute("DELETE FROM profiles WHERE owner = ?", (owner,))

    # Replace this owner's collection inside 1 transaction; foreign keys remove its old holdings
    # If an insert later fails, rollback restores the deleted rows as well as their weights
    db.execute("DELETE FROM portfolios WHERE owner = ?", (owner,))
    groups = [("current", state.current), ("scenario", state.scenarios)]
    if state.removed:
        groups.append(("removed", [state.removed]))
    for kind, items in groups:
        for position, item in enumerate(items):
            # Use placeholders for names and identifiers so user text is never executed as SQL
            cursor = db.execute(
                "INSERT INTO portfolios(owner, kind, position, name, dataset_version) VALUES (?, ?, ?, ?, ?)",
                (owner, kind, position, item.name, item.datasetVersion),
            )
            # Empty holdings still keep their portfolio row, representing a valid all-cash save
            # lastrowid attaches each weight to the portfolio just inserted, not a neighbouring save
            db.executemany("INSERT INTO holdings(portfolio_id, symbol, weight) VALUES (?, ?, ?)",
                           [(cursor.lastrowid, symbol, weight) for symbol, weight in item.weights.items()])
    # Advance once for the whole accepted collection, including rename/removal-only changes
    # The response carries this revision so the client's next write can prove it is up to date
    db.execute("UPDATE workspaces SET revision = revision + 1 WHERE owner = ?", (owner,))
    return state.model_copy(update={"revision": revision + 1})


router = APIRouter(prefix="/api/storage", tags=["saving"])


def owner_id(key):
    # The browser keeps a random capability, while SQL stores only its hash
    # Require exactly 32 bytes encoded as 64 lowercase hexadecimal characters before any SQL access
    if key is None or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise HTTPException(401, "A valid private workspace key is required")
    return hashlib.sha256(key.encode()).hexdigest()


@router.get("", response_model=SavedState)
def load_saved_state(x_workspace_key: Annotated[str | None, Header()] = None):
    # Do not accept an owner identifier in the URL/body; derive it only from the private header
    owner = owner_id(x_workspace_key)
    try:
        with connection() as db:
            # One read transaction keeps the revision and portfolio rows from different edits apart
            db.execute("BEGIN")
            return read_state(db, owner)
    except (sqlite3.Error, OSError) as error:
        raise HTTPException(503, "Saving is unavailable. Keep this tab open and retry") from error


@router.put("", response_model=SavedState)
def save_state(state: SavedState, x_workspace_key: Annotated[str | None, Header()] = None):
    # FastAPI validates every snapshot before opening a transaction or deleting existing records
    owner = owner_id(x_workspace_key)
    try:
        with connection() as db:
            return write_state(db, owner, state)
    except SaveConflict as error:
        # Reload/export guidance is different from retrying a temporary connection problem
        raise HTTPException(409, "Another tab saved changes. Reload before saving again") from error
    except (sqlite3.Error, OSError) as error:
        raise HTTPException(503, "Saving is unavailable. Keep this tab open and retry") from error
