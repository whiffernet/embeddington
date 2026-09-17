# Troubleshooting

> _"This is a very complicated case. A lotta ins, a lotta outs."_

Every installer *failure* prints an `[EMB-nn]` code with a fix line already attached.
Find yours below for the full story. If you are reporting a problem, send the run log —
the first section says where it is.

Back to the [README](../README.md).

---

## The run log

Every command the wizard runs — and the error from any that fails — is appended to
`~/.local/share/embeddington/run.log` (or `$EMBEDDINGTON_HOME/run.log`). So is the
Python environment bootstrap from `install.sh`, so a failed install and a failed run
land in one place. Each run opens with the revision it is about to run and whether the
code update succeeded, so the log answers "which version was this?" without anyone
having to ask:

```
=== embeddington run 2026-09-03T14:17:07-0700 ===
2026-09-03T14:17:07-0700  clone /Users/you/embeddington at v0.12.6 — code update: ok
```

A `code update: failed` there means the clone could not fast-forward and the run used
older code than you were expecting — `git -C <clone> status` shows what is in the way. The nightly update job writes there too, which is usually the only
way to find out that it has been failing quietly. It's trimmed to its last megabyte
each run, so it won't grow on you.

It lives outside the clone deliberately: reinstalling is the first thing people try, and
a log that a re-clone destroys is a log that is never there when you need it.

Nothing secret goes in it: no installer command carries a password on its command line,
the generated ArangoDB root password never leaves `consumer/.env`, and credentials
embedded in a `PIP_INDEX_URL` are stripped before anything is recorded. **If you're
reporting a problem, this is the file to send.**


### `SchemaVersionError: manifest schema major 4 exceeds supported 3; re-baseline`

> _"You're out of your element."_ — your client, politely, about a manifest it doesn't speak.

Your install predates the schema-4 release and the published manifest is now 4.0.0. **This is
the update refusing to run rather than applying a baseline it does not understand** — a
deliberate stop, not corruption. Nothing local is damaged, man.

The trailing number is whichever major *your* install speaks, so you may see `exceeds
supported 1` or `2` instead. The fix is the same either way — the ordinary update, which
pulls the newer code and then applies the baseline:

```bash
# run from: your clone
embeddington-setup --yes
```

Each major is a gate on a change an older client would get silently wrong:

- **Schema 3** is where baseline restores became **replace** rather than merge. A pre-3
  client applying a 3.0.0 baseline would have merged it into the existing collection,
  leaving two generations of every document.
- **Schema 4** is the move to KG schema **v3** — the graph now lives in `entities_v3` /
  `relationships_v3`, and the baseline entry names which generation it holds in a
  `kg_schema` field. A pre-4 client has no notion of that field, so it cannot name the
  collections the baseline actually contains: it would report a successful update while its
  MCP went on reading the v2 collections it already had. Serving yesterday's graph and
  calling it today's is worse than refusing, which is why the gate is here.

### "I installed it, but I don't see embeddington in Claude"

**First, the surface.** embeddington is an MCP server, not a plugin. It shows up under
**`/mcp`**, and it will never show up under `/plugin`. If that's where you looked, there is
nothing wrong with your install.

**Second, the directory.** The `.mcp.json` this repo ships is *project-scoped*: Claude Code
finds it when the clone is your project directory. Start Claude anywhere else and there is
nothing to see.

```bash
# run from: your clone
claude
```

**Still nothing?** Ask the client what it thinks, from the clone:

```bash
claude mcp list
```

Three answers, three unrelated problems:

| What it says | What it means |
| --- | --- |
| `embeddington … ⏸ Pending approval` | You were asked to approve it and didn't. Run `claude` there and accept — or `claude mcp reset-project-choices`, then relaunch, to be asked again. |
| `embeddington … ✘ Failed to connect` | Found, but it won't start. `embeddington-setup --check` names the reason; see **EMB-52**. |
| not listed at all | Claude isn't treating that directory as the project. Check where you launched it from. |

**Want it from every directory, not just the clone?** That's what the wizard's user-scope
offer is for — see the registration line under [query with Claude](#in-the-parlance-of-our-times-query-with-claude).

### EMB-10 — no interactive terminal

`install.sh` was piped without a TTY and `EMBEDDINGTON_YES` isn't set — it can't prompt
for anything. Run it from a real terminal, or set `EMBEDDINGTON_YES=1` for an
unattended install.

### EMB-11 — git missing

`git` isn't on `PATH`. Install it (`xcode-select --install` on macOS; `apt`/`dnf
install git` on Linux), then re-run.

### EMB-12 — python too old or missing

No `python3.13`, `python3.12`, or `python3` on `PATH` resolves to 3.12+. Install
Python 3.12 or newer (python.org, `brew install python@3.12`, or your distro), then
re-run.

### EMB-13 — can't reach the repo

`git ls-remote` against the clone URL failed — no network, or a proxy is in the way.
Check your connection, then re-run.

### EMB-14 — venv/pip bootstrap failed

Three distinct causes share this code, and `install.sh` tells you which: the
`python3-venv` package is missing (`sudo apt install python3-venv`, or
`python3.12-venv`, then re-run); a `pip install` step failed (the last 20 lines print
above the error, and the whole thing is in the run log — fix what it complains about,
then re-run); or
the clone is stale and `embeddington-setup` never landed (`cd` into the install dir,
`git stash && git pull --ff-only`, then re-run).

### EMB-15 — not enough disk

Preflight found less than 3 GB free. Free up at least 3 GB (12+ recommended), then
re-run.

### EMB-16 — install dir isn't empty and isn't a clone

The install directory exists, has files in it, and isn't an embeddington git clone —
`install.sh` won't overwrite something it doesn't recognize. Pick a different location
(`EMBEDDINGTON_INSTALL_DIR=...`), or move that directory aside.

### EMB-20 — docker install declined

No container runtime was found and every offer to install one was turned down (or,
in `--yes` mode, there was no one to ask — unattended mode never installs Docker
because it can't consent on your behalf). Install OrbStack, Colima, Docker Desktop,
or Docker Engine yourself, then re-run — or run interactively without
`EMBEDDINGTON_YES` so the wizard can offer.

### EMB-21 — docker daemon not reachable

The daemon didn't answer within the wait window, or it's up but your user can't
reach its socket yet (fresh Linux installs aren't in the `docker` group by default —
the wizard offers `usermod -aG docker`, but that only takes effect after you log out
and back in, or run `newgrp docker`). Start the daemon manually (OrbStack/Docker
Desktop, `colima start`, or `sudo systemctl start docker`) or re-login, then re-run.

The error now carries what the client was actually doing — the socket it dialed, the
active docker context, and the other contexts you have configured:

```
  dialed: unix:///Users/you/.docker/run/docker.sock
  context: desktop-linux (active)
  also configured: default, orbstack
  docker said: Cannot connect to the Docker daemon. Is the docker daemon running?
```

**If your runtime is plainly running and you still see this, read the context line.**
Migrating from Docker Desktop to OrbStack (or Colima, or Rancher) leaves the old
context selected, so the client keeps dialing a socket nothing owns any more while
your actual daemon sits there healthy. `docker context use orbstack` — or whichever
one the error lists — fixes it. A `DOCKER_HOST` exported in your shell profile beats
the context entirely, and is reported on its own line when set.

### EMB-22 — manual runtime install required

The wizard can't finish this install path for you — no Homebrew to install OrbStack
with, an OrbStack brew install that failed, Colima's three-step manual setup, or
Docker Desktop (a GUI download it can't script). Follow the printed steps or install
a runtime yourself, then re-run.

### EMB-23 — automatic docker install failed or unsupported

Either the `docker compose` v2 plugin is missing after an otherwise-working Docker
install, the Linux distro wasn't recognized so the wizard wouldn't guess a package
manager, or the recognized distro's package install command failed. Install Docker
Engine + the compose plugin per
[docs.docker.com/engine/install](https://docs.docker.com/engine/install/), then
re-run.

### EMB-24 — port already taken

A port `consumer/docker-compose.yml` needs is bound by something that isn't
embeddington. Stop whatever holds that port (or move it), then re-run.

### EMB-31 — docker compose up failed

Either `docker compose up -d --build` exited non-zero (the error prints just above),
or Qdrant/ArangoDB didn't answer within the store timeout. Fix what compose
complained about (ports, disk, daemon) — or check `docker compose ps` and
`docker compose logs` in `consumer/` — then re-run; it picks up where it left off.

You don't have to catch it live. When compose fails, the service states and the last
100 lines of container output are written to the run log, so a container that started
and then died leaves its reason behind — the low-RAM case where arango won't stay up
being the common one. **That's the file to send if you want a hand.** (An *image build*
failure is the exception: it happens before any container exists, so only the terminal
output above has it.)

**Not the same as `unknown`.** If `embeddington-setup --check` reports the containers (or
`embed`) as **`unknown — Docker isn't answering`**, that is not this error: the daemon never
replied, so nothing could be asked about the containers at all. Start Docker — open
OrbStack/Docker Desktop, `colima start`, or `sudo systemctl start docker` — and re-run the
check. The containers may well be fine underneath.

### EMB-32 — embed service didn't come up

The `embed` service's first build downloads ~2 GB of model weights, and that stalled
or failed past the embed timeout. Run `docker compose logs embed` in `consumer/` to
see why; a plain retry (`docker compose up -d --build`) resumes a dropped download
cleanly.

### EMB-33 — no usable ArangoDB password

`consumer/.env` either doesn't exist, or exists but its `ARANGO_ROOT_PASSWORD` is
empty or still the placeholder `change-me`. Re-run the installer to generate one, or
open the file and set `ARANGO_ROOT_PASSWORD` to any non-empty value yourself.

### EMB-41 — download failed (network)

A baseline or diff download hit a network error. Check your connection and re-run —
downloads resume/retry cleanly.

### EMB-42 — asset checksum mismatch

A downloaded asset failed checksum verification. Re-run — a corrupted download
re-fetches cleanly. If it repeats, open an issue.

### EMB-43 — populated store with no cursor

The stores already hold data and no cursor was found, so the updater refuses to
guess whether a full re-restore is safe (this is the same guard `embeddington-consume
update` exits `3` for). If the store is healthy, copy your old cursor into the state
dir (see **Configuration** in the [README](../README.md)); to deliberately re-restore everything, re-run with
`--force-baseline`.

### EMB-44 — proof-of-life query returned zero

After import, a real query against Qdrant and ArangoDB found at least one store
empty or unqueryable. Give the containers a few seconds to settle and re-run
`embeddington-setup --check`; if it persists, check `docker compose logs` in
`consumer/`, or run `embeddington-consume update --force-baseline` for a clean
restore.

### EMB-45 — updater error

The updater hit something other than a network, checksum, or guard failure (a chain
gap, a schema version mismatch, ...). Re-run the installer; if it repeats, run
`embeddington-consume update` directly for the full error.

### EMB-51 — MCP dependency install failed

`pip install -r mcp/requirements.txt` failed while wiring up Claude — the graph
itself is unaffected and fully usable without it. Run that `pip install` manually with
the clone's own interpreter (`.venv/bin/pip`) to see why.

### EMB-52 — the MCP server didn't start when probed

After wiring Claude, the installer starts the server once to prove it works, instead of
assuming it does. This code means that probe failed, and the message names which of the
few possible causes it was: the clone's `.venv` is missing, the server's dependencies
aren't installed in it, no password is resolvable (`consumer/.env` is missing or empty and
`mcp/.env` doesn't supply one), or the local stack isn't answering (which is the stack
being down, not the wiring being wrong).

Your knowledge graph is unaffected either way — this step only concerns querying it from
Claude. To watch the failure yourself:

```bash
# run from: repo root
.venv/bin/python mcp/server.py < /dev/null
```

A healthy server prints a startup line and exits cleanly when its input closes. Any other
outcome prints the real reason, which your client would otherwise report only as a closed
connection.

### EMB-61 — couldn't inspect store contents before deletion

Uninstall couldn't query the stores (daemon down?) before offering to delete their
volumes, so it can't prove they hold only embeddington data. This is a non-fatal
warning, not a stopper. For an inspected deletion: `cd consumer && docker compose up
-d`, then re-run the uninstall — or proceed knowing the contents are unverified.

### EMB-62 — crontab rewrite failed

Uninstall couldn't rewrite your crontab to strip the embeddington line. Run
`crontab -e` and remove the line yourself.

### EMB-63 — clone self-delete handoff failed

Uninstall hands off to a tiny detached script to delete the clone (so the running
Python process isn't deleting the directory it's executing from); the handoff
`execv` itself failed. Remove the clone yourself: `rm -rf <clone path>`.

---
