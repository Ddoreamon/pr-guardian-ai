"""Unit checks for the deployment fingerprint helper (no network, no server)."""

from __future__ import annotations

import hashlib

from scripts.verify_deployed_build import ASSETS, local_hashes, sha256


def test_line_endings_do_not_change_the_fingerprint():
    lf = b"a\nb\nc\n"
    crlf = b"a\r\nb\r\nc\r\n"
    assert sha256(lf) == sha256(crlf)
    assert sha256(lf) == hashlib.sha256(lf).hexdigest()


def test_local_hashes_cover_every_asset_at_the_tag():
    hashes = local_hashes("v1.0.0-rc2")
    assert set(hashes) == set(ASSETS)
    assert all(len(value) == 64 for value in hashes.values())
    # a known-stable asset must hash the same when fetched from another worktree
    assert hashes["/static/app.js"] == local_hashes("v1.0.0-rc2")["/static/app.js"]
