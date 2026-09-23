"""The retrieval bench's judging: an answer is the right paper's passage holding every anchor,
found in the passage itself or, for the tree, in what it is hydrated with."""

from litrag_parser.bench import holds, judge_lit, judge_tree, rate


def test_anchors_match_however_the_page_set_them():
    assert holds("cross linking in vacuum oven at 100�C under vacuum for 24  h", ["vacuum", "24 h"])
    assert holds("DMMB Assay", ["dmmb"])
    assert not holds("dehydrothermal crosslinking", ["dehydrothermal", "24 h"])


def test_tree_counts_the_context_a_passage_is_hydrated_with():
    answer = {"doi": "10.1/x", "contains": ["40 V"]}
    hits = [
        {"rank": 1, "paper": {"doi": "10.1/other"}, "hit": {"text": "40 V applied"}, "before": [], "after": [], "methods": []},
        {"rank": 2, "paper": {"doi": "10.1/X"}, "hit": {"text": "Threads were collected in isopropanol."}, "before": [{"text": "A potential of 40 V was applied."}], "after": [], "methods": []},
        {"rank": 3, "paper": {"doi": "10.1/x"}, "hit": {"text": "A constant 40 V potential."}, "before": [], "after": [], "methods": []},
    ]
    got = judge_tree(answer, hits)
    assert got["passage"] == 3 and got["context"] == 2  # another paper's words are no answer; a neighbour's are


def test_lit_and_the_rates():
    assert judge_lit({"doi": "10.1/x", "contains": ["DMMB"]}, [{"doi": "10.1/y", "text": "DMMB"}, {"doi": "10.1/x", "text": "the DMMB assay"}])["passage"] == 2
    r = rate([{"passage": 1}, {"passage": 3}, {"passage": None}, {"error": "x"}], "passage", 8)
    assert r == {"n": 3, "at1": 1, "at3": 2, "at8": 2, "mrr": round((1 + 1 / 3) / 3, 3)}
