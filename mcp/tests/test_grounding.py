"""Unit tests for the grounding classifier (spec §5 PR 5, issue #47)."""

import grounding


def _chunk(text="some prose", score=0.7):
    return {"id": "c1", "score": score, "text": text}


def _match(edges):
    return {"concept": "x", "edges": edges, "nodes": []}


def _edge(quote="a fact"):
    return {"id": "e1", "predicate": "P", "source_quote": quote}


class TestNone:
    def test_nothing_anywhere_is_none(self):
        g = grounding.classify([], [_match([])], [])
        assert g["tier"] == "none"
        assert grounding.REASON_NO_CHUNKS in g["reasons"]
        assert grounding.REASON_NO_KG in g["reasons"]

    def test_no_matches_list_at_all_is_none(self):
        g = grounding.classify([], [], [])
        assert g["tier"] == "none"


class TestWeak:
    def test_identifier_absent_from_all_content_is_weak(self):
        # The recorded incident class: on-topic content, asked-for id missing.
        g = grounding.classify(
            [_chunk("about hardware asset tables generally")],
            [_match([_edge("assets are tracked")])],
            ["sn_zz_fake_table"],
        )
        assert g["tier"] == "weak"
        assert any("sn_zz_fake_table" in r and "not found" in r for r in g["reasons"])

    def test_identifier_in_chunk_text_is_not_weak(self):
        g = grounding.classify(
            [_chunk("the sn_hamp_asset table stores hardware")],
            [_match([_edge()])],
            ["sn_hamp_asset"],
        )
        assert g["tier"] == "ok"

    def test_identifier_in_edge_quote_counts_as_grounded(self):
        g = grounding.classify(
            [_chunk("general prose")],
            [_match([_edge("sn_hamp_asset holds rows")])],
            ["sn_hamp_asset"],
        )
        assert g["tier"] == "ok"

    def test_identifier_match_is_case_insensitive(self):
        g = grounding.classify(
            [_chunk("The SN_HAMP_Asset table")], [_match([_edge()])], ["sn_hamp_asset"]
        )
        assert g["tier"] == "ok"

    def test_chunks_without_kg_is_weak(self):
        g = grounding.classify([_chunk()], [_match([])], [])
        assert g["tier"] == "weak"
        assert grounding.REASON_KG_EMPTY in g["reasons"]

    def test_kg_without_chunks_is_weak(self):
        g = grounding.classify([], [_match([_edge()])], [])
        assert g["tier"] == "weak"
        assert grounding.REASON_NO_CHUNKS in g["reasons"]

    def test_one_missing_one_found_identifier_is_weak_naming_only_missing(self):
        g = grounding.classify(
            [_chunk("has cmdb_rel_ci here")],
            [_match([_edge()])],
            ["cmdb_rel_ci", "sn_zz_fake_table"],
        )
        assert g["tier"] == "weak"
        assert any("sn_zz_fake_table" in r for r in g["reasons"])
        assert not any("cmdb_rel_ci" in r for r in g["reasons"])


class TestOk:
    def test_both_halves_present_no_tokens_is_ok_with_empty_reasons(self):
        g = grounding.classify([_chunk()], [_match([_edge()])], [])
        assert g == {"tier": "ok", "reasons": []}
