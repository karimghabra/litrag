"""Search queries drafted from a project's description, by the local model.

A project says what it is about in a few lines; the literature search wants Europe PMC
queries. The model is asked once, locally (Ollama on 127.0.0.1, the judge's model), for a
handful of queries in Europe PMC's syntax, and the answer is only ever a *suggestion*: the
person clicks one to run it. Nothing is searched, fetched or filed on the model's word.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .judge import default_url

SYSTEM = (
    "You write literature-search queries for Europe PMC. Europe PMC's syntax: AND, OR, NOT, parentheses, "
    "\"quoted phrases\", and field prefixes TITLE: and ABSTRACT:. Given a research project's description and the "
    "queries already run, propose new queries that together cover the project's materials, methods and questions — "
    "specific enough to return tens to a few hundred papers each, not tens of thousands. "
    'Answer JSON only: {"queries": ["...", "..."]}.'
)


def default_model() -> str:
    return os.environ.get("LITRAG_SUGGEST_MODEL") or os.environ.get("LITRAG_JUDGE_MODEL") or "qwen3:14b"


def _clean(q: Any) -> str | None:
    if not isinstance(q, str):
        return None
    q = " ".join(q.split()).strip()
    if len(q) < 3 or len(q) > 300:
        return None
    if q.count('"') % 2 or q.count("(") != q.count(")"):
        return None  # a query Europe PMC would refuse is no suggestion
    return q


def suggest_queries(description: str, done: list[str] | None = None, *, n: int = 6, model: str | None = None,
                    url: str | None = None, timeout: float = 180.0) -> dict[str, Any]:
    """`{queries, model}`, or `{queries: [], model, error}` when the model cannot be had."""
    model = model or default_model()
    if not description.strip():
        return {"queries": [], "model": model, "error": "The project has no description to draft queries from: describe it first."}
    done = done or []
    user = f"Project description:\n{description.strip()}\n\nQueries already run:\n" + ("\n".join(f"- {q}" for q in done) or "(none)") + f"\n\nPropose {n} new queries."
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 600},
    }
    req = urllib.request.Request(f"{url or default_url()}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            reply = json.loads(r.read().decode("utf-8"))
        content = json.loads(reply["message"]["content"])
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError) as e:
        return {"queries": [], "model": model, "error": f"The local model did not answer ({type(e).__name__}): is Ollama running with {model}?"}
    raw = content.get("queries") if isinstance(content, dict) else None
    seen = {re.sub(r"\s+", " ", q.lower()) for q in done}
    out: list[str] = []
    for q in raw or []:
        c = _clean(q)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return {"queries": out[:n], "model": model}
