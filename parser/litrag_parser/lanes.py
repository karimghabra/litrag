"""Which lane a heading names, by its meaning — and the one oracle every other question of
resemblance goes to.

The vocabulary in `facets.py` names a lane when a heading matches one of its phrases. Every
corpus adds phrases — "Results/Discussion", "Strengths and limitations", "Data Collection
and Outcome Assessment", Wiley's "3 | Results" — and the list is never finished. So a
heading the vocabulary does not know is asked of the embedder in `meaning.py`: the lane
whose prototype headings lie nearest names it, when the nearest is near enough (cosine
0.75 or more) and clearly nearer than the next lane (a margin of 0.08). A topical heading
of a review ("Immune cells in the aging ventricle") is near nothing and stays `other`:
unassignable beats misassigned.

`abstract` and `references` are never named by meaning — "Graphical abstract",
"Highlights" and "Article history" lie near them and are not them; the vocabulary names
those exactly.

This module holds the process-wide oracle: `configure` builds it (verdicts in
`lanes.sqlite` beside the libraries), `active()` hands it to whoever asks a question, and
`by_meaning` is the heading question `facets.role_of` asks. `LITRAG_LANES=off` leaves it
unconfigured, and every question then answers `other`.
"""

from __future__ import annotations

import os
from pathlib import Path

from .meaning import HEADING_PROTOTYPES, Oracle, standard

PROTOTYPES = HEADING_PROTOTYPES  # the heading examples, as the docs name them

_active: Oracle | None = None


def configure(cache: Path | None, url: str | None = None, model: str | None = None) -> Oracle:
    """The oracle every question after this call consults; `cache` is the store its verdicts
    live in (beside the libraries). The model is `LITRAG_LANES_MODEL`, else nomic-embed-text."""
    global _active
    _active = standard(cache, url=url, model=model)
    return _active


def configure_from_env(root: Path) -> Oracle | None:
    """`configure(root / "lanes.sqlite")` unless `LITRAG_LANES=off`; the one guard the worker,
    the harness and the judge share."""
    if os.environ.get("LITRAG_LANES", "on") == "off":
        return None
    return configure(Path(root) / "lanes.sqlite")


def active() -> Oracle | None:
    return _active


def by_meaning(heading: str, alone: bool = False) -> str:
    """`other` when no embedder is configured (the tests, a bare `parse_json`). With `alone`,
    the kind that also knows abstract and references — the vocabulary-off experiment."""
    if _active is None:
        return "other"
    return _active.nearest("heading-alone" if alone else "heading", heading).name
