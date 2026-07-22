"""Tests for the vendored noise ruleset (spec §4/M1, §5/U5).

GENERIC_NAMES / JUNK_PATTERNS are copied verbatim from the producer's
cleanup.py. A cross-repo pin test is impossible (private repo, absent from this
checkout), so these pin the vendored copy's SHAPE and document the re-sync
obligation.
"""

import ontology_rules as R


def test_vendored_generic_names_matches_source_size():
    # cleanup.py @ 0b650e6 defines 41 generic names. A failure means the
    # vendored copy has drifted from source and must be re-synced.
    assert len(R.GENERIC_NAMES) == 41
    assert all(n == n.lower() for n in R.GENERIC_NAMES)


def test_vendored_junk_patterns_matches_source_size():
    assert len(R.JUNK_PATTERNS) == 5


def test_provenance_is_recorded():
    assert R.VENDORED_FROM_COMMIT == "0b650e6"
    assert "cleanup.py" in R.VENDORED_FROM_FILE


def test_classify_generic_name():
    assert R.classify_noise("configure") == "generic"
    assert R.classify_noise("Overview") == "generic"


def test_classify_junk_patterns():
    assert R.classify_noise("The incident table") == "junk_pattern"
    assert R.classify_noise("How to configure Discovery") == "junk_pattern"
    assert R.classify_noise("See the release notes") == "junk_pattern"
    assert R.classify_noise("42") == "junk_pattern"
    assert R.classify_noise("true") == "junk_pattern"


def test_classify_too_short():
    assert R.classify_noise("ci") == "too_short"
    assert R.classify_noise("a") == "too_short"


def test_classify_dotted_alias_including_uppercase_tails():
    """The motivating prod case has an UPPERCASE segment after the dot.

    An earlier draft used ^[a-z0-9_]+\\.[a-z0-9_]+\\.?[A-Za-z], whose middle
    group is lowercase-only, so it did NOT match sn_azure_ad_spoke.AzureAD —
    the one example the rule exists for.
    """
    assert R.classify_noise("sn_azure_ad_spoke.AzureAD") == "dotted_alias"
    assert R.classify_noise("com.snc.discovery.Pattern") == "dotted_alias"
    assert R.classify_noise("global.ChannelSendToValidation") == "dotted_alias"


def test_classify_generic_word():
    assert R.classify_noise("Windows") == "generic_word"
    assert R.classify_noise("Search") == "generic_word"


def test_real_entities_are_not_noise():
    """Verified-real prod entity names that must survive classification.

    The parenthesised plugin names are the interesting cases: they contain
    dotted identifiers but start with a capital, so DOTTED_ALIAS correctly
    declines them.
    """
    for name in (
        "Now Assist in Virtual Agent",
        "Service Mapping plugin (com.snc.service-mapping)",
        "Discovery (com.snc.discovery)",
        "cmdb_rel_ci",
        "Customer Service Management",
    ):
        assert R.classify_noise(name) is None, name
