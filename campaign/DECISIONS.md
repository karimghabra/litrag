# Decisions taken for Karim

Choices that are really Karim's to make. Each one records the reversible
default taken and why, so he can overturn it cheaply. Newest last.

---

## D1 — The integration branch is cut from the merge commit of PR #20

*2026-09-21, Phase 0.*

**The choice.** `PROMPT.md` says "start from the branch
`claude/decisions-by-meaning`". Three facts complicate that:

- PR #20 (`claude/reader-measured-on-pairs`) was merged **into**
  `claude/decisions-by-meaning`, not into `main`. Its merge commit is
  `8fae8aa`.
- `origin/main` is still at `dcef6a7`, the merge of PR #19. It does **not**
  contain the reader work whose numbers this campaign is asked to reproduce.
- The local checkout was at `ff124e7`, one commit behind `8fae8aa`, and
  content-identical to it (the merge commit changes no file).

**The default taken.** The integration branch `campaign/dynamic-reader` is cut
from `8fae8aa` — `origin/claude/decisions-by-meaning` at its tip. This *is*
"start from `claude/decisions-by-meaning`" on the correct reading: that branch
now contains the measured reader. Cutting from the older tip instead would have
discarded PR #20 and made the baseline unreproducible by construction.

**Reversibility.** Total. Rebase the branch onto any other base.

**What Karim may want to decide instead.** Whether `claude/decisions-by-meaning`
should be merged to `main` before this campaign's PRs stack on top of it. I have
not merged anything to `main` and will not.

---

## D2 — The campaign works on copies, under a campaign root of its own

*2026-09-21, Phase 0.*

**The choice.** `rebuild` writes to a library's `store.sqlite`, and the oracle's
verdicts are written to `lanes.sqlite` at the **library root**, shared by every
library under it. So any rebuild run against `~/.protracker/library` would write
into Karim's own data. `PROMPT.md` forbids that.

**The default taken.** `~/.protracker/campaign/library` holds byte-for-byte
copies of all eleven libraries (3.5 GB; 254 GB free at the time), plus a copy of
`lanes.sqlite` (with its WAL, so none of the 107,771 stored verdicts are lost)
and of `headings.json`. Every command this campaign runs sets
`LITRAG_ROOT=$HOME/.protracker/campaign/library`.

Karim's root was fingerprinted first — every file's size and mtime, 3,354 files,
in `campaign/karim-root-digest.json`. It is re-checked at each phase gate
and in the final report, so "his libraries were never written to" is a verified
claim rather than an intention.

**Reversibility.** Total. Delete the campaign root; it is 3.5 GB of copies.

**The cost.** Disk, and the risk that the copies drift from his originals if he
reads papers during the week. The fingerprint detects that too.

---

## D3 — Reported numbers come from the campaign root, not Karim's

*2026-09-21, Phase 0.*

**The choice.** The baseline numbers in `NOTES.md` were measured against
`~/.protracker/library`. Reproducing them against a copy is only valid if the
copy is faithful.

**The default taken.** Reproduce the baseline on the copies, and verify the
copies are faithful by file count and `store.sqlite` size against the source
(done: eleven of eleven match). If a baseline number fails to reproduce, check
it against Karim's root **read-only** before concluding anything about the code.

**Reversibility.** Total.
