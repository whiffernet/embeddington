"""Uninstall: consent gates, gate ORDER, --yes semantics, rc-checked removals.

Interactive prompt order (all items present): cron -> containers -> embed_models ->
[foreign acknowledgment] -> data volumes (typed) -> state dir -> clone (typed).
Every answer list below is written against that exact order — a new prompt anywhere
shifts the sequences, and the with-cron test exists to catch exactly that.

Gate-presence pins use console OUTPUT (the typed-gate prompt text must not appear when
acknowledgment was declined): asserting only on surviving volumes is vacuous, because
a later "n" also keeps them.
"""

import io
import tempfile

from rich.console import Console

from installer import uninstall
from installer.cron import cron_line
from installer.runner import RunResult
from tests.installer.conftest import FakeHttp

KNOWN_QDRANT = (200, '{"result": {"collections": [{"name": "technology"}]}}')
FOREIGN_QDRANT = (
    200,
    '{"result": {"collections": [{"name": "technology"}, {"name": "my_precious_data"}]}}',
)
TYPED_GATE_TEXT = "cannot be undone"
ACK_TEXT = "Acknowledge"


class MapRun:
    """Run fake keyed on command-prefix; unmatched commands succeed with rc 0."""

    def __init__(self, mapping=None):
        self.calls = []
        self.mapping = dict(mapping or {})

    def __call__(self, cmd, *, cwd=None, env=None, timeout=None, stream=False):
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "stream": stream})
        for prefix, res in self.mapping.items():
            if " ".join(cmd).startswith(prefix):
                return res
        return RunResult(0, "", "")


def make_repo(tmp_path):
    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("ARANGO_ROOT_PASSWORD=pw\n")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / ".cursor").write_text("abc\n")
    return tmp_path


def drive(
    tmp_path,
    answers,
    *,
    qdrant=KNOWN_QDRANT,
    dbs=("technology_kg", "_system"),
    assume_yes=False,
    really=False,
    crontab="",
    run=None,
):
    """Run run_uninstall fully faked; returns (rc, recorder, console_output)."""
    repo = make_repo(tmp_path)
    recorder = {"rmtree": [], "execv": []}
    console = Console(file=io.StringIO(), force_terminal=False, width=200)
    it = iter(answers)
    rc = uninstall.run_uninstall(
        console,
        run if run is not None else MapRun(),
        repo,
        assume_yes=assume_yes,
        really_delete_data=really,
        env={"EMBEDDINGTON_HOME": str(repo / "state")},
        home=repo,
        http_get=FakeHttp({":6333/collections": qdrant}),
        list_databases=lambda: list(dbs),
        crontab_text=crontab,
        input_fn=lambda: next(it),
        rmtree=lambda p: recorder["rmtree"].append(str(p)),
        execv=lambda *a: recorder["execv"].append(a),
        mkstemp=lambda suffix="", prefix="": tempfile.mkstemp(
            suffix=suffix, prefix=prefix, dir=tmp_path
        ),
    )
    return rc, recorder, console.file.getvalue()


def joined(run):
    return [" ".join(c["cmd"]) for c in run.calls]


# --- interactive gates ---------------------------------------------------------------


def test_all_declined_removes_nothing(tmp_path):
    run = MapRun()
    rc, rec, _ = drive(tmp_path, ["n", "n", "n", "n", "n"], run=run)
    assert rc == 0
    assert rec["rmtree"] == [] and rec["execv"] == []
    assert not any("compose down" in c or "volume rm" in c for c in joined(run))


def test_typed_delete_removes_data_volumes_with_exact_names(tmp_path):
    run = MapRun()
    drive(tmp_path, ["n", "n", "delete", "n", "n"], run=run)
    assert any(
        c["cmd"] == ["docker", "volume", "rm", "consumer_qdrant_storage", "consumer_arango_data"]
        for c in run.calls
    )


def test_y_at_the_typed_gate_keeps_data(tmp_path):
    run = MapRun()
    drive(tmp_path, ["n", "n", "y", "n", "n"], run=run)
    assert not any("volume rm" in c and "qdrant_storage" in c for c in joined(run))


def test_interactive_clone_requires_typed_delete_not_y(tmp_path):
    run = MapRun()
    _, rec, _ = drive(tmp_path, ["n", "n", "n", "n", "y"], run=run)
    assert rec["execv"] == []  # "y" is not "delete"


def test_state_dir_prompt_warns_about_the_guard_when_data_kept(tmp_path):
    _, rec, out = drive(tmp_path, ["n", "n", "n", "y", "n"])
    assert rec["rmtree"]  # state dir removed on consent
    assert "EMB-43" in out  # ...but only after the keeping-the-graph warning


# --- the foreign-data acknowledgment gate --------------------------------------------


def test_declined_acknowledgment_never_even_offers_the_typed_gate(tmp_path):
    # [CRITIC] Pinned via OUTPUT: if the ack gate were deleted, the typed-gate prompt
    # would appear (and consume "delete"); asserting only on kept volumes is vacuous.
    run = MapRun()
    _, _, out = drive(tmp_path, ["n", "n", "n", "delete", "n"], qdrant=FOREIGN_QDRANT, run=run)
    assert "my_precious_data" in out
    assert TYPED_GATE_TEXT not in out
    assert not any("volume rm" in c and "qdrant_storage" in c for c in joined(run))


def test_acknowledgment_precedes_the_typed_gate_and_then_deletes(tmp_path):
    run = MapRun()
    _, _, out = drive(tmp_path, ["n", "n", "y", "delete", "n", "n"], qdrant=FOREIGN_QDRANT, run=run)
    assert out.index(ACK_TEXT) < out.index(TYPED_GATE_TEXT)  # gate ORDER is load-bearing
    assert any("volume rm" in c and "qdrant_storage" in c for c in joined(run))


def test_foreign_arango_database_is_named(tmp_path):
    _, foreign = uninstall.inspect_stores(
        FakeHttp({":6333/collections": KNOWN_QDRANT}),
        lambda: ["technology_kg", "_system", "secret_side_project"],
    )
    assert foreign == ["arango db: secret_side_project"]


def test_uninspectable_stores_show_emb61_but_still_gate(tmp_path):
    run = MapRun()
    _, _, out = drive(tmp_path, ["n", "n", "delete", "n", "n"], qdrant=(500, ""), run=run)
    assert "EMB-61" in out  # the warning has its registered code, not a bare sentence
    assert any("volume rm" in c and "qdrant_storage" in c for c in joined(run))


# --- unattended (--yes) semantics ----------------------------------------------------


def explode():
    raise AssertionError("a --yes run must never read input")


def test_unattended_with_foreign_data_never_prompts_and_keeps_volumes(tmp_path):
    run = MapRun()
    rc, rec, out = drive(tmp_path, [], qdrant=FOREIGN_QDRANT, assume_yes=True, really=True, run=run)
    assert rc == 0
    assert not any("volume rm" in c and "qdrant_storage" in c for c in joined(run))
    assert "my_precious_data" in out and "KEPT" in out


def test_yes_without_really_delete_data_keeps_data_and_clone(tmp_path):
    run = MapRun()
    rc, rec, _ = drive(tmp_path, [], assume_yes=True, really=False, run=run)
    assert rc == 0
    assert any("compose down" in c for c in joined(run))  # safe items still removed
    assert not any("volume rm" in c and "qdrant_storage" in c for c in joined(run))
    assert rec["execv"] == []


def test_yes_with_really_removes_everything_when_clean(tmp_path):
    run = MapRun()
    rc, rec, _ = drive(tmp_path, [], assume_yes=True, really=True, run=run)
    cmds = joined(run)
    assert any("compose down" in c for c in cmds)
    assert any("volume rm" in c and "arango_data" in c for c in cmds)
    # Order pin: containers stop BEFORE their volumes are removed.
    down_idx = next(i for i, c in enumerate(cmds) if "compose down" in c)
    rm_idx = next(i for i, c in enumerate(cmds) if "volume rm" in c and "arango_data" in c)
    assert down_idx < rm_idx
    assert rec["rmtree"] and rec["execv"]


def test_unattended_dirty_clone_is_kept(tmp_path):
    run = MapRun({"git status": RunResult(0, " M consumer/.env\n?? my_notes.md\n", "")})
    rc, rec, out = drive(tmp_path, [], assume_yes=True, really=True, run=run)
    assert rec["execv"] == []  # untracked files are user data — no unattended deletion
    assert "my_notes.md" in out


def test_no_shared_runtime_removal_commands_ever(tmp_path):
    run = MapRun()
    drive(tmp_path, [], assume_yes=True, really=True, run=run)
    for c in joined(run):
        assert not c.startswith(
            ("brew uninstall", "brew remove", "apt-get remove", "dnf remove", "apt remove")
        )


# --- rc-checking, names, cron, dirty listing ------------------------------------------


def test_volume_rm_failure_lands_in_failed_not_removed(tmp_path):
    run = MapRun({"docker volume rm": RunResult(1, "", "volume is in use")})
    _, _, out = drive(tmp_path, ["n", "n", "delete", "n", "n"], run=run)
    assert "FAILED" in out
    removed_line = next(line for line in out.splitlines() if "Removed:" in line)
    assert "data_volumes" not in removed_line


def test_resolved_volume_names_respect_a_custom_project_prefix(tmp_path):
    run = MapRun(
        {
            "docker volume ls": RunResult(
                0, "myproj_qdrant_storage\nmyproj_arango_data\nmyproj_embed_models\n", ""
            )
        }
    )
    drive(tmp_path, ["n", "n", "delete", "n", "n"], run=run)
    assert any("volume rm" in c and "myproj_qdrant_storage" in c for c in joined(run))


def test_resolved_volume_names_are_shown_at_the_gate_before_deletion(tmp_path):
    # [CRITIC] resolve_volume_names suffix-matches and can over-match a second compose
    # project's stopped volume; the typed-delete prompt and the receipt must both name
    # the resolved list so an over-match is visible before AND after deletion, not just
    # discoverable by re-running `docker volume ls`.
    run = MapRun(
        {
            "docker volume ls": RunResult(
                0, "myproj_qdrant_storage\nmyproj_arango_data\nmyproj_embed_models\n", ""
            )
        }
    )
    _, _, out = drive(tmp_path, ["n", "n", "delete", "n", "n"], run=run)
    pre_delete_out = out[: out.index(TYPED_GATE_TEXT)]
    assert "myproj_qdrant_storage" in pre_delete_out
    assert "myproj_arango_data" in pre_delete_out
    removed_line = next(line for line in out.splitlines() if "Removed:" in line)
    assert "myproj_qdrant_storage" in removed_line
    assert "myproj_arango_data" in removed_line


def test_with_cron_line_the_cron_prompt_comes_first(tmp_path):
    # Pins prompt POSITION: the cron offer must consume the first answer, or every
    # later gate shifts by one on real boxes that carry the cron line.
    cron = "0 6 * * * cd $HOME/embeddington && .venv/bin/embeddington-consume update\n"
    run = MapRun()
    drive(tmp_path, ["y", "n", "n", "n", "n", "n"], crontab=cron, run=run)
    assert any(c["cmd"][0] == "crontab" and len(c["cmd"]) == 2 for c in run.calls)
    assert not any("compose down" in c for c in joined(run))  # its answer was the 2nd "n"


def test_with_new_form_cron_line_the_cron_prompt_is_still_offered(tmp_path):
    # Dual-marker gap (critique): the manifest gate used to check only the legacy
    # substring, so a crontab holding ONLY the new self-upgrading line was never even
    # offered for removal. Mirrors test_with_cron_line_the_cron_prompt_comes_first but
    # with the new-form line as the ONLY embeddington line present.
    cron = cron_line(str(tmp_path / "embeddington")) + "\n"
    run = MapRun()
    drive(tmp_path, ["y", "n", "n", "n", "n", "n"], crontab=cron, run=run)
    assert any(c["cmd"][0] == "crontab" and len(c["cmd"]) == 2 for c in run.calls)
    assert not any("compose down" in c for c in joined(run))  # its answer was the 2nd "n"


def test_crontab_line_is_removed_surgically():
    cron = (
        "MAILTO=e\n"
        "0 6 * * * cd $HOME/embeddington && .venv/bin/embeddington-consume update\n"
        "0 7 * * * /usr/bin/backup.sh\n"
    )
    kept = uninstall.strip_cron_lines(cron)
    assert "embeddington-consume" not in kept
    assert "backup.sh" in kept and "MAILTO=e" in kept


def test_dirty_files_returns_first_twenty_and_the_total(tmp_path):
    lines = "\n".join(f"?? file_{i}.txt" for i in range(25)) + "\n"
    run = MapRun({"git status": RunResult(0, lines, "")})
    names, total = uninstall.dirty_files(run, tmp_path)
    assert len(names) == 20 and total == 25
    assert "file_0.txt" in names


def test_receipt_includes_the_clone_fate(tmp_path):
    _, _, out = drive(tmp_path, ["n", "n", "n", "n", "n"])
    removed_or_kept = out[out.index("Removed:") :]
    assert "clone" in removed_or_kept  # printed AFTER the clone decision
