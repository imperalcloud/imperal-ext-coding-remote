"""v1.8.1 — coding-remote is a SYSTEM app, and its manifest says so twice.

Background (2026-07-26). The app kept showing up in the Marketplace even after
repeated re-deploys, while `billing` -- equally a system app -- never did. The
manifest already carried `"system": true`, so the flag was NOT the problem:
the gateway syncs manifest fields into `developer_apps` from an allowlist that
includes BOTH `system` and `category`, but it only writes a key that is
actually PRESENT in the manifest. `category` was absent here, so the row kept
the `productivity` value it was first created with, forever -- and every
re-deploy legitimately left it untouched. `billing` sits in `system`.

Marketplace read paths filter on `system = FALSE`, so the listing was already
correct; the stale `productivity` category is what kept the app looking like
an ordinary productivity app to anything grouping by category, and what made
the row disagree with the manifest.

These tests pin BOTH fields, because either one alone is a silent trap:
  * `system` alone  -> what we had: the app looks miscategorised forever.
  * `category` alone -> the app would be listed in the Marketplace.
They read the shipped `imperal.json` (the deploy source of truth), not any
in-memory copy, so drift between file and code is caught rather than mocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path(__file__).resolve().parent.parent / "imperal.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_is_valid_json_and_present(manifest):
    """A malformed manifest fails the deploy, not the runtime -- catch it here."""
    assert manifest["app_id"] == "coding-remote"


def test_marked_as_system_app(manifest):
    """`system: true` is what keeps it OUT of the Marketplace listing: every
    marketplace read path filters `system = FALSE`."""
    assert manifest["system"] is True


def test_category_is_system_and_is_actually_present(manifest):
    """The regression that cost the re-deploys: the gateway's manifest sync
    only writes keys PRESENT in the manifest, so an ABSENT `category` silently
    preserves whatever the DB row was created with. Presence is the assertion
    that matters -- a truthy-but-missing key is exactly the bug."""
    assert "category" in manifest, "absent category => DB keeps its stale value forever"
    assert manifest["category"] == "system"


def test_system_and_category_agree(manifest):
    """Belt and braces: a system app filed under a user-facing category is a
    contradiction that reads as 'someone half-reverted this'."""
    assert manifest["system"] is True and manifest["category"] == "system"


def test_version_matches_the_code(manifest):
    """`app.py` declares the version independently of the manifest; if the two
    drift, the marketplace row and the running extension disagree about what
    is deployed."""
    app_py = (MANIFEST.parent / "app.py").read_text(encoding="utf-8")
    assert f'version="{manifest["version"]}"' in app_py
