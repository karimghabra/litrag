"""Every modification the reader makes to a paper, with where it happened.

The reader has always *counted* what it did — `tree.repairs["stitched"] = 3`, `tree.dropped["furniture"] = 8` —
which says a paper was mended but never says where, what it said before, or why. A person reviewing a paper
page by page needs the other thing: this line, on this page, in this box, said *that* and now says *this*,
because a rule with a name decided so.

`Repairs` is a dict of those same counters, so every `repairs[k] = repairs.get(k, 0) + 1` in the reader still
works untouched, plus a log beside it. A site that knows its context calls `note()` instead of incrementing:

    repairs.note("glyphs", page=3, box=[72, 210, 520, 232], before="pH ¼ 7.4", after="pH = 7.4",
                 why="a symbol font's '¼' between words is '='")

`kind` is the counter's own name, so the counts and the log never disagree. `stage` says which pass made the
change; `before`/`after` are the text as it stood and as it stands, `None` where one side does not exist (a
dropped block has no after, a recovered line has no before).
"""

from __future__ import annotations

from typing import Any, Iterable

#: which pass a kind belongs to, for grouping the review by stage
STAGES: dict[str, str] = {
    # recover.py — the text layer read back for what the layout model missed
    "recovered": "recovered",
    "rebuilt": "recovered",
    "formulas": "recovered",
    "attached": "recovered",
    "ligatures": "recovered",
    "tables": "recovered",
    "unglued": "recovered",
    "furniture_stripped": "recovered",
    "table_notes": "recovered",        # a table's note the layout model glued to a paragraph, cut off again
    # what is left out of the body on purpose
    "page_furniture": "dropped",
    "furniture": "dropped",
    "junk": "dropped",
    "decorative": "dropped",
    "sidebar": "dropped",
    "empty": "dropped",
    "label": "dropped",      # front matter the rules name as the journal's, not the paper's
    "picture": "dropped",    # the journal's logo, the same small picture on every page
    # the text itself
    "glyphs": "text",
    "unrepeated": "text",
    "formula_text": "text",
    # blocks put back together
    "stitched": "joined",
    "joined": "joined",
    "rejoined": "joined",
    "rejoined_meaning": "joined",
    "deduplicated": "joined",
    "judged": "joined",
    "caption_tail": "joined",
    "unfused": "split",
    "reordered": "moved",
    "displaced_head": "moved",
    # structure
    "built_headings": "structure",
    "inferred_references": "structure",
    "canonical_meaning": "structure",
    "label_veto": "structure",
    "front_meaning": "structure",
    "heading_level": "structure",      # a heading set in the type of the paper's own sections, taken out of the section above it
    "laned": "structure",              # a section whose heading names nothing, read by its paragraphs
    "lane_disagreement": "structure",  # the heading and the paragraphs disagree: noted, never applied
    "captions_meaning": "structure",
}


def stage_of(kind: str) -> str:
    return STAGES.get(kind, "other")


class Repairs(dict):
    """The reader's counters, and the record of each change beneath them."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.log: list[dict[str, Any]] = []

    def note(
        self,
        kind: str,
        *,
        page: int | None = None,
        box: list[float] | None = None,
        before: str | None = None,
        after: str | None = None,
        why: str | None = None,
        node_id: str | None = None,
        ref: str | None = None,
        count: int = 1,
    ) -> None:
        """Count a change as before, and record what it was."""
        self[kind] = self.get(kind, 0) + count
        self.log.append(
            {
                "kind": kind,
                "stage": stage_of(kind),
                "page": page,
                "box": [round(float(v), 1) for v in box] if box else None,
                "before": _clip(before),
                "after": _clip(after),
                "why": why,
                "node_id": node_id,
                "ref": ref,
            }
        )

    def record(self, kind: str, **where: Any) -> None:
        """Log a change whose counter the caller has already raised."""
        count = self.get(kind, 0)
        self.note(kind, **where)
        self[kind] = count  # note() counts; here the caller did

    def extend(self, records: Iterable[dict[str, Any]]) -> None:
        """Take in changes another pass recorded (recover.py runs before the tree exists)."""
        for record in records:
            kind = str(record.get("kind") or "other")
            self[kind] = self.get(kind, 0) + 1
            self.log.append(
                {
                    "kind": kind,
                    "stage": record.get("stage") or stage_of(kind),
                    "page": record.get("page"),
                    "box": record.get("box"),
                    "before": _clip(record.get("before")),
                    "after": _clip(record.get("after")),
                    "why": record.get("why"),
                    "node_id": record.get("node_id"),
                    "ref": record.get("ref"),
                }
            )


_LIMIT = 600


def _clip(text: str | None) -> str | None:
    """A change is read beside its page, so the middle of a long block is not what a reviewer needs."""
    if text is None:
        return None
    text = " ".join(str(text).split())
    if len(text) <= _LIMIT:
        return text
    return f"{text[: _LIMIT // 2]} […] {text[-_LIMIT // 2 :]}"


def merged(*logs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One log in page order, then in the order the passes ran, for the review."""
    out: list[dict[str, Any]] = []
    for log in logs:
        out.extend(log)
    for seq, record in enumerate(out):
        record["seq"] = seq
    return sorted(out, key=lambda r: (r["page"] if r["page"] is not None else 10_000, r["seq"]))
