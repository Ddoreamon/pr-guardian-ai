# ABORTED RUN — zero model calls, no data

This directory is the first attempt at the frozen held-out run (commit
`ebcd05f`). It is preserved for auditability and **must not be used as a result**.

## What happened

All 24 variants were recorded as `status=error` with
`GitReaderError: Not a git repository`, and the run made **0 model requests**
(`Model requests (failed): 0`), so it produced no data and no cost.

## Cause

A module-level `_git(*args)` helper added for experiment metadata shadowed the
existing repo-scoped `_git(repo, *args)` used by `materialise()`. Every
`git init` / `git add` / `git commit` call silently failed (the new helper did
not raise), so `read_changed_files()` ran against a non-repository.

## Fix

The metadata helper is now `_git_output(*args)`; the repo-scoped `_git(repo, ...)`
is intact. A regression test
(`tests/test_contrast.py::test_materialise_produces_a_usable_git_repository`)
asserts that materialisation yields a working repository and a non-empty diff.

The experiment was then re-run once from a clean tree; that result lives in
`contrast-heldout-v3/`.
