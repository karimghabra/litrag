"""An XML paper's figures as images: the files its XML names, from the services that publish it.

A JATS file names each figure's image (`<fig>` … `<graphic xlink:href="…-g001"/>`) and holds
none of it, so a paper read from XML has shown its figures only as placeholders. The image
files are published beside the XML, and are fetched from there, by PMCID, nothing else sent:

1. **PMC Cloud Service** (`pmc-oa-opendata.s3.amazonaws.com`, the host the PDF route asks): the
   latest version's JSON lists `media_urls`, each an `s3://` address carrying the file's MD5.
   A graphic is matched to the file of the same name (`…-g001` to `…-g001.jpg`), and a file is
   taken only when its MD5 holds.
2. **Europe PMC's REST service** (`/{PMCID}/supplementaryFiles?includeInlineImage=yes`): a zip of
   the article's images (each figure at full size and as a small GIF, the larger taken) beside
   its supplementary files. Each member is checked by the zip's own CRC as it is read.
3. **A PDF of the paper**, when neither has the images: the PDF the worker keeps beside the XML
   (figures.keep_beside, which PMC's PDF and an open copy OpenAlex names pass through, worker.py)
   is searched for each figure's caption (figures.caption_pages), and an image on that page that
   the caption claims is cut out at its own resolution. A figure drawn as vector graphics is not
   an image there, and stays without one.

Each image is a file beside the store that holds the paper (the library's own folder, or the shared store), in `figures/<safe key>/` and a row in `figure_images`:
the figure's place among the XML's figures, its label and caption as the XML prints them, the
file, where it came from, its SHA-256, size and pixels. What a paper's ask came to is a row in
`figure_image_asks`: how many images the XML names, how many are on disk, and whether asking again
would change anything (`settled`), and whether the PDF fallback answered for good (`fallback`:
a PDF searched, or none to be had; the worker's to say) — so a run over a whole library is
resumable: a paper both have answered for is passed by, a file on disk whose hash holds is not
fetched again, and a run broken off by the network picks up where it stopped.

A row joins its figure's node by the key the caption gives both (figures.figure_key: "2", "S2",
"Scheme 1"), never by order; a figure the XML gives no number joins a node only when the node's
caption is the XML's caption. Neither is a guess: an image that matches no node is kept and not
shown (invariant 5). A rebuild renumbers nodes and changes nothing here, since the join is made
when asked (`images_for`).

Requests go one at a time, a little apart, and one that breaks off (a reset, a timeout, an answer
cut short, a busy 429/500/502/503/504) is asked again after a pause; a refused connection is not
(nothing is listening). Every base can be pointed elsewhere, as acquire.py's are:
`LITRAG_PMC_CLOUD_URL`, `LITRAG_EPMC_URL`. `LITRAG_FIGURE_IMAGES=off` stops the fetching.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import acquire

#: How many times a request that broke off is asked again, and the first pause (doubled each time).
RETRIES = 4
BACKOFF = 1.5
#: The least time between two requests to one host, in seconds.
GAP = {"cloud": 0.1, "epmc": 0.5}
#: No figure's file is larger; Europe PMC's zip carries the supplementary files too, so it may be.
IMAGE_MOST = 60 * 2**20
ZIP_MOST = 400 * 2**20
#: What the window draws as it is; anything else (a TIFF) is stored as a PNG.
SHOWN = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
_RANK = {".jpg": 0, ".jpeg": 0, ".png": 1, ".tif": 2, ".tiff": 2, ".webp": 3, ".gif": 4}
XLINK = "{http://www.w3.org/1999/xlink}href"

SCHEMA = """
CREATE TABLE IF NOT EXISTS figure_images (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,         -- the figure's place among the XML's figures, from 1
  part INTEGER NOT NULL,            -- the image's place in its figure, from 1: a figure may be printed as several files
  xml_id TEXT,                      -- the <fig>'s id
  figure_label TEXT,                -- the key its label gives it, "2", "S2", "Scheme 1" (figures.figure_key): what joins it to its node; NULL when it gives no number
  label TEXT,                       -- the label as the XML prints it, "Figure 2"
  caption TEXT,                     -- the caption's text, from the XML
  href TEXT,                        -- the graphic's xlink:href; NULL for one cut from a PDF
  file TEXT NOT NULL,               -- the image, from the library's folder: figures/<safe key>/<name>
  source TEXT NOT NULL,             -- pmc-cloud | europepmc | pdf
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, width INTEGER, height INTEGER, mime TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY(paper, ordinal, part)
);
CREATE TABLE IF NOT EXISTS figure_image_asks (
  paper TEXT PRIMARY KEY REFERENCES papers(key) ON DELETE CASCADE,
  at TEXT NOT NULL,
  figures INTEGER NOT NULL,         -- the figures the XML holds
  images INTEGER NOT NULL,          -- the images they name
  had INTEGER NOT NULL,             -- the figures with an image on disk now
  sources TEXT,                     -- where those came from, ", "-joined
  settled INTEGER NOT NULL,         -- 1 when asking the services again would change nothing: every figure had, or each of the two answered for the rest
  why TEXT,                         -- each source's answer, for the figures not had
  fallback INTEGER NOT NULL DEFAULT 0  -- what the PDF fallback came to (worker._images_fallback): 1 answered for good, a PDF of the paper searched or none to be had; 2, 3 no open copy asked for with open copies, or OpenAlex, switched off, an answer while that stays off; 0 to be tried
);
"""


def enabled() -> bool:
    """Whether an XML paper's figure images are fetched, unless `LITRAG_FIGURE_IMAGES=off`."""
    return os.environ.get("LITRAG_FIGURE_IMAGES", "on").strip().lower() not in ("off", "0", "false", "no")


def ensure_schema(conn: sqlite3.Connection) -> None:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('figure_images', 'figure_image_asks')")}
    if len(have) < 2:
        conn.executescript(SCHEMA)
    if "fallback" not in {r[1] for r in conn.execute("PRAGMA table_info(figure_image_asks)")}:
        conn.execute("ALTER TABLE figure_image_asks ADD COLUMN fallback INTEGER NOT NULL DEFAULT 0")
    conn.commit()


# -- the figures the XML names ---------------------------------------------------------------------------------


@dataclass
class XmlFigure:
    """One `<fig>` of a JATS file: its place, id, label, caption, the key the caption gives it, and
    the images it names."""

    ordinal: int
    xml_id: str | None
    label: str | None
    caption: str
    key: str | None
    hrefs: list[str] = field(default_factory=list)


def _text(el: Any) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def jats_figures(raw: bytes) -> list[XmlFigure]:
    """The figures a JATS file holds where its reading has them (jats_prep.floats_in: the body,
    the back matter and the article's floats-group, never `<front>`; an image of a table counts as
    a figure, which is what the reader makes it), in document order, each with its label spelled
    as the reader spells it (jats_prep.name_floats: ACS's bare "1" is "Figure 1") and its key
    (figures.figure_key) from the label and caption, as a node's comes from its caption's text.
    The images are its `<graphic>`s — of an `<alternatives>`, the first that is not a thumbnail
    (Europe PMC's `content-type="thumb"`) — never an `<inline-graphic>`.
    Bytes that are not XML hold none."""
    from lxml import etree

    from .figures import _LABEL, figure_key
    from .jats_prep import _article_groups, _holds_table, _in_front, etree_local, name_floats

    try:
        root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True))
    except etree.XMLSyntaxError:
        return []
    try:
        name_floats(root)
    except Exception:  # noqa: BLE001 — a label not spelled is read as the file has it, as the reader does
        pass
    scopes = [s for s in (next(root.iter("body"), None), next(root.iter("back"), None)) if s is not None] + _article_groups(root)
    out: list[XmlFigure] = []
    seen: dict[int, Any] = {}  # each element kept alive, so that no other takes its id
    for scope in scopes:
        for el in scope.iter("fig", "table-wrap"):
            if id(el) in seen or _in_front(el) or (etree_local(el.tag) == "table-wrap" and _holds_table(el)):
                continue
            seen[id(el)] = el
            hrefs: list[str] = []
            for g in el.iter("graphic"):
                alt = g.getparent()
                if alt is not None and etree_local(alt.tag) == "alternatives":
                    gs = [c for c in alt if isinstance(c.tag, str) and etree_local(c.tag) == "graphic"]
                    pick = next((c for c in gs if (c.get("content-type") or "").lower() != "thumb"), gs[0])
                    if alt.index(g) != alt.index(pick):
                        continue  # Europe PMC's thumbnail beside the image, or another alternative of it
                href = (g.get(XLINK) or "").strip()
                if href and href not in hrefs:
                    hrefs.append(href)
            label = _text(el.find("label")) or None
            caption = _text(el.find("caption"))
            m = _LABEL.match(f"{label or ''} {caption}".strip())
            out.append(XmlFigure(len(out) + 1, el.get("id"), label, caption, figure_key(m.group(1), m.group(2)) if m else None, hrefs))
    return out


# -- asking -----------------------------------------------------------------------------------------------------


class Unreachable(RuntimeError):
    """A service that could not be asked at all: nothing listening, or every try broke off. `got`
    is what it gave before that."""

    def __init__(self, *args: object, got: dict[str, Got] | None = None) -> None:
        super().__init__(*args)
        self.got = got or {}


_last: dict[str, float] = {}


def _pause(host: str) -> None:
    wait = _last.get(host, 0.0) + GAP.get(host, 0.2) - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _broke_off(e: BaseException) -> bool:
    """A request that broke off on the way, worth asking again: a reset, a timeout, an answer cut
    short, a busy service. A refused connection is not: nothing is listening."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (429, 500, 502, 503, 504)
    if isinstance(e, urllib.error.URLError):
        e = e.reason if isinstance(e.reason, BaseException) else e
        if isinstance(e, urllib.error.URLError):
            return False
    if isinstance(e, ConnectionRefusedError):
        return False
    return isinstance(e, (TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException, OSError))


def fetch(url: str, timeout: float, *, host: str, most: int, to: Path | None = None) -> bytes | Path:
    """One GET, asked again when it breaks off (`RETRIES` times, pausing `BACKOFF`, then twice that
    …), never closer than `GAP` to the last request to the same host. The answer's bytes, or with
    `to` the file they were written to (through a `.part` beside it, so a broken answer leaves no
    file). An answer larger than `most` is refused. An HTTP error that is not a busy service
    raises at once; one that keeps breaking off raises `Unreachable`, as a refused connection does."""
    req = urllib.request.Request(url, headers={"User-Agent": acquire.USER_AGENT})
    for attempt in range(RETRIES + 1):
        _pause(host)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                size = resp.headers.get("Content-Length")
                if size and size.isdigit() and int(size) > most:
                    raise ValueError(f"an answer of {int(size)} bytes, more than {most}")
                if to is None:
                    body = resp.read(most + 1)
                    if len(body) > most:
                        raise ValueError(f"an answer of more than {most} bytes")
                    if size and size.isdigit() and len(body) != int(size):
                        raise http.client.IncompleteRead(body, int(size) - len(body))
                    return body
                part = to.with_name(to.name + ".part")
                n = 0
                try:
                    with part.open("wb") as f:
                        while chunk := resp.read(1 << 20):
                            n += len(chunk)
                            if n > most:
                                raise ValueError(f"an answer of more than {most} bytes")
                            f.write(chunk)
                    if size and size.isdigit() and n != int(size):
                        raise http.client.IncompleteRead(b"", int(size) - n)
                    os.replace(part, to)
                finally:
                    part.unlink(missing_ok=True)
                return to
        except Exception as e:  # noqa: BLE001 — sorted below: asked again, or raised as it is
            if not _broke_off(e):
                if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
                    raise Unreachable(f"{type(e).__name__}: {e.reason}") from e
                raise
            if attempt == RETRIES:
                if isinstance(e, urllib.error.HTTPError):
                    raise
                raise Unreachable(f"{type(e).__name__}: {e}") from e
            time.sleep(BACKOFF * 2 ** attempt)
        finally:
            _last[host] = time.monotonic()
    raise AssertionError("unreachable")


def _stem(name: str) -> str:
    """A file's name without its folder or extension, in lower case: what a graphic's href and the
    published file share (`…-g001` and `…-g001.jpg`, `nihms-1-f0001.jpg` and itself)."""
    base = name.rsplit("/", 1)[-1]
    head, dot, ext = base.rpartition(".")
    return (head if dot and ext.lower() in ("jpg", "jpeg", "png", "gif", "tif", "tiff", "webp", "eps", "svg", "bmp") else base).lower()


def _best(names: list[str], sizes: dict[str, int] | None = None) -> str | None:
    """Of the files published for one graphic, the one to keep: a JPEG or a PNG before a TIFF, and
    a GIF last (Europe PMC's GIF is a thumbnail); the larger of two of a kind."""
    usable = [n for n in names if Path(n).suffix.lower() in _RANK]
    if not usable:
        return None
    return min(usable, key=lambda n: (_RANK[Path(n).suffix.lower()], -(sizes or {}).get(n, 0)))


@dataclass
class Got:
    """An image had for one graphic: its bytes and where they came from."""

    body: bytes
    name: str
    source: str


#: The Cloud Service's answer for an author manuscript: PMC publishes its text and no image file, and Europe
#: PMC, which serves images only for the open-access subset, is not asked (it answers 500 after a minute).
MANUSCRIPT = "an author manuscript, published with no image files"
_S3_MEDIA = re.compile(r"^s3://[^/]+/([^?#]+)(?:\?md5=([0-9a-fA-F]{32}))?")
_VERSION = r"<Prefix>{}\.(\d+)/</Prefix>"


def from_cloud(pmcid: str, hrefs: list[str], timeout: float, base: str | None = None) -> tuple[dict[str, Got], str, bool]:
    """The PMC Cloud Service's files for these hrefs: `({href: Got}, why the rest were not had,
    settled)`. Settled when the service answered for every href it did not give: it holds no
    version of the article, or a version listing no file of that name. A file failing its MD5, or
    listed with none to check it by, is not taken, and not settled. A file whose request fails
    costs only itself, the files had before it kept and nothing settled. Raises `Unreachable`
    when the service could not be asked, for the article's record or for a file, carrying the
    files had before it."""
    b = acquire.pmc_cloud_base(base)
    pmcid = pmcid.upper()
    listing = fetch(f"{b}/?{urllib.parse.urlencode({'list-type': 2, 'prefix': f'{pmcid}.', 'delimiter': '/'})}", timeout, host="cloud", most=4 << 20)
    versions = [int(v) for v in re.findall(_VERSION.format(re.escape(pmcid)), bytes(listing).decode("utf-8", "replace"))]
    if not versions:
        return {}, "not in its open-access datasets", True
    v = max(versions)
    try:
        meta = json.loads(fetch(f"{b}/{pmcid}.{v}/{pmcid}.{v}.json", timeout, host="cloud", most=4 << 20))
    except ValueError:
        return {}, "its record would not read", False
    media: dict[str, list[tuple[str, str | None]]] = {}
    for u in meta.get("media_urls") or []:
        m = _S3_MEDIA.match(str(u))
        if m:
            media.setdefault(_stem(m.group(1)), []).append((m.group(1), m.group(2)))
    got: dict[str, Got] = {}
    bad: list[str] = []
    for href in hrefs:
        files = dict(media.get(_stem(href), []))
        pick = _best(list(files))
        if pick is None:
            continue
        name = Path(pick).name
        if not files[pick]:
            bad.append(f"{name} is listed with no MD5")
            continue
        try:
            body = bytes(fetch(f"{b}/{pick}", timeout, host="cloud", most=IMAGE_MOST))
        except urllib.error.HTTPError as e:
            bad.append(f"{name}: HTTP {e.code}")
            continue
        except Unreachable as e:
            raise Unreachable(f"{name}: {e}", got=got) from e
        except ValueError as e:
            bad.append(f"{name}: {e}")
            continue
        if hashlib.md5(body).hexdigest() != files[pick].lower():
            bad.append(f"{name} failed its MD5")
            continue
        got[href] = Got(body, name, "pmc-cloud")
    missing = len(hrefs) - len(got)
    if not missing:
        return got, "", True
    if bad:
        return got, f"{len(bad)} not taken: " + "; ".join(bad[:3]) + ("; …" if len(bad) > 3 else ""), False
    what = MANUSCRIPT if meta.get("is_manuscript") and not media else "no file of that name"
    return got, f"{missing} of {len(hrefs)}: {what}", True


def from_europepmc(pmcid: str, hrefs: list[str], timeout: float, scratch: Path, base: str | None = None) -> tuple[dict[str, Got], str, bool]:
    """Europe PMC's zip of the article's images, for these hrefs: `({href: Got}, why the rest were
    not had, settled)`. The zip is written to `scratch` and gone after; each member is checked by
    its CRC as it is read, and one that fails it is not taken (nor settled). Settled when Europe
    PMC answered for every href it did not give: a zip without it, or a 404. A 500 is a fault of
    its own, asked again as a busy answer is and never settled (an author manuscript, which it
    answers so, is settled by the Cloud Service and not asked here). Raises `Unreachable` when it
    could not be asked."""
    url = f"{acquire.rest_base(base)}/{pmcid.upper()}/supplementaryFiles?includeInlineImage=yes"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        fetch(url, max(timeout, 120), host="epmc", most=ZIP_MOST, to=scratch)
    except urllib.error.HTTPError as e:
        return {}, f"HTTP {e.code}", e.code == 404
    except ValueError as e:
        return {}, str(e), False
    got: dict[str, Got] = {}
    bad = 0
    try:
        try:
            z = zipfile.ZipFile(scratch)
        except zipfile.BadZipFile:
            return {}, "not a zip in the answer", False
        with z:
            infos = {i.filename: i for i in z.infolist() if not i.is_dir()}
            by_stem: dict[str, list[str]] = {}
            for name in infos:
                by_stem.setdefault(_stem(name), []).append(name)
            for href in hrefs:
                names = by_stem.get(_stem(href), [])
                pick = _best(names, {n: infos[n].file_size for n in names})
                if pick is None or infos[pick].file_size > IMAGE_MOST:
                    continue
                try:
                    body = z.read(pick)  # the CRC is checked as it is read
                except (zipfile.BadZipFile, OSError, EOFError, zlib.error):
                    bad += 1
                    continue
                got[href] = Got(body, Path(pick).name, "europepmc")
    finally:
        scratch.unlink(missing_ok=True)
    missing = len(hrefs) - len(got)
    if not missing:
        return got, "", True
    if bad:
        return got, f"{bad} failed their CRC", False
    return got, f"{missing} of {len(hrefs)}: not in its zip", True


# -- storing ----------------------------------------------------------------------------------------------------


def _kind(body: bytes) -> str | None:
    """jpeg | png | gif | webp | tiff by the bytes, or None."""
    if body[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    if body[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def _shown(body: bytes) -> tuple[bytes, str, int | None, int | None]:
    """The bytes the window draws, their type, and the image's pixels: a JPEG, PNG, GIF or WebP as
    it came, anything else PIL opens as a PNG of it. Raises ValueError for bytes that are no image."""
    from PIL import Image

    kind = _kind(body)
    try:
        img = Image.open(io.BytesIO(body))
        img.load()
    except Exception as e:  # noqa: BLE001 — what PIL cannot open is no image
        raise ValueError(f"not an image ({type(e).__name__})") from e
    w, h = img.size
    if kind in SHOWN:
        return body, SHOWN[kind], w, h
    out = io.BytesIO()
    (img if img.mode in ("RGB", "RGBA", "L", "LA", "P") else img.convert("RGB")).save(out, "PNG")
    return out.getvalue(), "image/png", w, h


def folder(lib_dir: Path, key: str) -> Path:
    from .library import safe_key

    return Path(lib_dir) / "figures" / safe_key(key)


def _save(lib_dir: Path, key: str, name: str, body: bytes) -> tuple[str, str, int, int | None, int | None, str]:
    """An image written under the paper's folder (through a temporary file): `(file from the
    library's folder, sha256, bytes, width, height, mime)`."""
    data, mime, w, h = _shown(body)
    if mime == "image/png" and not name.lower().endswith(".png"):
        name = f"{Path(name).stem}.png"
    d = folder(lib_dir, key)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest.relative_to(lib_dir).as_posix(), hashlib.sha256(data).hexdigest(), len(data), w, h, mime


def _on_disk(lib_dir: Path, row: sqlite3.Row) -> bool:
    """Whether a row's file is on disk in the bytes it was stored as."""
    p = Path(lib_dir) / row["file"]
    try:
        return p.is_file() and p.stat().st_size == row["bytes"] and hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
    except OSError:
        return False


def _there(lib_dir: Path, row: sqlite3.Row) -> bool:
    """Whether a row's file is on disk at the size it was stored at: `_on_disk` without reading it."""
    try:
        return (Path(lib_dir) / row["file"]).stat().st_size == row["bytes"]
    except OSError:
        return False


def _held(conn: sqlite3.Connection, lib_dir: Path, key: str) -> dict[tuple[int, int], sqlite3.Row]:
    return {(r["ordinal"], r["part"]): r for r in conn.execute("SELECT * FROM figure_images WHERE paper = ?", (key,)) if _on_disk(lib_dir, r)}


@dataclass
class Asked:
    """What one paper's ask came to: its figures and images, those had (before and new), where
    the new ones came from, each source's answer, whether it is settled, and whether a source
    could not be asked at all."""

    figures: int = 0
    images: int = 0
    had: int = 0
    new: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    why: list[str] = field(default_factory=list)
    settled: bool = False
    unreachable: bool = False
    missing: list[XmlFigure] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"figures": self.figures, "images": self.images, "had": self.had, "new": self.new, "sources": dict(self.sources),
                "why": "; ".join(self.why) or None, "settled": self.settled, "unreachable": self.unreachable, "missing": len(self.missing)}


def _figures_of(conn: sqlite3.Connection, lib_dir: Path, key: str) -> list[XmlFigure] | None:
    row = conn.execute("SELECT format, file FROM papers WHERE key = ?", (key,)).fetchone()
    if row is None or row["format"] != "jats" or not row["file"]:
        return None
    path = Path(lib_dir) / "papers" / row["file"]
    return jats_figures(path.read_bytes()) if path.is_file() else None


def _record(conn: sqlite3.Connection, key: str, a: Asked, now: str, sources: list[str]) -> None:
    conn.execute(
        "INSERT INTO figure_image_asks(paper, at, figures, images, had, sources, settled, why) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(paper) DO UPDATE SET at = excluded.at, figures = excluded.figures, images = excluded.images, had = excluded.had, "
        "sources = excluded.sources, settled = excluded.settled, why = excluded.why",
        (key, now, a.figures, a.images, a.had, ", ".join(sources) or None, int(a.settled), "; ".join(a.why)[:400] or None),
    )


#: What the PDF fallback came to (`figure_image_asks.fallback`): to be tried, answered for good, or
#: no open copy asked for because open copies, or OpenAlex, were switched off.
TRY, FINAL, COPIES_OFF, OPENALEX_OFF = 0, 1, 2, 3


def fallback_answered(conn: sqlite3.Connection, key: str, came: int) -> None:
    """What the PDF fallback came to for a paper asked, kept on its ask: `FINAL` when it answered
    for good (a PDF of the paper searched, or none to be had), so the next run passes it by;
    `COPIES_OFF` or `OPENALEX_OFF` when no open copy was asked for with that switched off, which
    stands only while it is (`_stands`); `TRY` when it broke off (a host not reached, a budget
    spent), so the next run tries it again."""
    conn.execute("UPDATE figure_image_asks SET fallback = ? WHERE paper = ?", (came, key))
    conn.commit()


def _stands(came: int) -> bool:
    """Whether what the PDF fallback came to is an answer now: one for good is; one given with a
    switch off is while the switch is off, and not once it is on again."""
    from . import openalex

    if came == FINAL:
        return True
    if came == COPIES_OFF:
        return not acquire.open_copies()
    return came == OPENALEX_OFF and not (acquire.open_copies() and openalex.enabled())


def pdf_kept(conn: sqlite3.Connection, key: str) -> None:
    """A PDF newly kept beside a paper (figures.keep_beside): what the PDF fallback answered before
    was about another PDF, or none, and stands no longer, so the next run cuts from this one.
    Written with the caller's own commit."""
    if "fallback" in {r[1] for r in conn.execute("PRAGMA table_info(figure_image_asks)")}:
        conn.execute("UPDATE figure_image_asks SET fallback = 0 WHERE paper = ?", (key,))


def _had_sources(conn: sqlite3.Connection, key: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT source FROM figure_images WHERE paper = ? GROUP BY source ORDER BY MIN(ordinal)", (key,))]


def from_services(conn: sqlite3.Connection, lib_dir: Path, key: str, *, timeout: float = 30, again: bool = False,
                  cloud: str | None = None, rest: str | None = None) -> Asked:
    """A paper's figure images from the services that publish its XML, the PMC Cloud Service
    first, then Europe PMC, each asked only for what the one before did not give; the rows and
    the paper's ask written. A figure with every image on disk in its stored bytes is not asked
    again, and a paper whose last ask settled is passed by unless `again`; one that has lost an
    image it had since then is asked again, and the PDF fallback's answer no longer stands. A
    paper not read from XML, or whose file is gone, is asked nothing. Never raises for the network: a source that
    could not be asked is `unreachable`, what it would have given is not settled, and the source
    after it is not asked in this ask."""
    from .library import now_iso

    ensure_schema(conn)
    a = Asked()
    figs = _figures_of(conn, lib_dir, key)
    if figs is None:
        return a
    a.figures, a.images = len(figs), sum(len(f.hrefs) for f in figs)
    done = {o for (o, _) in _held(conn, lib_dir, key)}  # a figure with an image on disk, whichever way it came
    a.had = len(done)
    a.missing = [f for f in figs if f.ordinal not in done]
    last = conn.execute("SELECT settled, figures, had FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()
    if not a.missing:
        a.settled = True
        if last is None or not last["settled"] or last["figures"] != a.figures:
            _record(conn, key, a, now_iso(), _had_sources(conn, key))
            conn.commit()
        return a
    if last is not None and a.had < last["had"]:
        conn.execute("UPDATE figure_image_asks SET had = ?, settled = 0, fallback = 0 WHERE paper = ?", (a.had, key))
        conn.commit()
    elif last is not None and last["settled"] and last["figures"] == a.figures and not again:
        a.settled = True  # asked before, every source answered for what it did not give, and nothing it gave is gone since
        return a
    row = conn.execute("SELECT pmcid FROM papers WHERE key = ?", (key,)).fetchone()
    wanted = [h for f in a.missing for h in f.hrefs]
    if not row["pmcid"] or not wanted:
        a.why.append("no PMCID to ask by" if not row["pmcid"] else "the XML names no image for them")
        a.settled = True
        _record(conn, key, a, now_iso(), _had_sources(conn, key))
        conn.commit()
        return a
    got: dict[str, Got] = {}
    settled = True
    for name, ask in (("PMC Cloud", lambda hs: from_cloud(row["pmcid"], hs, timeout, cloud)),
                      ("Europe PMC", lambda hs: from_europepmc(row["pmcid"], hs, timeout, Path(lib_dir) / "figures" / f".{_safe(key)}.images.zip.part", rest))):
        rest_hrefs = [h for h in wanted if h not in got]
        if not rest_hrefs or (name == "Europe PMC" and a.why and a.why[-1].endswith(MANUSCRIPT)):
            break
        try:
            these, why, ok = ask(rest_hrefs)
        except Unreachable as e:
            these, why, ok = e.got, f"could not be asked: {e}", False
            a.unreachable = True
        except urllib.error.HTTPError as e:
            these, why, ok = {}, f"HTTP {e.code}", False
        except Exception as e:  # noqa: BLE001 — a source's fault never costs the paper
            these, why, ok = {}, f"{type(e).__name__}: {e}", False
        got.update(these)
        if why:
            a.why.append(f"{name}: {why}")
        settled = settled and ok
        if a.unreachable:
            break
    stamp = now_iso()
    for f in a.missing:
        if not f.hrefs or not all(h in got for h in f.hrefs):
            continue
        rows = []
        try:
            for i, h in enumerate(f.hrefs):
                file, sha, n, w, hgt, mime = _save(lib_dir, key, got[h].name, got[h].body)
                rows.append((key, f.ordinal, i + 1, f.xml_id, f.key, f.label, f.caption, h, file, got[h].source, sha, n, w, hgt, mime, stamp))
        except ValueError as e:
            a.why.append(f"{f.label or f'figure {f.ordinal}'}: {e}")
            settled = False
            continue
        conn.execute("DELETE FROM figure_images WHERE paper = ? AND ordinal = ?", (key, f.ordinal))
        conn.executemany(f"INSERT INTO figure_images VALUES ({', '.join('?' * 16)})", rows)
        a.new += 1
        for r in rows:
            a.sources[r[9]] = a.sources.get(r[9], 0) + 1
    a.had += a.new
    a.missing = [f for f in a.missing if not (f.hrefs and all(h in got for h in f.hrefs))]
    a.settled = not a.missing or settled
    if not a.missing:
        a.why = []
    _record(conn, key, a, stamp, _had_sources(conn, key))
    conn.commit()
    return a


def _safe(key: str) -> str:
    from .library import safe_key

    return safe_key(key)


def from_pdf(conn: sqlite3.Connection, lib_dir: Path, key: str, pdf_path: Path) -> dict[str, Any]:
    """The figures still without an image cut from a PDF of the paper (the one kept beside its
    XML): on each page printing one of their captions (figures.caption_pages), every image large
    enough to be a figure that the caption claims (figures.claimed) is cut at its own resolution
    and stored as a PNG, `source` 'pdf'. Only numbered figures: a PDF's caption is matched by its
    number, never by order. `{cut, figures}`: how many figures gained an image, and how many were
    looked for. Never raises: a PDF that will not open cuts nothing."""
    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image

    from . import figures as F
    from .library import now_iso

    ensure_schema(conn)
    figs = _figures_of(conn, lib_dir, key)
    if not figs:
        return {"cut": 0, "figures": 0}
    held = {o for (o, _) in _held(conn, lib_dir, key)}
    todo: dict[str, XmlFigure] = {}
    for f in figs:
        if f.key is not None and f.key not in todo and f.ordinal not in held:
            todo[f.key] = f  # the first figure to give a key keeps it, as figures.figure_numbers has it
    numbers = {k: n for k, n in F.figure_numbers(conn, key).items() if k in todo}
    if not numbers:
        return {"cut": 0, "figures": 0}
    cuts: dict[str, list[bytes]] = {}
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:  # noqa: BLE001
        return {"cut": 0, "figures": len(numbers)}
    try:
        for pno, page, _, caps in F.caption_pages(pdf, numbers):
            w, h = page.get_size()
            images = F._images(page, w, h)
            page.close()
            for _, box in images:
                num = F.claimed(list(box), caps, numbers)
                if num is None or num in cuts and len(cuts[num]) >= 8:
                    continue
                try:
                    img, _, _, _ = F.crop(pdf, pno + 1, box)
                except Exception:  # noqa: BLE001 — one image pdfium cannot render costs only itself
                    continue
                out = io.BytesIO()
                Image.fromarray(np.asarray(img)).save(out, "PNG")
                cuts.setdefault(num, []).append(out.getvalue())
    except Exception:  # noqa: BLE001 — a PDF that breaks part-way keeps what was cut
        pass
    finally:
        pdf.close()
    stamp = now_iso()
    for num, bodies in cuts.items():
        f = todo[num]
        rows = []
        for i, body in enumerate(bodies):
            file, sha, n, w_, h_, mime = _save(lib_dir, key, f"pdf-{_safe(num)}-{i + 1}.png", body)
            rows.append((key, f.ordinal, i + 1, f.xml_id, f.key, f.label, f.caption, None, file, "pdf", sha, n, w_, h_, mime, stamp))
        conn.execute("DELETE FROM figure_images WHERE paper = ? AND ordinal = ?", (key, f.ordinal))
        conn.executemany(f"INSERT INTO figure_images VALUES ({', '.join('?' * 16)})", rows)
    if cuts:
        row = conn.execute("SELECT * FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()
        if row is not None:
            had = len({o for (o, _) in _held(conn, lib_dir, key)})
            conn.execute("UPDATE figure_image_asks SET had = ?, sources = ?, settled = ?, at = ? WHERE paper = ?",
                         (had, ", ".join(_had_sources(conn, key)) or None, int(had >= row["figures"] or row["settled"]), stamp, key))
    conn.commit()
    return {"cut": len(cuts), "figures": len(numbers)}


# -- answering --------------------------------------------------------------------------------------------------


def _squeezed(s: str | None) -> str:
    return re.sub(r"[\W_]+", "", (s or "").casefold())


def images_for(conn: sqlite3.Connection, lib_dir: Path, key: str) -> dict[str, list[dict[str, Any]]]:
    """A paper's figure images by the node they join: `{node_id: [{path, file, label, caption,
    source, width, height, mime}]}`, each figure's parts in order. A row joins the node its key
    names (figures.figure_numbers: the first node whose caption gives that key); a row with no key
    joins the one node whose caption is the XML's caption, when exactly one is and the caption
    says something. Only files on disk. A paper read from its PDF has none: its pages draw them."""
    from .figures import _figure_rows, figure_numbers

    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'figure_images'").fetchone():
        return {}
    p = conn.execute("SELECT format FROM papers WHERE key = ?", (key,)).fetchone()
    if p is None or p["format"] != "jats":
        return {}
    rows = [r for r in conn.execute("SELECT * FROM figure_images WHERE paper = ? ORDER BY ordinal, part", (key,)) if (Path(lib_dir) / r["file"]).is_file()]
    if not rows:
        return {}
    numbers = figure_numbers(conn, key)
    taken = set(numbers.values())
    by_caption: dict[str, list[str]] = {}
    for r in _figure_rows(conn, [key]):
        if r["node_id"] not in taken:
            by_caption.setdefault(_squeezed(r["cap"] or r["text"]), []).append(r["node_id"])
    out: dict[str, list[dict[str, Any]]] = {}
    joined: dict[str, int] = {}  # node → the figure joined to it: a second figure giving the same key does not join it too
    for r in rows:
        node = numbers.get(r["figure_label"]) if r["figure_label"] else None
        if node is None:
            cap = _squeezed(f"{r['label'] or ''}{r['caption'] or ''}")
            nodes = by_caption.get(cap, []) if len(cap) >= 12 else []
            node = nodes[0] if len(nodes) == 1 else None
        if node is None or joined.setdefault(node, r["ordinal"]) != r["ordinal"]:
            continue
        out.setdefault(node, []).append({"path": str(Path(lib_dir) / r["file"]), "file": r["file"], "label": r["label"], "caption": r["caption"],
                                         "source": r["source"], "width": r["width"], "height": r["height"], "mime": r["mime"]})
    return out


def states(conn: sqlite3.Connection, lib_dir: Path) -> dict[str, dict[str, Any]]:
    """Each XML paper's images, from the rows and the files' presence: `{figures, had, settled,
    fallback, sources, why, at}` for a paper asked, nothing for one never asked. `had` counts the
    figures with an image on disk now, at the size it was stored at; `settled` is whether the
    services answered for the rest and `fallback` whether the PDF fallback did — for good, or
    with the open-copy route switched off and off still (`_stands`) — neither standing once an
    image had at the last ask is gone."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'figure_image_asks'").fetchone():
        return {}
    ensure_schema(conn)
    there: dict[str, set[int]] = {}
    for r in conn.execute("SELECT paper, ordinal, file, bytes FROM figure_images"):
        if _there(lib_dir, r):
            there.setdefault(r["paper"], set()).add(r["ordinal"])
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM figure_image_asks"):
        had = len(there.get(r["paper"], ()))
        whole = had >= r["had"]
        out[r["paper"]] = {"figures": r["figures"], "had": had, "settled": bool(r["settled"]) and whole, "fallback": _stands(r["fallback"]) and whole,
                           "sources": r["sources"], "why": r["why"], "at": r["at"]}
    return out


def wanting(state: dict[str, Any] | None) -> bool:
    """Whether a fetch could still change a paper's images, by its state (`states`): never asked,
    or lacking some figure's image while the services or the PDF fallback have yet to answer for good."""
    return state is None or (state["had"] < state["figures"] and not (state["settled"] and state["fallback"]))


#: The registrants a report names, for the papers whose figures have no image.
PUBLISHERS = {"10.1021": "ACS", "10.3390": "MDPI", "10.1016": "Elsevier", "10.1002": "Wiley", "10.1111": "Wiley", "10.1038": "Springer Nature",
              "10.1007": "Springer", "10.1186": "BMC", "10.1039": "RSC", "10.1371": "PLOS", "10.3389": "Frontiers", "10.1073": "PNAS",
              "10.1093": "Oxford", "10.1088": "IOP", "10.1080": "Taylor & Francis", "10.1063": "AIP", "10.1126": "AAAS", "10.1107": "IUCr",
              "10.1042": "Portland Press", "10.1083": "Rockefeller", "10.1146": "Annual Reviews", "10.1103": "APS", "10.1364": "Optica"}


def summary(conn: sqlite3.Connection, lib_dir: Path, within: list[str] | None = None) -> dict[str, Any]:
    """What a library's XML papers (those of `within`, a project's in the shared store) came to: papers, those with any image and with every figure's,
    figures and figures with an image, by source, and the papers without, by publisher (a DOI's
    registrant) and by why. SQL and the files' presence only."""
    from .truth import registrant

    ensure_schema(conn)
    papers = conn.execute("SELECT key, doi FROM papers WHERE format = 'jats' AND status = 'parsed'").fetchall()
    mine = None if within is None else set(within)
    if mine is not None:
        papers = [p for p in papers if p["key"] in mine]
    asks = {r["paper"]: r for r in conn.execute("SELECT * FROM figure_image_asks")}
    imgs: dict[str, set[int]] = {}
    src: dict[str, int] = {}
    for r in conn.execute("SELECT paper, ordinal, source, file FROM figure_images WHERE part = 1"):
        if (mine is None or r["paper"] in mine) and (Path(lib_dir) / r["file"]).is_file():
            imgs.setdefault(r["paper"], set()).add(r["ordinal"])
            src[r["source"]] = src.get(r["source"], 0) + 1
    out: dict[str, Any] = {"papers": len(papers), "with_figures": 0, "any": 0, "every": 0, "figures": 0, "had": 0, "sources": src, "answered": 0,
                           "without": {}, "why": {}}
    for p in papers:
        a = asks.get(p["key"])
        if a is None:
            continue
        out["answered"] += 1
        if not a["figures"]:
            continue
        out["with_figures"] += 1
        n = len(imgs.get(p["key"], ()))
        out["figures"] += a["figures"]
        out["had"] += n
        out["any"] += n > 0
        out["every"] += n >= a["figures"]
        if n < a["figures"]:
            pub = PUBLISHERS.get(registrant(p["doi"]), registrant(p["doi"]))
            out["without"][pub] = out["without"].get(pub, 0) + 1
            why = re.sub(r"\d+ of \d+: ", "", a["why"] or "not asked yet")
            out["why"][why] = out["why"].get(why, 0) + 1
    return out


def merge_into(src_dir: Path, sconn: sqlite3.Connection, target_dir: Path, tconn: sqlite3.Connection, key: str, tkey: str) -> int:
    """A paper filed into another project by a merge (projects.merge) brings its figure images: each
    file on the source's disk in its stored bytes copied under the target's folder for the key the
    target holds it by, its row with it, and the source's last ask, so the target does not ask the
    services again for what they settled. A row the target has already for the same figure stands.
    Returns how many images came."""
    import shutil

    if not sconn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'figure_images'").fetchone():
        return 0
    ensure_schema(tconn)
    came = 0
    cols = [r[1] for r in sconn.execute("PRAGMA table_info(figure_images)")]
    for r in sconn.execute("SELECT * FROM figure_images WHERE paper = ? ORDER BY ordinal, part", (key,)):
        if not _on_disk(src_dir, r):
            continue
        d = folder(target_dir, tkey)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / Path(r["file"]).name
        tmp = dest.with_name(dest.name + ".part")
        shutil.copy2(Path(src_dir) / r["file"], tmp)
        os.replace(tmp, dest)
        got = {**dict(r), "paper": tkey, "file": dest.relative_to(target_dir).as_posix()}
        came += tconn.execute(f"INSERT OR IGNORE INTO figure_images({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [got[c] for c in cols]).rowcount
    a = sconn.execute("SELECT * FROM figure_image_asks WHERE paper = ?", (key,)).fetchone() if \
        sconn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'figure_image_asks'").fetchone() else None
    if a is not None:
        acols = list(a.keys())
        tconn.execute(f"INSERT OR IGNORE INTO figure_image_asks({', '.join(acols)}) VALUES ({', '.join('?' * len(acols))})", [tkey if c == "paper" else a[c] for c in acols])
    return came
