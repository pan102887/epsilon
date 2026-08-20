# Optional: Hash-Based Drift Tracking via `manifest.json`

This file describes an **optional** enhancement to the drift protocol defined in `../../SKILL.md`. Enable it only when the user explicitly asks for hash-based tracking (typical in long-running, multi-person, or multi-agent work where mtime + user-declared drift is insufficient).

When disabled, the coordinator and roles rely solely on user declaration + mtime + content sanity.

## Location

`docs/spec/<feature-slug>/manifest.json`

## Schema

```json
{
  "version": 1,
  "steering": {
    "sha256": "<aggregate-hash-of-docs/steering>",
    "files": {
      "docs/steering/<file>.md": "<hash>"
    }
  },
  "artifacts": {
    "requirement.md": {
      "sha256": "<hash>",
      "mtime": "<iso-8601>",
      "upstream": { "steering": "<aggregate-hash>" }
    },
    "design.md": {
      "sha256": "<hash>",
      "mtime": "<iso-8601>",
      "stale": false,
      "staleReason": "",
      "upstream": {
        "steering": "<aggregate-hash>",
        "requirement.md": "<hash>"
      }
    },
    "tasks.md": {
      "sha256": "<hash>",
      "mtime": "<iso-8601>",
      "stale": false,
      "staleReason": "",
      "upstream": {
        "steering": "<aggregate-hash>",
        "requirement.md": "<hash>",
        "design.md": "<hash>"
      }
    }
  }
}
```

## Rules

- Compute the `docs/steering` aggregate hash deterministically: concatenate files sorted by path (bytes), then `sha256`.
- After creating or updating `requirement.md`, update its manifest entry and mark `design.md` / `tasks.md` stale unless they are regenerated against the new hash.
- After creating or updating `design.md`, record the current `requirement.md` hash under `design.md.upstream.requirement.md`; update `design.md` metadata; mark `tasks.md` stale unless regenerated.
- After creating or updating `tasks.md`, record the current `requirement.md` and `design.md` hashes under `tasks.md.upstream`.
- If the steering aggregate hash changes, treat all spec artifacts as stale until reviewed or regenerated against current steering.
- Mark stale artifacts with `"stale": true` and a concise `staleReason`; clear those fields only after regenerating against current upstream hashes.
- Before implementation, require `tasks.md.upstream.requirement.md` and `tasks.md.upstream.design.md` to match current hashes. If they do not match, stop implementation and regenerate stale artifacts in pipeline order.
- If `manifest.json` is missing while the manifest mode is enabled, create it before proceeding and treat existing downstream artifacts as unverified until their upstream hashes are recorded or regenerated.
- Preserve unrelated manifest entries written by other tools.

## Cost and Trade-off

Manifest mode makes drift judgments authoritative even against pure-formatting edits. The cost is operational noise: re-formatting a steering doc marks every spec artifact stale until cleared. Only enable it when that trade-off is explicitly acceptable to the user.
