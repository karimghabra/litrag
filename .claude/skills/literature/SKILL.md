---
name: literature
description: Ask the project's library of papers a bench question and answer from what it holds, with citations — or grow the library. Reads the library; stages papers only when asked. Runs locally, where the papers are.
---

# The literature

litrag keeps one library of papers per project (`DESIGN.md`; the `lit`
CLI — `lit help`, or `npm run lit -- help` from this repository). This skill
is the assistant's way in: a question goes to the library, the answer comes
back with citations, and nothing is asserted that no passage supports.

**The rule that makes this honest: the library speaks, not your training.**
An answer here is built from retrieved passages and cites them. If the
passages do not answer part of the question, say so in those words and say
what would — a paper to fetch, a PDF for the inbox, a search to run. Never
fill a gap from memory and present it as the library's.

1. **Find the library.** `lit libraries --json` lists them with their vault
   project ids. Pick the one for the project in question — by the project's
   vault id (`n156`), its name, or the library id (`looped-ligament`). If the
   project has none, say so and offer `lit init <project>`; do not make one
   unasked. (`npm link` puts `lit` on the PATH; otherwise `npm run lit -- <args>`.)

2. **Ask it.** `lit query <lib> "<question>" --limit 8 --json`. Read every
   hit's `citation`, `section`, `kind` and `text`. `--trace` shows which of
   the three rankings — words, meaning, the graph walk — held each hit and
   which entities the walk started from; use it when a result looks odd.
   For a question with columns in it — "what concentrations has anyone
   used" — go to the rows: `lit sql <lib> "select …"` over `parameters`,
   `materials`, `methods`, `claims`, `entities`. `lit entities <lib>` says
   what the library knows the names of.

3. **Answer from the passages, citing each.** One claim, one citation:
   paper, year, section — the `citation` string as given, or a shorter
   form of it. Quote a value in the paper's own words when a number is the
   answer. Group by paper when several agree; say when they disagree. Keep
   to the user's question; do not tour the library.

4. **Say what the library could not do.** If the top hits are off-topic,
   the answer is "the library does not speak to this", plus the likely
   reason: the papers that would are on `lit wanted`'s list — name them,
   with their links — or no search has been run for the topic. Offer the next
   verb — `lit search`, a PDF for the inbox, `lit snowball <paper>` from a
   paper that came close — and run it only on the user's word.

5. **Grow it only when asked.** `lit search` stages; `lit fetch` and
   `lit ingest` read; `lit annotate` makes the graph's entities; `lit
   extract` runs the model stage on the GPU. Report counts from each verb's
   JSON. A search that pulls in off-topic papers is worth saying out loud
   before fetching them; the query set is the user's to tune.

6. **Never**: fabricate a citation; present a training-memory fact as a
   library finding; run `lit init` or `lit config` unasked; send paper text
   anywhere but the local model.
