---
description: "Check the local install's health and explain what it reports"
allowed-tools: Bash(.venv/bin/embeddington-setup --check)
---

# Install health check

Run the wizard's doctor from the clone root and interpret it for the user:

```bash
.venv/bin/embeddington-setup --check
```

It mutates nothing and exits 0 only when the install is healthy.

Explain the rows that are not ✓, in the user's terms rather than the table's:

- **containers / embed down** — the stack isn't running; `docker compose up -d` from
  `consumer/` brings it back, and the data in the volumes is untouched.
- **stores empty / cursor missing** — the graph was never imported, or the state directory
  moved. `/emb-update` is the fix.
- **mcp deps** — the server's dependencies are missing from the clone's own `.venv`, which
  is the interpreter the server actually runs under. Claude access is affected; the graph
  itself is fine.
- **mcp config** — no password is resolvable. The server reads `ARANGO_ROOT_PASSWORD` from
  `consumer/.env`, so this usually means that file is missing rather than anything about
  Claude.
- **mcp reach** — informational: whether the server is registered for every directory or
  only inside this clone.

Any `EMB-nn` code has a matching section in the README's troubleshooting table with a fix
line already attached; quote it rather than improvising. The doctor never repairs anything,
so if something is wrong, say what would fix it and let the user decide.
