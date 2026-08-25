"""Where the Arango password comes from, and in what order.

The installer deliberately does NOT copy the password into mcp/.env: the consumer
stack's .env already holds it, 0600, written at install time. Duplicating a credential
onto disk to satisfy a config file is a worse trade than reading the one that exists.
Precedence: process env > mcp/.env (both already merged into the environment by the time
these run) > consumer/.env.
"""

import server


def _consumer_env(tmp_path, body):
    path = tmp_path / ".env"
    path.write_text(body)
    return path


def test_reads_the_consumer_stack_password(tmp_path):
    path = _consumer_env(tmp_path, "ARANGO_ROOT_PASSWORD=from-consumer\n")
    assert server._password_from_consumer_env(path) == "from-consumer"


def test_absent_file_is_not_an_error(tmp_path):
    assert server._password_from_consumer_env(tmp_path / "nope.env") is None


def test_empty_assignment_counts_as_unset(tmp_path):
    path = _consumer_env(tmp_path, "ARANGO_ROOT_PASSWORD=\n")
    assert server._password_from_consumer_env(path) is None


def test_an_existing_password_always_wins(tmp_path):
    """mcp/.env and claude_desktop_config.json land in the environment before this runs;
    neither may be silently replaced by the consumer stack's credential."""
    path = _consumer_env(tmp_path, "ARANGO_ROOT_PASSWORD=from-consumer\n")
    assert server._fallback_arango_password({"ARANGO_PASSWORD": "explicit"}, path) is None


def test_an_empty_password_does_not_shadow_a_usable_one(tmp_path):
    """`cp .env.example .env` leaves ARANGO_PASSWORD= behind — the exact shape that used
    to leave the server dead with a working password sitting one directory away."""
    path = _consumer_env(tmp_path, "ARANGO_ROOT_PASSWORD=from-consumer\n")
    assert server._fallback_arango_password({"ARANGO_PASSWORD": "   "}, path) == "from-consumer"


def test_nothing_anywhere_stays_none(tmp_path):
    """server.main() then exits with its own clear message, which is the right outcome."""
    assert server._fallback_arango_password({}, tmp_path / "nope.env") is None
