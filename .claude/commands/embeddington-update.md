---
description: "Bring the local install current and explain the receipt"
allowed-tools: Bash(.venv/bin/embeddington-setup --yes)
---

# Update the install

Run the wizard's unattended update from the clone root:

```bash
.venv/bin/embeddington-setup --yes
```

This is the same Update the wizard runs interactively: it pulls code, re-syncs
dependencies only if packaging changed, brings container config current, applies data
diffs, and keeps the keyword index complete. Every step no-ops when it is already current,
and the user's data stays queryable throughout. A first run after a long gap may restore a
full baseline, which is a large download — say so if the output shows it happening rather
than letting the user wonder why it is taking minutes.

Then read the receipt back in plain language: what actually changed, and what it means for
them. In particular:

- **"One-time upgrades applied"** — enumerate them; these are the things that only happen
  once.
- **Code updated under `mcp/`** — their data works immediately, but Claude Desktop needs
  reopening to pick up new server code. Claude Code picks it up on its next run.
- **Nothing changed** — say that plainly. A quiet update is the normal case and is not a
  failure.

If the run ends on an `EMB-nn` code, quote the README's fix line for it. Do not re-run the
command hoping for a different result unless the code's own guidance says a retry is the
fix.
