# CLI reference

## Pin management

| Command | Description |
|---|---|
| `refactory pin list` | List all pins with commit (first 12 chars), creation time and driver checkout. |
| `refactory pin prune` | Remove pins no run refers to. Also removes their `graph-pin-<commit12>` profiles from the person's DSH home. Never removes a pin that a live or resumable run refers to. |
