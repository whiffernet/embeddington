"""Unit tests for the pure hybrid-retrieval helpers (spec §5 PR 4)."""

import hybrid


class TestExtractIdentifierTokens:
    def test_snake_case_and_dotted(self):
        q = "What does the cmdb_rel_ci table store, and what does com.snc.discovery activate?"
        assert hybrid.extract_identifier_tokens(q) == ["cmdb_rel_ci", "com.snc.discovery"]

    def test_plain_prose_yields_nothing(self):
        assert hybrid.extract_identifier_tokens("Explain Incident in ServiceNow.") == []

    def test_single_segment_words_excluded(self):
        # 'discovery' and 'incident.' (sentence dot) are not identifiers.
        assert hybrid.extract_identifier_tokens("Does discovery scan incident. Yes.") == []

    def test_dedup_case_and_order_and_cap(self):
        q = "sn_hamp_one sn_hamp_two SN_HAMP_ONE a.b c.d e_f_g extra_one_more"
        toks = hybrid.extract_identifier_tokens(q)
        assert toks == ["sn_hamp_one", "sn_hamp_two", "a.b"]  # deduped, ordered, capped at 3


class TestRrfMerge:
    def _c(self, cid, score=0.5):
        return {"id": cid, "score": score, "text": f"t-{cid}"}

    def test_item_in_both_lanes_outranks_single_lane_leader(self):
        dense = [self._c("a"), self._c("b"), self._c("c")]
        lex = [self._c("b"), self._c("d")]
        fused = hybrid.rrf_merge([dense, lex])
        # b: rank 2 in dense + rank 1 in lex -> 1/(60+2)+1/(60+1); a: 1/(60+1) only.
        assert fused[0]["id"] == "b"
        assert {x["id"] for x in fused} == {"a", "b", "c", "d"}

    def test_dedup_keeps_first_occurrence_fields(self):
        dense = [{"id": "a", "score": 0.9, "text": "dense-a"}]
        lex = [{"id": "a", "score": 0.1, "text": "lex-a"}]
        fused = hybrid.rrf_merge([dense, lex])
        assert len(fused) == 1 and fused[0]["text"] == "dense-a"

    def test_limit_truncates(self):
        dense = [self._c(str(i)) for i in range(5)]
        assert len(hybrid.rrf_merge([dense], limit=2)) == 2

    def test_empty_lanes(self):
        assert hybrid.rrf_merge([[], []]) == []

    def test_deterministic_tie_break_on_id(self):
        a = hybrid.rrf_merge([[self._c("b")], [self._c("a")]])
        b = hybrid.rrf_merge([[self._c("a")], [self._c("b")]])
        assert [x["id"] for x in a] == [x["id"] for x in b] == ["a", "b"]


class TestApplyThreshold:
    def test_drops_below_threshold(self):
        chunks = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.2}]
        assert [c["id"] for c in hybrid.apply_threshold(chunks, 0.5)] == ["a"]

    def test_zero_threshold_noop(self):
        chunks = [{"id": "a", "score": -1.0}]
        assert hybrid.apply_threshold(chunks, 0.0) == chunks

    def test_can_return_empty(self):
        assert hybrid.apply_threshold([{"id": "a", "score": 0.1}], 0.9) == []
