"""Cited answers from the local model: retrieve the passages, then write only what they say.

A question goes first to the retrieval a `query` makes (`retrieve.query`: words and meaning
fused, every hit hydrated from the tree), over one project or several. The passages found are
numbered and handed to a chat model on this machine (Ollama on 127.0.0.1, `LITRAG_OLLAMA_URL`),
which answers in a fixed JSON shape: a status (`answered`, `partial`, `not_found`), a list of
claims each naming the passages it rests on, and what the passages leave unsaid. Nothing is
sent anywhere else; a library is never read by a service.

The model's answer is not taken on its word. Every claim is checked against the passages it
cites before it is shown (`check_claims`):

- citation markers the model writes into a claim's text ("… 26.8 °C [2].") are taken out: the
  claim's `sources` say what it cites, and the prose adds them;
- a citation to a passage that was not given is struck; a claim left citing nothing is not
  part of the answer — it is handed back under `dropped`, with the reason, never shown as a
  finding (an uncited sentence is the model's, not the library's);
- every number the claim states must be printed in a passage it cites, and most of its words
  must be there too, or in the cited papers' titles and the passages' headings (a claim names
  "the type III collagen cold gel" where its paragraph says "C3Gel"); a claim that fails
  either is kept but marked `weak`, so the window shows it as a sentence to check rather
  than a finding;
- an `answered` status with no claim left is a `not_found`, and claims under a `not_found` make
  it a `partial`: the status says what the claims show.

When nothing is retrieved the model is not asked; when the model cannot be had the passages
still come back, with the reason (`status: unanswered`). The passages are cut so that the
prompt fits the model's window whatever their number (1 to `MAX_K`); a prompt that fills the
window all the same is said (`warning`). Every citation carries what the
window needs to open the paper at the passage: the library, the paper's key, the node, its
section, page and box.

The model is the request's `model`, else `LITRAG_ANSWER_MODEL`, else the library's
`library.json` `ollama.answer`, else `DEFAULT_MODEL` — chosen on the grounding set
(`campaign/answers/grounding.jsonl`, NOTES.md 2026-11-19) to fit a 10 GB card beside the
embedder.

    uv run --project parser --no-sync python -m litrag_parser.answer --root DIR --measure campaign/answers/grounding.jsonl [--model M] [--k N] [--json OUT]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from . import retrieve
from .library import Library, list_libraries, open_library
from .store import open_store

DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_URL = "http://127.0.0.1:11434"
K = 8  # passages handed to the model
MAX_K = 20  # the most a question may ask for
SOURCE_CHARS = 1500  # a passage's text as the model sees it; 8 of them and the rules fit an 8,192-token window
PASSAGE_CHARS = K * SOURCE_CHARS  # all the passages' text together: more than 8 passages are each cut shorter
CONTEXT = 8192  # one window for every question: Ollama reloads a model whose num_ctx changes
NUM_PREDICT = 1200
SUPPORT = 0.5  # the share of a claim's words a cited passage must hold, beside every number it states

STATUSES = ("answered", "partial", "not_found")

SYSTEM = (
    "You answer a scientist's question from numbered passages taken from papers in their own library, and from "
    "nothing else. Rules:\n"
    "1. Use only what the passages state. Never add facts from your own knowledge, even well-known ones.\n"
    "2. Write the answer as short claims. Each claim states one thing and lists the numbers of the passages that "
    "state it. A claim no passage states must not be written.\n"
    "3. Keep numbers, units, materials and conditions exactly as the passage gives them.\n"
    "4. If the passages do not answer the question, set status to \"not_found\" and give no claims. If they answer "
    "only part of it, set status to \"partial\" and say in \"missing\" what they leave out. Otherwise \"answered\".\n"
    "5. A passage about a different material, organism or condition than the question asks about does not answer "
    "it. Where passages disagree, give each finding as its own claim with its own passage.\n"
    "Reply with JSON only."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "sources"],
            },
        },
        "missing": {"type": "string"},
    },
    "required": ["status", "claims", "missing"],
}


# -- which model, where ------------------------------------------------------------------------------


def default_url() -> str:
    return (os.environ.get("LITRAG_OLLAMA_URL") or DEFAULT_URL).rstrip("/")


def default_model(lib: Library | None = None) -> str:
    """`LITRAG_ANSWER_MODEL`, else the library's `ollama.answer`, else `DEFAULT_MODEL`."""
    env = os.environ.get("LITRAG_ANSWER_MODEL")
    if env:
        return env
    if lib is not None:
        model = (lib.manifest.get("ollama") or {}).get("answer")
        if model:
            return str(model)
    return DEFAULT_MODEL


def models(url: str | None = None, timeout: float = 2.0) -> dict[str, Any]:
    """What Ollama holds that can answer: `{url, down, error, models: [{name, size, family,
    parameters, quantization}]}`, embedders left out. A `GET /api/tags`, nothing loaded."""
    url = (url or default_url()).rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as r:
            tags = json.loads(r.read().decode("utf-8")).get("models", [])
    except Exception as e:  # noqa: BLE001 - refused, timed out, nonsense: all mean down
        return {"url": url, "down": True, "error": f"Ollama is not answering at {url} ({type(e).__name__})", "models": []}
    out = []
    for m in tags:
        d = m.get("details") or {}
        caps = m.get("capabilities")
        if (isinstance(caps, list) and caps and "completion" not in caps) or "bert" in str(d.get("family") or ""):
            continue  # an embedder
        out.append({"name": m.get("name"), "size": m.get("size"), "family": d.get("family"), "parameters": d.get("parameter_size"), "quantization": d.get("quantization_level")})
    return {"url": url, "down": False, "error": None, "models": sorted(out, key=lambda m: str(m["name"]))}


# -- retrieval, over one library or several -----------------------------------------------------------


def _citation(lib: Library, h: dict[str, Any]) -> dict[str, Any]:
    hit, paper, section = h["hit"], h.get("paper") or {}, h.get("section") or {}
    return {
        "lib": lib.id,
        "node_id": hit["node_id"],
        "paper": paper.get("key") or hit.get("paper"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "journal": paper.get("journal"),
        "doi": paper.get("doi"),
        "section": hit.get("heading") or section.get("heading"),
        "ancestry": hit.get("ancestry") or [],
        "role": hit.get("role"),
        "type": hit.get("type"),
        "page": hit.get("page"),
        "bbox": hit.get("bbox"),
        "text": hit.get("text") or "",
        "score": h.get("score"),
        "ranks": h.get("ranks"),
    }


def gather(libs: list[Library], question: str, embedder: Any, k: int = K) -> dict[str, Any]:
    """The `k` best passages across `libs`: each library's `retrieve.query`, merged by fused
    score. A passage the same paper holds in two libraries is given once. Returns `{sources,
    embedder, counts, seconds}`; `sources` carry no number yet."""
    t0 = time.monotonic()
    pooled: list[dict[str, Any]] = []
    meaning: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for lib in libs:
        conn = open_store(lib.store_path)
        try:
            out = retrieve.query(conn, question, embedder, k=k)
        finally:
            conn.close()
        meaning = meaning if meaning.get("down") else out["embedder"]
        for name, n in (out.get("counts") or {}).items():
            counts[name] = counts.get(name, 0) + int(n or 0)
        pooled += [_citation(lib, h) for h in out["hits"]]
    pooled.sort(key=lambda s: -(s["score"] or 0.0))
    seen: set[tuple[str, str]] = set()
    sources: list[dict[str, Any]] = []
    for s in pooled:
        key = (str(s["paper"]), _norm(s["text"])[:200])
        if key in seen:
            continue
        seen.add(key)
        sources.append(s)
        if len(sources) >= k:
            break
    return {"sources": sources, "embedder": meaning, "counts": counts, "seconds": round(time.monotonic() - t0, 3)}


# -- the prompt ------------------------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return head[: space if space > limit * 0.8 else limit] + " …"


def where(s: dict[str, Any]) -> str:
    """A passage's place as a citation reads: paper (year), section path, page."""
    path = " › ".join(a for a in (s.get("ancestry") or []) if a) or (s.get("section") or "")
    bits = [f"{s.get('title') or s.get('paper')}" + (f" ({s['year']})" if s.get("year") else "")]
    if path:
        bits.append(path)
    if s.get("page"):
        bits.append(f"p. {s['page']}")
    return " — ".join(bits)


def prompt(question: str, sources: list[dict[str, Any]]) -> str:
    limit = min(SOURCE_CHARS, PASSAGE_CHARS // max(1, len(sources)))
    blocks = [f"[{s['n']}] {where(s)}\n{_cut(s['text'], limit)}" for s in sources]
    return f"Question: {question.strip()}\n\nPassages:\n\n" + "\n\n".join(blocks) + "\n\nAnswer the question from these passages only."


# -- the model ---------------------------------------------------------------------------------------------


class ModelError(RuntimeError):
    pass


def chat(model: str, system: str, user: str, *, url: str | None = None, timeout: float = 600.0,
         on_tokens: Callable[[int], None] | None = None) -> dict[str, Any]:
    """One structured answer from Ollama's `/api/chat`, streamed so progress can be told:
    `{content, prompt_tokens, tokens, seconds, load_seconds}`. Thinking off, temperature 0,
    a fixed seed and window, so the same question over the same passages is the same answer.
    Raises `ModelError` with the reason a person can act on."""
    url = (url or default_url()).rstrip("/")
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": True,
        "format": SCHEMA,
        "think": False,
        "options": {"temperature": 0, "seed": 7, "num_ctx": CONTEXT, "num_predict": NUM_PREDICT},
    }
    t0 = time.monotonic()
    for attempt in (0, 1):
        req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            text = e.read()[:300].decode("utf-8", "replace") if hasattr(e, "read") else ""
            if attempt == 0 and "think" in text.lower():
                body.pop("think")  # a model with no thinking to turn off
                continue
            if e.code == 404 or "not found" in text.lower():
                raise ModelError(f"Ollama does not have {model}: ollama pull {model}") from e
            raise ModelError(f"Ollama refused the question (HTTP {e.code}): {text.strip()}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ModelError(f"Ollama is not answering at {url} ({type(e).__name__}): start it, then ask again") from e
    parts: list[str] = []
    last: dict[str, Any] = {}
    tokens = 0
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise ModelError(f"the model failed: {msg['error']}")
                piece = (msg.get("message") or {}).get("content") or ""
                if piece:
                    parts.append(piece)
                    tokens += 1
                    if on_tokens is not None:
                        on_tokens(tokens)
                if msg.get("done"):
                    last = msg
                    break
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise ModelError(f"Ollama stopped answering part-way ({type(e).__name__}): ask again") from e
    if not last:
        raise ModelError("Ollama stopped answering part-way (the reply ended unfinished): ask again")
    return {
        "content": "".join(parts),
        "prompt_tokens": last.get("prompt_eval_count"),
        "tokens": last.get("eval_count") or tokens,
        "seconds": round(time.monotonic() - t0, 2),
        "load_seconds": round((last.get("load_duration") or 0) / 1e9, 2),
        "eval_seconds": round((last.get("eval_duration") or 0) / 1e9, 2),
        "done_reason": last.get("done_reason"),
    }


def parse_reply(content: str) -> dict[str, Any] | None:
    """The model's JSON, or None when it is not the shape asked for."""
    try:
        d = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("status") not in STATUSES or not isinstance(d.get("claims", []), list):
        return None
    claims = []
    for c in d.get("claims") or []:
        if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not c["text"].strip():
            continue
        cites = c.get("sources") if isinstance(c.get("sources"), list) else []
        claims.append({"text": " ".join(c["text"].split()), "sources": [x for x in cites if isinstance(x, int) and not isinstance(x, bool)]})
    return {"status": d["status"], "claims": claims, "missing": str(d.get("missing") or "").strip()}


# -- the check ----------------------------------------------------------------------------------------------

_NUM = re.compile(r"(?<![\w.])[-−]?\d+(?:[.,]\d+)*")
_DIGITS = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")  # also the 2 of "α2" and the 4 of "H4"
_WORDS = re.compile(r"[^\W\d_]{4,}")
STOP = frozenset(
    "about above after again against also among based because been before being below between both could does doing during each either from further have having here however into itself just more most much must only other over same should since some such than that their them then there these they this those through under until upon very were what when where whether which while whom with within without would your".split()
)


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower().replace("−", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s)


def numbers(text: str, glued: bool = False) -> set[str]:
    """The numbers a text states, as written less thousands separators: "1,000" and "1000" are
    one number, "2.5" is not "25". `glued` also takes digits that follow a letter, as in "α2"
    or "H4", so that a claim's "alpha 2 chain" finds its passage's "α2"."""
    out = set()
    for m in (_DIGITS if glued else _NUM).finditer(_norm(text)):
        n = m.group(0).lstrip("-")
        if re.fullmatch(r"\d{1,3}(,\d{3})+", n):
            n = n.replace(",", "")
        out.add(n.replace(",", "."))
    return out


def _stems(text: str) -> set[str]:
    return {w[:6] for w in _WORDS.findall(_norm(text)) if w not in STOP}


def support(claim: str, passages: list[str], context: Iterable[str] = ()) -> dict[str, Any]:
    """How far the cited passages hold a claim: `{numbers_missing, words, support}` — every
    number the claim states that none of them prints, the share of its words (cut to six
    letters, so "crosslinked" meets "crosslinking") they hold, and the verdict, `supported` or
    `weak`. `context` — the cited papers' titles, the passages' headings — counts for words,
    never for numbers: a number must be in a passage's own text."""
    text = " ".join(passages)
    have = numbers(text, glued=True)
    missing = sorted(n for n in numbers(claim) if n not in have)
    want = _stems(claim)
    share = (len(want & _stems(" ".join([text, *context]))) / len(want)) if want else 1.0
    return {"numbers_missing": missing, "words": round(share, 3), "support": "supported" if not missing and share >= SUPPORT else "weak"}


_MARK = re.compile(r"\s*\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\]")


def unmarked(text: str) -> str:
    """A claim's text without the citation markers some models write into it as well as into
    its `sources` ("… at 26.8 °C [2]."): the 2 is not a number the claim states, and the prose
    adds the citations itself."""
    return re.sub(r"\s+([.,;:])", r"\1", _MARK.sub("", text or "")).strip()


def check_claims(reply: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """The model's reply held against the passages: `{status, claims, dropped, missing}` — see
    the module's docstring for the rules."""
    by_n = {s["n"]: s for s in sources}
    claims: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for c in reply["claims"]:
        c = {**c, "text": unmarked(c["text"])}
        valid = sorted({n for n in c["sources"] if n in by_n})
        struck = sorted({n for n in c["sources"] if n not in by_n})
        if not valid:
            dropped.append({"text": c["text"], "sources": c["sources"], "reason": "cites no passage it was given" if c["sources"] else "cites nothing"})
            continue
        context = [t for n in valid for t in [by_n[n].get("title") or "", *(by_n[n].get("ancestry") or [])]]
        s = support(c["text"], [by_n[n]["text"] for n in valid], context)
        claims.append({"text": c["text"], "sources": valid, **({"struck": struck} if struck else {}), **s})
    status = reply["status"]
    if status in ("answered", "partial") and not claims:
        status = "not_found"
    elif status == "not_found" and claims:
        status = "partial"
    return {"status": status, "claims": claims, "dropped": dropped, "missing": reply.get("missing") or ""}


def summary(status: str, claims: list[dict[str, Any]], missing: str) -> str:
    """The answer as prose, every sentence closed by its citations: "… at 37 °C [2, 5]."."""
    if status in ("not_found", "unanswered") or not claims:
        return "The library does not answer this." + (f" {missing}" if missing and status == "not_found" else "")
    out = []
    for c in claims:
        text = c["text"].rstrip()
        end = text[-1] if text and text[-1] in ".!?" else "."
        out.append(f"{text.rstrip('.!?')} [{', '.join(str(n) for n in c['sources'])}]{end}")
    return " ".join(out)


# -- the whole answer -------------------------------------------------------------------------------------------


def ask(libs: list[Library], question: str, *, model: str | None = None, k: int = K, url: str | None = None,
        embedder: Any = None, on_progress: Callable[[dict[str, Any]], None] | None = None,
        chat_fn: Callable[..., dict[str, Any]] = chat) -> dict[str, Any]:
    """Retrieve, ask, check: `{question, libs, model, status, summary, claims, dropped, missing,
    sources, retrieval, timing, error?, warning?}`. `sources` are every passage the model was
    given, each with its number `n` and `cited` (whether a kept claim cites it). `k` is held to
    1..`MAX_K`; `warning` says a prompt and reply that filled the model's window."""
    question = (question or "").strip()
    if not question:
        raise ValueError("an empty question")
    if not libs:
        raise ValueError("no library to ask")
    model = model or default_model(libs[0])
    k = max(1, min(MAX_K, int(k)))
    url = (url or default_url()).rstrip("/")
    say = on_progress or (lambda _e: None)
    t0 = time.monotonic()
    say({"stage": "retrieve", "message": f"Retrieving passages from {len(libs)} librar{'y' if len(libs) == 1 else 'ies'}"})
    got = gather(libs, question, embedder if embedder is not None else retrieve.OllamaEmbedder(url=url), k=k)
    sources = got["sources"]
    for i, s in enumerate(sources, 1):
        s["n"] = i
    out: dict[str, Any] = {
        "question": question,
        "libs": [l.id for l in libs],
        "model": model,
        "status": "not_found",
        "summary": "",
        "claims": [],
        "dropped": [],
        "missing": "",
        "sources": sources,
        "retrieval": {"embedder": got["embedder"], "counts": got["counts"], "seconds": got["seconds"]},
        "timing": {"retrieve": got["seconds"]},
    }
    if not sources:
        out["missing"] = "No passage in the library matches the question."
        out["summary"] = summary("not_found", [], out["missing"])
        out["timing"]["total"] = round(time.monotonic() - t0, 2)
        return out
    papers = len({(s["lib"], s["paper"]) for s in sources})
    say({"stage": "model", "message": f"Asking {model} about {len(sources)} passages from {papers} paper{'s' if papers != 1 else ''}", "sources": len(sources)})
    last = [0.0]

    def tokens(n: int) -> None:
        now = time.monotonic()
        if now - last[0] >= 1.0:
            last[0] = now
            say({"stage": "writing", "message": f"{model} is writing: {n} tokens", "tokens": n})

    try:
        r = chat_fn(model, SYSTEM, prompt(question, sources), url=url, on_tokens=tokens)
    except ModelError as e:
        out.update(status="unanswered", error=str(e))
        out["summary"] = f"No answer: {e}. The passages retrieved are below."
        out["timing"]["total"] = round(time.monotonic() - t0, 2)
        return out
    out["timing"].update(model=r["seconds"], load=r.get("load_seconds"), prompt_tokens=r.get("prompt_tokens"), tokens=r.get("tokens"),
                         tokens_per_second=round(r["tokens"] / r["eval_seconds"], 1) if r.get("eval_seconds") else None)
    if (r.get("prompt_tokens") or 0) + (r.get("tokens") or 0) >= CONTEXT:
        out["warning"] = f"The question and its passages filled {model}'s {CONTEXT:,}-token window: part of them may not have been read. Ask with fewer passages."
    reply = parse_reply(r["content"])
    if reply is None:
        out.update(status="unanswered", error=f"{model} did not answer in the shape asked for" + (" (cut short)" if r.get("done_reason") == "length" else ""), raw=r["content"][:2000])
        out["summary"] = f"No answer: {out['error']}. The passages retrieved are below."
    else:
        checked = check_claims(reply, sources)
        out.update(checked)
        out["summary"] = summary(checked["status"], checked["claims"], checked["missing"])
    cited = {n for c in out["claims"] for n in c["sources"]}
    for s in sources:
        s["cited"] = s["n"] in cited
    out["timing"]["total"] = round(time.monotonic() - t0, 2)
    return out


def resolve(root: Path, names: Iterable[str]) -> list[Library]:
    """Libraries by id, name or project id; `all` (or `*`) is every library under the root."""
    libs: list[Library] = []
    for name in names:
        name = str(name).strip()
        if not name:
            continue
        if name.lower() in ("all", "*"):
            return list_libraries(root)
        lib = open_library(root, name)
        if lib is None:
            raise ValueError(f"No library {name!r} under {root}")
        if all(l.id != lib.id for l in libs):
            libs.append(lib)
    return libs


# -- the grounding set ----------------------------------------------------------------------------------------------


def _holds_all(text: str, anchors: list[str]) -> bool:
    t = _norm(text)
    return all(_norm(a) in t for a in anchors)


def judge_case(case: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    """One question of the grounding set against its answer. A case with `answer` is in the
    library: `found` (the answering passage — the paper's DOI, every anchor — was among the
    sources), `answered` (status answered or partial), `cites_answer` (a kept claim cites that
    passage). A case with `absent: true` is not: `refused` (status not_found). Both count claims
    kept, supported and weak, and claims dropped. An `unanswered` case — no model, a broken
    reply — is judged on neither: it carries `error` and nothing else."""
    out: dict[str, Any] = {"id": case["id"], "status": a["status"], "claims": len(a["claims"]),
                           "supported": sum(c["support"] == "supported" for c in a["claims"]),
                           "weak": sum(c["support"] == "weak" for c in a["claims"]), "dropped": len(a["dropped"]),
                           "seconds": a["timing"].get("total"), "model_seconds": a["timing"].get("model"),
                           "tokens_per_second": a["timing"].get("tokens_per_second")}
    if a["status"] == "unanswered":
        out["error"] = a.get("error") or "unanswered"
        return out
    if case.get("absent"):
        out["refused"] = a["status"] == "not_found"
        return out
    want = case["answer"]
    answering = {s["n"] for s in a["sources"] if (s.get("doi") or "").lower() == want["doi"].lower() and _holds_all(s["text"], want["contains"])}
    out["found"] = bool(answering)
    out["answered"] = a["status"] in ("answered", "partial")
    out["cites_answer"] = any(set(c["sources"]) & answering for c in a["claims"])
    out["cites_paper"] = any((a["sources"][n - 1].get("doi") or "").lower() == want["doi"].lower() for c in a["claims"] for n in c["sources"])
    return out


def measure(root: Path, cases: list[dict[str, Any]], model: str | None = None, k: int = K,
            on_case: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Every case asked and judged: `{model, cases, present {n, found, answered, cites_answer,
    refused_wrongly}, absent {n, refused, answered_wrongly}, claims {kept, supported, weak,
    dropped, support_rate}, seconds {mean, median}, tokens_per_second, errors}`. `model` is the
    model the answers used; a case left `unanswered` counts under `errors` and in no rate."""
    rows: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for case in cases:
        libs = resolve(root, [case["lib"]] if isinstance(case["lib"], str) else case["lib"])
        a = ask(libs, case["question"], model=model, k=k)
        j = judge_case(case, a)
        rows.append(j)
        answers.append({"case": case, "answer": a})
        if on_case:
            on_case(case, j)
    judged = [r for r in rows if "error" not in r]
    present = [r for r in judged if "found" in r]
    absent = [r for r in judged if "refused" in r]
    kept = sum(r["claims"] for r in judged)
    sup = sum(r["supported"] for r in judged)
    secs = sorted(r["seconds"] for r in judged if r.get("seconds") is not None)
    tps = [r["tokens_per_second"] for r in judged if r.get("tokens_per_second")]
    return {
        "model": ", ".join(sorted({x["answer"]["model"] for x in answers})),
        "k": k,
        "cases": rows,
        "present": {"n": len(present), "found": sum(r["found"] for r in present), "answered": sum(r["answered"] for r in present),
                    "cites_answer": sum(r["cites_answer"] for r in present), "cites_paper": sum(r["cites_paper"] for r in present),
                    "refused_wrongly": sum(not r["answered"] for r in present if r["found"])},
        "absent": {"n": len(absent), "refused": sum(r["refused"] for r in absent), "answered_wrongly": sum(not r["refused"] for r in absent)},
        "claims": {"kept": kept, "supported": sup, "weak": sum(r["weak"] for r in judged), "dropped": sum(r["dropped"] for r in judged),
                   "support_rate": round(sup / kept, 3) if kept else None},
        "seconds": {"mean": round(sum(secs) / len(secs), 2) if secs else None, "median": secs[len(secs) // 2] if secs else None},
        "tokens_per_second": round(sum(tps) / len(tps), 1) if tps else None,
        "errors": len(rows) - len(judged),
        "answers": answers,
    }


# -- the command line --------------------------------------------------------------------------------------------------


def _line(j: dict[str, Any]) -> str:
    if "error" in j:
        judged = f" error={j['error']}"
    elif "found" in j:
        judged = f" found={j['found']} cites={j['cites_answer']}"
    else:
        judged = f" refused={j['refused']}"
    return f"  {j['id']}: {j['status']}{judged} claims={j['claims']} weak={j['weak']} {j['seconds']}s"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.answer", description="The grounding set asked and judged: cited answers measured on questions the library answers and questions it cannot.")
    ap.add_argument("--measure", required=True, help="a grounding set (JSON lines): ask every case and report")
    ap.add_argument("--root", default=os.environ.get("LITRAG_ROOT") or os.environ.get("PROTRACKER_LIBRARY") or str(Path.home() / ".protracker" / "library"))
    ap.add_argument("--model", help=f"the chat model (default LITRAG_ANSWER_MODEL, the library's ollama.answer, or {DEFAULT_MODEL})")
    ap.add_argument("--k", type=int, default=K, help=f"passages handed to the model (1 to {MAX_K})")
    ap.add_argument("--json", help="a file to write the whole report to, every answer in it")
    args = ap.parse_args(argv)
    cases = [json.loads(l) for l in Path(args.measure).read_text("utf-8").splitlines() if l.strip()]
    rep = measure(Path(args.root).expanduser(), cases, model=args.model, k=args.k, on_case=lambda c, j: print(_line(j), file=sys.stderr))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("cases", "answers")}, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
