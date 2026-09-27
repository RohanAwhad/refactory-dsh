# Steering ledger acceptance — 2026-09-27

Based on main `8c3f2a61b738e8305e0d7b1ee10345b7deb91e3d` (PR #249). **Steering → View full ledger** opens a scrollable viewer over every recorded entry, including the original brief, past notes, corrections, constraints and gate history. Records remain oldest first with their timestamps and complete text. Search matches text, kind or timestamp and never changes the saved ledger.

## What changed

- A compact latest-note preview replaces the long inline list. The viewer keeps its current history while refreshing, labels failed refreshes, follows live appends and restores trigger focus on Close/Escape. Switching watched runs closes the viewer and clears its cached history.
- The ledger parser previously stripped accumulated text on every line, which removed paragraph breaks. It now accumulates each entry before trimming its outer whitespace, preserving blank lines and internal indentation in UI reads and dispatched ledger context. Existing ledger files need no migration or rewrite.
- Steering notes accept multiple lines. Submission disables the Add button until the result arrives; only a confirmed note clears its matching draft. A refused request or a newly typed draft retains the user's text.
- Steering remains append-only context delivered at supported boundaries. Reading history does not repeat gate approvals, alter the graph or rerun completed work. The existing explicit verdict and correction controls retain those responsibilities.

## Native evidence

Chrome checks used the disposable preview home on port 3095 and an isolated, completed local fixture at `.factory/interactive/steering-ledger-preview`. No model calls or execution launches were needed.

- Opened the original brief and all recorded notes; paragraph breaks and indentation remained readable.
- Searched an earlier correction and saw exactly one matching entry.
- Submitted a multiline note through the real panel, verified it persisted exactly on disk, and saw the draft clear after confirmation.
- Appended another note from the local ledger API while the viewer stayed open: its count changed from six to seven, the original brief remained, and search focus stayed in place.
- Closed with Escape and confirmed focus returned to View full ledger.

| Surface | Native capture |
|---|---|
| Full history and original brief | [Ledger](assets/steering-20260927/full-ledger-dark.png) |
| Earlier correction found by search | [Search](assets/steering-20260927/search-past-text-dark.png) |

Captures crop browser chrome and surrounding application space; application pixels are unchanged.

## Automated checks and limits

- GUI: **179 passed in 12 files**, including full history beyond a short recent list, literal text rendering, timestamps, search, refresh/failure retention, live appends, focus restoration, run switching, failed submissions and preserving a newer draft during confirmation.
- Focused Python checks: **5 passed** for multiline ledger/console round trips, retained gate history with one-use verdict consumption, and the existing agent-delivery suite.
- Production build, host/client typechecks, lint and whitespace checks passed.
- The broader steering + delivery selection has **eight existing failures**, reproduced with the exact unmodified `steering.py` from main. They concern legacy resume/verdict and citation expectations. One reference-IR test skips because the optional `remote-factory` oracle is absent. This follow-up does not claim that broader selection is green.
- No upstream dependency, patch, runtime API or ledger schema changes. The viewer uses the existing complete ledger payload and watch stream; the parser and UI fixes remain local to Refactory.
