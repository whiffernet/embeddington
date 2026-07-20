# PR 5 evidence — empty/weak-retrieval guard (#47)

The `enrich` envelope now carries `grounding: {tier: "ok"|"weak"|"none", reasons: [...]}`,
classified from the FINAL (post-ceiling-trim) response content by the pure
`mcp/grounding.py` classifier. The tool description instructs callers: on `none`/`weak`,
say what was not found rather than answering from prior knowledge — never present an
identifier that is not in the returned content.

## Tier semantics (the classifier's pinned contract)

| tier | condition                                                                                                                                 | reasons                                                                                    |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| none | zero post-threshold chunks AND zero KG edges                                                                                              | both constants ("no vector chunks cleared the score threshold", "no KG concepts resolved") |
| weak | not none, AND (an extracted identifier appears literally in NO returned chunk text or edge quote, OR exactly one retrieval half is empty) | names the missing identifier(s) and/or the empty half                                      |
| ok   | otherwise                                                                                                                                 | `[]`                                                                                       |

## Live gates (battery stack, shipped defaults, warm cache — live-verified, labeled as such)

| probe                                                         | tier     | chunks/edges | reasons (abridged)                                                                                       | fake id in payload |
| ------------------------------------------------------------- | -------- | ------------ | -------------------------------------------------------------------------------------------------------- | ------------------ |
| nonsense ("purple elephant quantum bicycle recipes")          | **none** | 0 / 0        | both none-constants                                                                                      | —                  |
| **incident class** ("What is the sn_zz_fake_table used for?") | **weak** | 5 / 0        | "identifier(s) sn_zz_fake_table not found in any returned content", "KG returned nothing for this query" | **False**          |
| fixed-11: "Explain CMDB in ServiceNow."                       | ok       | 3 / 31       | []                                                                                                       | False              |
| fixed-11: "What roles does Discovery require?"                | ok       | 5 / 30       | []                                                                                                       | False              |
| identifier cohort: pm_project                                 | ok       | 4 / 22       | []                                                                                                       | False              |

The second row is issue #47's recorded incident class reproduced and guarded: on-topic
chunks came back (5 of them), the asked-for table does not exist, and the envelope now
says so explicitly instead of presenting a full-looking result. The identifier appears
nowhere in the returned content — the regression test
`test_enrich_grounding_weak_when_asked_identifier_absent` pins this class, and
`test_grounding_reflects_post_trim_not_pre_trim_content` pins that classification
happens on what the caller actually receives (an order-swap mutant fails it).

## Interplay + honest scope

- Classification is observation-only: no selection, threshold, or lane behavior changed
  in this PR (diff touches enrich envelope assembly, the classifier module, the tool
  description, and tests only).
- Lexical-lane degradation stays an independent signal: the classifier judges returned
  content, which is the honest basis whether or not the index was ready.
- The threshold shipped in #38 is the mechanism that makes `none` possible (weak chunks
  are dropped, not padded); this PR labels the outcome.
- Scope: the MCP guarantees **its own output** — every identifier it returns comes from a
  retrieved chunk or KG record. It cannot control what a downstream LLM says; the guard
  removes the padded-input condition behind the recorded fabrication and hands the
  caller an explicit signal plus instructions. `vector_search` still signals only via
  fewer results (no warnings channel — recorded follow-up from PR 4's review).
