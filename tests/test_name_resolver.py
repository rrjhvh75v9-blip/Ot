"""
Tests for artarb.utils.name_resolver.

The resolver uses a lazy-loaded, module-level cache backed by the database.
All tests here bypass the database entirely: the `resolver_cache` fixture
builds the cache directly from SimpleNamespace artist objects and uses
monkeypatch to inject it, so tests are fast, hermetic, and run without a
running PostgreSQL.
"""

import logging
import uuid
from types import SimpleNamespace

import pytest

import artarb.utils.name_resolver as nr
from artarb.utils.name_resolver import resolve

# ---------------------------------------------------------------------------
# Fixed UUIDs — deterministic so assertion failures print readable values
# ---------------------------------------------------------------------------

CORNEILLE_ID   = uuid.UUID("c0111111-0000-0000-0000-000000000001")
APPEL_ID       = uuid.UUID("a9910000-0000-0000-0000-000000000002")
ALECHINSKY_ID  = uuid.UUID("a1ec0000-0000-0000-0000-000000000003")
_BREITNER_ID   = uuid.UUID("b1234567-0000-0000-0000-000000000099")  # noise artist


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def resolver_cache(tmp_path, monkeypatch):
    """Populate the resolver cache with known test artists; no DB required.

    Uses monkeypatch so every module-level mutation is automatically undone
    after each test, keeping tests fully independent.
    """
    # --- artists under test + a noise artist so matching must discriminate ---
    artists = [
        SimpleNamespace(
            id=CORNEILLE_ID,
            name_canonical="Corneille",
            # Full birth name stored as a variant — this is the key for the
            # second Corneille test.
            name_variants=["Guillaume Cornelis van Beverloo", "Guillaume van Beverloo"],
        ),
        SimpleNamespace(
            id=APPEL_ID,
            name_canonical="Appel, Karel",
            name_variants=None,
        ),
        SimpleNamespace(
            id=ALECHINSKY_ID,
            name_canonical="Alechinsky, Pierre",
            name_variants=None,
        ),
        SimpleNamespace(
            id=_BREITNER_ID,
            name_canonical="Breitner, George Hendrik",
            name_variants=["G.H. Breitner"],
        ),
    ]

    # Build the cache exactly as _load_cache does, but without touching the DB.
    cache: dict[str, tuple[uuid.UUID, str]] = {}
    for artist in artists:
        for candidate in nr._all_candidates(artist.name_canonical, artist.name_variants):
            if candidate not in cache:
                cache[candidate] = (artist.id, artist.name_canonical)

    monkeypatch.setattr(nr, "_cache", cache)
    monkeypatch.setattr(nr, "_cache_loaded", True)

    # Redirect the unmatched log to a temp file so tests never write to CWD
    # and each test starts with a clean log.
    monkeypatch.setattr(nr, "UNMATCHED_LOG_PATH", tmp_path / "unmatched.log")
    monkeypatch.setattr(nr, "_unmatched_log", None)
    # Also clear any handlers the singleton logger may already hold.
    unmatched_logger = logging.getLogger("artarb.unmatched_artists")
    for handler in list(unmatched_logger.handlers):
        unmatched_logger.removeHandler(handler)
        handler.close()

    return {"cache": cache, "log_path": tmp_path / "unmatched.log"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCorneille:
    """Corneille (1922-2010) is known by his mononym; his full birth name
    'Guillaume Cornelis van Beverloo' must resolve to the same record."""

    def test_stage_name_resolves(self):
        assert resolve("Corneille") == CORNEILLE_ID

    def test_full_birth_name_resolves(self):
        assert resolve("Guillaume Cornelis van Beverloo") == CORNEILLE_ID

    def test_stage_name_and_full_birth_name_yield_same_uuid(self):
        """The whole point of name_variants: both names are one artist."""
        assert resolve("Corneille") == resolve("Guillaume Cornelis van Beverloo")

    def test_partial_birth_name_resolves(self):
        """Shorter variant 'Guillaume van Beverloo' is also stored."""
        assert resolve("Guillaume van Beverloo") == CORNEILLE_ID


class TestKarelAppel:
    """Karel Appel (1921-2006), CoBrA movement.
    RKD canonical is 'Appel, Karel'; scrapers deliver 'Karel Appel'."""

    def test_firstname_lastname_resolves(self):
        assert resolve("Karel Appel") == APPEL_ID

    def test_lastname_firstname_resolves(self):
        assert resolve("Appel, Karel") == APPEL_ID

    def test_uppercased_resolves(self):
        assert resolve("KAREL APPEL") == APPEL_ID

    def test_surname_only_resolves(self):
        assert resolve("Appel") == APPEL_ID


class TestPierreAlechinsky:
    """Pierre Alechinsky (1927-), Belgian CoBrA artist.
    RKD canonical is 'Alechinsky, Pierre'."""

    def test_firstname_lastname_resolves(self):
        assert resolve("Pierre Alechinsky") == ALECHINSKY_ID

    def test_lastname_firstname_resolves(self):
        assert resolve("Alechinsky, Pierre") == ALECHINSKY_ID


class TestNoMatch:
    def test_fake_name_returns_none(self):
        assert resolve("Xyz Nonexistent") is None

    def test_fake_name_written_to_unmatched_log(self, resolver_cache):
        resolve("Xyz Nonexistent")
        log_path = resolver_cache["log_path"]
        assert log_path.exists(), "Unmatched log file was not created"
        content = log_path.read_text(encoding="utf-8")
        assert "Xyz Nonexistent" in content

    def test_empty_string_returns_none(self):
        assert resolve("") is None

    def test_whitespace_only_returns_none(self):
        assert resolve("   ") is None

    def test_unmatched_log_contains_closest_candidate(self, resolver_cache):
        """The log line should include a BEST_BELOW_THRESHOLD hint."""
        resolve("Xyz Nonexistent")
        content = resolver_cache["log_path"].read_text(encoding="utf-8")
        assert "BEST_BELOW_THRESHOLD" in content or "NO_CANDIDATES" in content
