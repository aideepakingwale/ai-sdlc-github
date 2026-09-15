---
id: draft_release_notes
name: Draft release notes
description: Draft release notes from commits or a change summary (local model).
phase: 6
roles: [DEV]
tier: local
executor: llm
mock_kind: chat
input_hint: Commit messages or change summary
---

You draft release notes from the given commits/changes, grouped under `### Added`, `###
Changed`, `### Fixed`. One crisp line per item, user-facing language, no commit hashes.
