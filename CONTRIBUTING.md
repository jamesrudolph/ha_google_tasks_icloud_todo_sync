# Contributing

> Publishing to HACS: see [AGENTS.md](AGENTS.md) and https://hacs.xyz/docs/publish/

See [PLAN.md](PLAN.md) for design and scope. Run `ruff check .` and `pytest` before opening a PR.

## Releasing

1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new `## [X.Y.Z]` heading and merge.
2. Create and push a tag: `git tag vX.Y.Z && git push origin vX.Y.Z` (SemVer; use `vX.Y.Z-beta.N` for pre-releases).
3. The **Release** workflow sets `version` in `manifest.json` from the tag, builds `ha_google_tasks_icloud_todo_sync.zip`, and publishes a GitHub release with generated notes. Tags containing a `-` are marked as pre-releases.
