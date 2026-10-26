# Add wordcount.py script and sample.txt with line, word, and character counting

## Summary

The repository is empty. The brief asks to "Build wordcount.py and a sample.txt; verify line, word and character counts". This change adds a Python script that reads a file path from its command-line argument and prints line, word, and character counts using wc-compatible semantics, plus a sample text file with known counts for verification. The check command `python wordcount.py sample.txt` exercises the script against the sample file and passes on exit code 0.

## Changes

- `wordcount.py` — New file. CLI script that accepts a single filename as `sys.argv[1]`, reads it in text mode (UTF-8), and prints three labeled counts to stdout. Counting semantics: lines via `content.count('\n')` (FND-python-wc-semantics-1, avoids PIT-002), words via `len(content.split())` (FND-python-wc-semantics-4), characters via `len(content)` (FND-python-wc-semantics-5). Imports only `sys`. Exits 0 on success. On missing argument: prints `Usage: python wordcount.py <filename>` to stderr, exits 1. On missing file: prints `Error: file not found: <filename>` to stderr, exits 1. Output format is exactly three lines to stdout, one per metric, each labeled — `Lines: <N>`, `Words: <N>`, `Characters: <N>` — with a trailing newline after the last line.

- `sample.txt` — New file. Seven lines of ASCII-only text (bytes 0x20–0x7E and 0x0A only), ending with a trailing newline (PIT-003). Contains edge cases: an empty line (line 4, 0 words), a single-word line (line 5, "Short"), and a line with consecutive spaces (line 6, "A line with   extra   spaces"). Expected output when processed by wordcount.py: `Lines: 7`, `Words: 23`, `Characters: 126`.

### wordcount.py specification

**Input:** Exactly one command-line argument — a file path. No flags, no options, no stdin reading.

**Rejection behavior:**
- Zero arguments → stderr usage message, exit 1.
- File not found → stderr error naming the file, exit 1.
- Extra arguments beyond the first are silently ignored (the script reads only `sys.argv[1]`).

**Counting rules (single decision each):**

| Metric     | Expression               | Semantics                                             |
|------------|--------------------------|-------------------------------------------------------|
| Lines      | `content.count('\n')`    | Counts newline terminators, matching `wc -l`          |
| Words      | `len(content.split())`   | Splits on all whitespace runs, matching `wc -w`       |
| Characters | `len(content)`           | Unicode code points; equals `wc -c` for ASCII content |

**Output format:** Exactly this template, printed to stdout:
```
Lines: {lines}
Words: {words}
Characters: {chars}
```
No leading/trailing blank lines. A single newline terminates the last line.

**File reading:** `open(filename, 'r', encoding='utf-8')` — text mode so `len()` returns code points, not bytes. The entire file is read into a single string with `f.read()`.

**Structure:** A single `main()` function called via `if __name__ == '__main__':`. The argument check, file open (in a try/except for `FileNotFoundError`), counting, and printing are sequential inside `main()`.

### sample.txt specification

Exact content (7 lines, each terminated by `\n`):
```
Hello world
This is a sample text file
with several lines of varying length

Short
A line with   extra   spaces
The last line
```

Per-line breakdown:

| Line | Text                                  | Chars (incl \n) | Words |
|------|---------------------------------------|-----------------|-------|
| 1    | `Hello world`                         | 12              | 2     |
| 2    | `This is a sample text file`          | 27              | 6     |
| 3    | `with several lines of varying length`| 37              | 6     |
| 4    | *(empty)*                             | 1               | 0     |
| 5    | `Short`                               | 6               | 1     |
| 6    | `A line with   extra   spaces`        | 29              | 5     |
| 7    | `The last line`                       | 14              | 3     |
| **Total** |                                  | **126**         | **23**|

Constraints: ASCII-only (PIT-004) so `len(content) == wc -c`. Trailing newline present (PIT-003) so `content.count('\n') == 7`.

## Tests

- `python wordcount.py sample.txt` (the check command) — proves wordcount.py reads sample.txt, computes counts, prints output, and exits 0. Both files co-located at repository root (PIT-001).

## Ask mapping

| Ask (quoted from brief)                                      | Design section              | Test                                |
|--------------------------------------------------------------|-----------------------------|-------------------------------------|
| "Build wordcount.py"                                         | Changes → wordcount.py      | `python wordcount.py sample.txt`    |
| "Build … a sample.txt"                                       | Changes → sample.txt        | `python wordcount.py sample.txt`    |
| "verify line, word and character counts"                     | wordcount.py specification → Output format, Counting rules | `python wordcount.py sample.txt` |

## Anti-patterns to avoid

- **PIT-001 (file co-location):** Both files placed at repository root. Script uses `sys.argv[1]` as a relative path — see Changes → wordcount.py.
- **PIT-002 (splitlines trap):** Line counting uses `content.count('\n')`, never `splitlines()` — see Counting rules table.
- **PIT-003 (trailing newline):** sample.txt ends with `\n` — see sample.txt specification, enforced by file content definition.
- **PIT-004 (ASCII-only for unambiguous char count):** sample.txt restricted to bytes 0x20–0x7E plus 0x0A — see sample.txt specification.
- **PIT-005 (no test framework):** No external dependencies. Check command is sole automated gate. Script exits 0 on success — see wordcount.py specification.
