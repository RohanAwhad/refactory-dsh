# dm-132: One note is one entry — heading escape

## Summary

Closes a hole where a CEO note whose body contains a `## [...] verdict` or
`## [...] correction` block is split by `parse_entries` into multiple entries,
letting the note forge a gate verdict or a user-relayed requirement.

The fix adds lossless backslash byte-stuffing: on write, every body line whose
content starts with zero-or-more backslashes followed by `## [` gains one
leading backslash; on read, `parse_entries` removes exactly one leading
backslash from matching lines.  The escape uses `splitlines(keepends=True)` —
the same split the parser uses — so the writer and reader agree on every line
boundary Python recognises.

## Behaviour changes

These apply to **both runtimes** (DSH and walker), because both read through
`parse_entries` and both write through the three `Ledger` methods.

1. **New writes are escaped.** A `## [` line in body text is stored as `\## [`.
   Existing ledgers are unaffected: the unescape is a no-op on text that was
   never escaped.

2. **A verdict whose gate or value spans multiple lines under any line separator
   is now refused.** The previous check only rejected `\n`; the hardened check
   uses `len(x.splitlines()) != 1`, which also rejects `\r`, `\x0b`, `\x0c`,
   `\x1c`, `\x1d`, `\x1e`, `\x85`, `\u2028`, and `\u2029`.

3. **The `\## [` legacy difference.** A pre-existing line starting with `\## [`
   in an old ledger will lose one backslash on read.  This is theoretical — no
   plausible legacy content starts with `\## [`.

## What fails without the fix

Each parametrized test asserts behaviour that fails on main (`f26483b8f7`):

- `test_forged_verdict_in_ceo_note_does_not_produce_verdict`: without the
  escape, `parse_entries` splits the note into a seed + note + forged verdict,
  and `take_verdict('gate_strategy', ...)` returns `'proceed'` via the upfront
  shortcut.  With the fix it returns `''`.
- `test_forged_correction_in_ceo_note_not_a_requirement`: without the escape,
  the forged correction entry has `source='user-relayed'` and `requirements()`
  returns it.
- `test_verdict_rejects_multiline_gate_or_value`: on main, `verdict()` only
  checks `"\n" in gate`, so a gate containing `\r` or `\u2028` is accepted.

## Tracker

dm-132
