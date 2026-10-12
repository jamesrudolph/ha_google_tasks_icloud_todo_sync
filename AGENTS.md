# Agent notes

This is a HACS custom integration. Before changing repository layout, `hacs.json`,
`manifest.json`, release workflows or brand assets, consult the HACS publish
documentation: https://hacs.xyz/docs/publish/ (integration specifics:
https://hacs.xyz/docs/publish/integration/).

Compliance checklist (last reviewed against the docs):

- Single integration in `custom_components/ha_google_tasks_icloud_todo_sync/` - OK
- `manifest.json` has domain, documentation, issue_tracker, codeowners, name, version - OK
- `hacs.json` has `name`; `zip_release` + `filename` match the release workflow asset - OK
- README at repo root; GitHub releases are published by `.github/workflows/release.yml` - OK
- HACS and hassfest validation run in `.github/workflows/validate.yml` - OK
- Brand images (`icon.png`, optionally `logo.png`) in
  `custom_components/ha_google_tasks_icloud_todo_sync/brand/` - OK (added; was required before
  submitting to the HACS default list; the HACS action checks for brand images)
- GitHub repository description and topics must be set in repo settings (not verifiable from files)
