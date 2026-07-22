"""Noise-detection ruleset for ontology metrics (spec §4/M1).

GENERIC_NAMES and JUNK_PATTERNS are copied VERBATIM from the producer repo's
junk-entity classifier so both layers agree on what "junk" means. Two layers
disagreeing about the same entity is a defect.

    Source: whiffernet/langchain
            my_agent/knowledge_graph/cleanup.py @ 0b650e6

RE-SYNC OBLIGATION: a cross-repo pin test is impossible — the producer repo is
private and absent from this checkout. If cleanup.py's ruleset changes, update
this copy by hand and bump test_ontology_rules.py's size assertions.
Consolidating the three copies (cleanup.py, qa_checks.py, this one) is a named
Round B item.

DOTTED_ALIAS is the one rule NOT vendored — neither producer-side classifier
pattern-matches dotted identifiers, and that class
(`sn_azure_ad_spoke.AzureAD`) is the documented cause of embeddington's only v1
bake-off loss.
"""

import re

from ontology_frozen import MAX_GENERIC_WORD_CHARS, MIN_NAME_CHARS

VENDORED_FROM_FILE = "my_agent/knowledge_graph/cleanup.py"
VENDORED_FROM_COMMIT = "0b650e6"

GENERIC_NAMES: frozenset[str] = frozenset(
    {
        "configure",
        "set up",
        "setup",
        "enable",
        "disable",
        "update",
        "create",
        "delete",
        "manage",
        "view",
        "edit",
        "add",
        "remove",
        "install",
        "activate",
        "deactivate",
        "test",
        "run",
        "start",
        "stop",
        "check",
        "verify",
        "apply",
        "use",
        "open",
        "close",
        "overview",
        "introduction",
        "summary",
        "description",
        "details",
        "information",
        "note",
        "notes",
        "example",
        "examples",
        "prerequisite",
        "prerequisites",
        "procedure",
        "result",
        "results",
    }
)

JUNK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^(the|a|an|this|that|these|those)\s", re.IGNORECASE),
    re.compile(r"^(how to|steps to|guide to)\s", re.IGNORECASE),
    re.compile(r"^(see|refer|click|navigate)\s", re.IGNORECASE),
    re.compile(r"^\d+$"),
    re.compile(r"^(true|false|yes|no|none|null|n/a)$", re.IGNORECASE),
)

# A lowercase-rooted dotted identifier with one or more dotted segments of ANY
# case: "sn_azure_ad_spoke.AzureAD", "com.snc.discovery.Pattern".
#
# The leading segment must be lowercase — that is what distinguishes a machine
# identifier from prose containing a dotted token, so
# "Service Mapping plugin (com.snc.service-mapping)" is correctly NOT matched.
#
# KNOWN CATEGORY OVERLAP, accepted deliberately: version strings such as
# "v7.0.1" also match. They are genuinely noise as entity names, so the noise
# VERDICT is right even though "dotted_alias" is the wrong label for them. The
# metric counts noise, not taxonomy, so this is recorded rather than fixed.
DOTTED_ALIAS: re.Pattern = re.compile(r"^[a-z0-9_]+(\.[A-Za-z0-9_]+)+")


def classify_noise(name: str) -> str | None:
    """Classify an entity name as a noise category, or None if it looks real.

    Order matters: the most specific rules run first so a name matching several
    categories reports the most informative one.

    Args:
        name: The raw entity name as stored in ``entities_v2``.

    Returns:
        One of "generic", "junk_pattern", "too_short", "dotted_alias",
        "generic_word", or None when the name shows no noise smell.
    """
    stripped = name.strip()
    if not stripped:
        return "too_short"

    if stripped.casefold() in GENERIC_NAMES:
        return "generic"

    for pattern in JUNK_PATTERNS:
        if pattern.search(stripped):
            return "junk_pattern"

    if len(stripped) < MIN_NAME_CHARS:
        return "too_short"

    if DOTTED_ALIAS.match(stripped):
        return "dotted_alias"

    if (
        len(stripped.split()) == 1
        and len(stripped) <= MAX_GENERIC_WORD_CHARS
        and not re.search(r"[._\-]", stripped)
    ):
        return "generic_word"

    return None
