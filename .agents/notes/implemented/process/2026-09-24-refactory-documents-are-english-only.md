# Agent Note: The refactory documents are English-only

Status: implemented

## Problem

This repository is a fork of DeepSeek Harness, and inherits its documentation standard: [docs/i18n/README.md](../../../../docs/i18n/README.md) requires every in-scope document to carry a Chinese counterpart and a consistency record, and `pnpm run verify-translation-pairing` (a `test:docs` gate) enforces it. Seventeen documents this project owns were paired that way — the root README, the refactory README, four package READMEs, and eleven Agent Notes — although nobody chose it for refactory. Every documentation change cost a translation and a re-recorded hash, and a change that touched only the English side failed the gate by design.

## Decision

**The documents this project owns are English-only.** Their paths are listed in `scripts/translation-pairing.manifest.json` under `excluded`, which is the mechanism the pairing gate provides for this: a listed file has no `.zh.md` and no `.i18n.yaml` at all. The Chinese sides and the consistency records are deleted, and the `English | [中文](…)` switcher line is gone from each English file. Where the Chinese side of an *upstream* document linked to one of them, the link now points at the English path, because a target outside the bilingual corpus keeps the authored path.

**The upstream corpus keeps its Chinese side.** The other 1,379 pairs under `.agents/notes/`, `packages/`, `docs/`, `python/` and `native/` document DeepSeek Harness, its gates expect the pairs, and this fork still merges from upstream; excluding them would turn every future sync into a documentation conflict.

## Consequences

A refactory documentation change is one file, and the pairing gate counts 748 pairs instead of 765. The step that is easy to forget is the manifest: a new refactory document needs its path added there, or its translation brought along. Forgetting is loud rather than silent — the gate reports the document as missing a counterpart and names it.

## Alternatives considered

- **Keep translating.** That is the cost this decision removes: a change to one README meant editing two files plus a hash record, and every correction in this project's documentation arrived twice.
- **Exclude the whole corpus.** It would make the repository English-only, and it would also divorce the fork from upstream documentation permanently: the deleted pairs would conflict on every sync, and the DSH gates and the documentation website are built around them.
- **Delete the Chinese files without listing them.** The gate would report all seventeen as missing counterparts. The manifest entry is what makes "this document has no Chinese side" a decision rather than an omission.
