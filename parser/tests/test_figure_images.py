"""An XML paper's figures as images (figure_images.py): the figures a JATS file names, their files
from the PMC Cloud Service and Europe PMC (canned on 127.0.0.1, from records taken live), a PDF of
the paper as the fallback, the rows, the join to the tree, and the worker's ingest and op.
Nothing here reaches the network."""

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from litrag_parser import figure_images as fi
from litrag_parser.library import create_library
from litrag_parser.store import file_paper, open_store

FIXTURES = Path(__file__).parent / "fixtures"
#: The PMC Cloud Service's record of PMC11278924.1, as it answered on 2026-11-20 (media_urls with their MD5s).
RECORD = json.loads((FIXTURES / "figure_images" / "PMC11278924.1.json").read_text())
#: Europe PMC's zip of the same article's images (`supplementaryFiles?includeInlineImage=yes`, 2026-11-20), its GIF thumbnails kept.
THUMBS = (FIXTURES / "figure_images" / "PMC11278924.epmc-thumbnails.zip").read_bytes()
XML = (FIXTURES / "PMC11278924.xml").read_bytes()
PMCID = "PMC11278924"
NAMES = [f"micromachines-15-00851-g00{i}.jpg" for i in range(1, 7)]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(fi, "BACKOFF", 0)
    monkeypatch.setattr(fi, "GAP", {"cloud": 0, "epmc": 0})


def jpeg(seed: int, size=(40, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (seed * 30 % 255, 80, 160)).save(out, "JPEG")
    return out.getvalue()


def cloud(epmc, images: dict[str, bytes], *, record=None, md5=None):
    """The Cloud Service holding PMC11278924.1: its live record, each media file's MD5 that of the
    bytes stood in for it here (or `md5`, to fail it)."""
    meta = dict(record or RECORD)
    urls = []
    for u in meta.get("media_urls", []):
        name = u.split("/")[-1].split("?")[0]
        if name in images:
            urls.append(f"s3://pmc-oa-opendata/{PMCID}.1/{name}?md5={md5 or hashlib.md5(images[name]).hexdigest()}")
            epmc.routes[f"/cloud/{PMCID}.1/{name}"] = (200, "image/jpeg", images[name])
    meta["media_urls"] = urls
    epmc.json(f"/cloud/{PMCID}.1/{PMCID}.1.json", meta)


def library(tmp_path, xml=XML, caps=None, pmcid=PMCID, doi="10.3390/mi15070851"):
    """A library holding one paper read from `xml`, its figures as nodes captioned as the reader
    leaves them ("Figure 1 …"), the six of PMC11278924 unless `caps` says."""
    lib = create_library(tmp_path, f"Images {len(list(tmp_path.iterdir()))}")
    (lib.papers_dir / "paper.xml").write_bytes(xml)
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="Aligned collagen", file="paper.xml", sha256="x", fmt="jats", doi=doi, pmid=None, pmcid=pmcid, now="t").key
    caps = caps if caps is not None else [f"Figure {i} What figure {i} shows." for i in range(1, 7)]
    rows = [(f"{key}#s", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "")]
    for i, cap in enumerate(caps, 1):
        rows.append((f"{key}#s#picture-{i}", key, f"{key}#s", i, 2, "picture", "picture", "results", None, "[]", ""))
        rows.append((f"{key}#s#picture-{i}#caption-1", key, f"{key}#s#picture-{i}", 0, 3, "caption", "caption", "results", None, "[]", cap))
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (key,))
    conn.commit()
    return lib, conn, key


def asked(epmc):
    return [p for p in epmc.seen if p.startswith(("/cloud/", "/rest/"))]


# -- the figures the XML names ---------------------------------------------------------------------------------


def test_the_figures_of_a_jats_file_with_their_labels_keys_and_images():
    figs = fi.jats_figures(XML)
    assert [(f.ordinal, f.label, f.key, f.hrefs) for f in figs] == [(i, f"Figure {i}", str(i), [NAMES[i - 1]]) for i in range(1, 7)]  # the image, never Europe PMC's thumbnail beside it
    assert figs[0].xml_id == "micromachines-15-00851-f001" and figs[0].caption.startswith("(A) 0.1% (4 h) random")


def test_what_the_reader_counts_as_a_figure_is_what_is_named():
    xml = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta><abstract><fig id="ga"><graphic xlink:href="toc"/></fig></abstract></article-meta></front>
    <body><sec><fig id="f1" fig-type="figure"><label>1</label><caption><p>Bare label.</p></caption><graphic xlink:href="ja-1"/></fig>
    <fig id="sch1"><label>1</label><caption><p>A route.</p></caption><graphic xlink:href="sch-1.tif"/></fig>
    <fig id="f2"><label>Figure 2</label><caption><p>Two panels.</p></caption><graphic xlink:href="f2a"/><graphic xlink:href="f2b"/><p><inline-graphic xlink:href="icon"/></p></fig>
    <table-wrap id="t1"><label>Table 1</label><caption><p>An image of a table.</p></caption><graphic xlink:href="t1"/></table-wrap>
    <table-wrap id="t2"><label>Table 2</label><table><tr><td>x</td></tr></table></table-wrap>
    <fig id="u"><caption><p>No number at all.</p></caption><graphic xlink:href="u"/></fig></sec></body></article>"""
    figs = fi.jats_figures(xml)
    assert [(f.xml_id, f.label, f.key, f.hrefs) for f in figs] == [
        ("f1", "Figure 1", "1", ["ja-1"]),  # ACS's bare "1" spelled out, as the reader spells it
        ("sch1", "Scheme 1", "Scheme 1", ["sch-1.tif"]),  # a scheme keeps its kind: never figure 1
        ("f2", "Figure 2", "2", ["f2a", "f2b"]),  # a figure printed as two files; no inline graphic
        ("t1", "Table 1", None, ["t1"]),  # an image of a table, which the reader makes a figure
        ("u", None, None, ["u"]),
    ]  # never the graphical abstract under <front>
    assert fi.jats_figures(b"not xml") == []


# -- from the services -----------------------------------------------------------------------------------------


def test_images_from_the_pmc_cloud_service_checked_stored_and_joined_to_their_figures(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    a = fi.from_services(conn, lib.dir, key)
    assert (a.figures, a.images, a.had, a.new, a.sources, a.settled, a.unreachable) == (6, 6, 6, 6, {"pmc-cloud": 6}, True, False)
    rows = conn.execute("SELECT ordinal, figure_label, label, href, file, source, sha256, width, height, mime FROM figure_images WHERE paper = ? ORDER BY ordinal", (key,)).fetchall()
    assert [(r[0], r[1], r[2], r[3], r[5], r[7], r[8], r[9]) for r in rows] == [(i, str(i), f"Figure {i}", NAMES[i - 1], "pmc-cloud", 40, 30, "image/jpeg") for i in range(1, 7)]
    assert all((lib.dir / r[4]).read_bytes() == images[r[3]] and hashlib.sha256(images[r[3]]).hexdigest() == r[6] for r in rows)
    assert rows[0][4] == "figures/doi_10.3390_mi15070851/micromachines-15-00851-g001.jpg"
    assert "/rest/" not in "".join(asked(epmc))  # Europe PMC not asked: the Cloud gave them all
    joined = fi.images_for(conn, lib.dir, key)
    assert {n: [i["file"].rsplit("/", 1)[1] for i in v] for n, v in joined.items()} == {f"{key}#s#picture-{i}": [NAMES[i - 1]] for i in range(1, 7)}
    assert joined[f"{key}#s#picture-1"][0]["path"] == str(lib.dir / rows[0][4]) and joined[f"{key}#s#picture-2"][0]["label"] == "Figure 2"
    state = fi.states(conn, lib.dir)[key]
    assert state == {"figures": 6, "had": 6, "settled": True, "fallback": False, "sources": "pmc-cloud", "why": None, "at": state["at"]}
    assert not fi.wanting(state)  # every figure had: the fallback has nothing to answer for


def test_a_second_run_asks_nothing_and_a_file_gone_is_fetched_alone(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    fi.from_services(conn, lib.dir, key)
    seen = len(epmc.seen)
    again = fi.from_services(conn, lib.dir, key)
    assert (again.had, again.new, again.settled) == (6, 0, True) and len(epmc.seen) == seen  # idempotent: nothing asked
    gone = lib.dir / conn.execute("SELECT file FROM figure_images WHERE paper = ? AND ordinal = 3", (key,)).fetchone()[0]
    gone.write_bytes(b"half an image")  # broken on disk: its hash no longer holds
    third = fi.from_services(conn, lib.dir, key)
    assert (third.had, third.new) == (6, 1) and gone.read_bytes() == images[NAMES[2]]
    assert [p.rsplit("/", 1)[1] for p in epmc.seen[seen:] if "g00" in p] == [NAMES[2]]  # the one file, not the six


def test_a_file_failing_its_md5_is_not_taken_and_europe_pmc_s_zip_is_asked_for_it(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES)}, md5="0" * 32)
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (200, "application/zip", THUMBS)
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.sources, a.settled) == (6, {"europepmc": 6}, True)
    rows = conn.execute("SELECT file, source, mime, width FROM figure_images WHERE paper = ? ORDER BY ordinal", (key,)).fetchall()
    assert rows[0][0].endswith("micromachines-15-00851-g001.gif") and {r[1] for r in rows} == {"europepmc"} and rows[0][2] == "image/gif"
    assert f"/rest/{PMCID}/supplementaryFiles?includeInlineImage=yes" in epmc.seen
    assert not list((lib.dir / "figures").glob("*.zip*"))  # the zip gone once read
    assert not list((lib.dir / "inbox").glob("*.zip*"))  # and never in the inbox, where a store's papers wait to be filed


def test_a_file_the_cloud_fails_to_give_costs_only_itself_and_one_with_no_md5_is_not_taken(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    epmc.routes[f"/cloud/{PMCID}.1/{NAMES[2]}"] = (500, "text/plain", b"slow down")
    meta = json.loads(epmc.routes[f"/cloud/{PMCID}.1/{PMCID}.1.json"][2])
    meta["media_urls"] = [u.split("?")[0] if NAMES[4] in u else u for u in meta["media_urls"]]  # listed with nothing to check it by
    epmc.json(f"/cloud/{PMCID}.1/{PMCID}.1.json", meta)
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.sources, a.settled) == (4, {"pmc-cloud": 4}, False)  # the four fetched kept; the two not taken asked again
    assert a.why[0] == f"PMC Cloud: 2 not taken: {NAMES[2]}: HTTP 500; {NAMES[4]} is listed with no MD5"
    assert f"/cloud/{PMCID}.1/{NAMES[4]}" not in epmc.seen  # not even fetched
    assert sorted(r[0] for r in conn.execute("SELECT ordinal FROM figure_images WHERE paper = ?", (key,))) == [1, 2, 4, 6]
    assert fi.wanting(fi.states(conn, lib.dir)[key])


def test_a_file_that_is_no_image_leaves_its_figure_missing_and_the_paper_unsettled(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    images[NAMES[2]] = b"<html>not the figure</html>"  # its MD5 holds, as the record gives it, and it is no image
    cloud(epmc, images)
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.new, a.settled) == (5, 5, False)
    assert [f.ordinal for f in a.missing] == [3]
    assert len(a.why) == 1 and "not an image" in a.why[0]
    assert sorted(r[0] for r in conn.execute("SELECT ordinal FROM figure_images WHERE paper = ?", (key,))) == [1, 2, 4, 5, 6]
    assert tuple(conn.execute("SELECT figures, had, settled FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()) == (6, 5, 0)
    assert fi.wanting(fi.states(conn, lib.dir)[key])


def test_of_two_files_for_a_graphic_the_full_image_is_kept_before_the_thumbnail(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, n in enumerate(NAMES):
            z.writestr(n.replace(".jpg", ".gif"), zipfile.ZipFile(io.BytesIO(THUMBS)).read(n.replace(".jpg", ".gif")))
            z.writestr(n, jpeg(i, (400, 300)))
        z.writestr("mmc1.pdf", b"%PDF supplementary")
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (200, "application/zip", buf.getvalue())
    a = fi.from_services(conn, lib.dir, key)  # the Cloud holds no version of it
    assert a.sources == {"europepmc": 6}
    assert [r[0] for r in conn.execute("SELECT width FROM figure_images WHERE paper = ?", (key,))] == [400] * 6


def test_an_author_manuscript_is_settled_without_asking_europe_pmc(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    epmc.cloud(PMCID, manuscript=True)  # XML and text, no PDF and no image
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.why) == (0, True, [f"PMC Cloud: 6 of 6: {fi.MANUSCRIPT}"])
    assert not [p for p in epmc.seen if p.startswith("/rest/")]
    seen = len(epmc.seen)
    assert fi.from_services(conn, lib.dir, key).settled and len(epmc.seen) == seen  # passed by
    fi.from_services(conn, lib.dir, key, again=True)
    assert len(epmc.seen) > seen  # unless asked again


def test_neither_service_holding_it_is_settled(tmp_path, epmc):
    lib, conn, key = library(tmp_path)  # the Cloud lists no version of it, and Europe PMC answers 404
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.why) == (0, True, ["PMC Cloud: not in its open-access datasets", "Europe PMC: HTTP 404"])
    assert tuple(conn.execute("SELECT figures, had, settled FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()) == (6, 0, 1)


def test_a_500_from_europe_pmc_is_asked_again_and_never_its_settled_answer(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (500, "text/html", b"<html>Error: 500</html>")
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.why) == (0, False, ["PMC Cloud: not in its open-access datasets", "Europe PMC: HTTP 500"])
    assert len([p for p in epmc.seen if p.startswith("/rest/")]) == fi.RETRIES + 1  # a server's fault, asked again as a busy answer is
    assert conn.execute("SELECT settled FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()[0] == 0
    assert fi.wanting(fi.states(conn, lib.dir)[key])
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (200, "application/zip", THUMBS)  # the fault over: the next ask has them
    b = fi.from_services(conn, lib.dir, key)
    assert (b.had, b.settled, b.sources) == (6, True, {"europepmc": 6})


def test_a_busy_answer_is_asked_again(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    busy = {"n": 0}
    first = epmc.routes[f"/cloud/{PMCID}.1/{NAMES[0]}"]

    def flaky(q):
        busy["n"] += 1
        return (503, "text/plain", b"slow down") if busy["n"] < 3 else first

    epmc.routes[f"/cloud/{PMCID}.1/{NAMES[0]}"] = flaky
    assert fi.from_services(conn, lib.dir, key).had == 6 and busy["n"] == 3


def test_an_answer_cut_short_is_asked_again(tmp_path, monkeypatch):
    calls = {"n": 0}

    class Resp:
        def __init__(self, body, size):
            self.body, self.headers = io.BytesIO(body), {"Content-Length": str(size)}

        def read(self, n=-1):
            return self.body.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError(104, "Connection reset by peer")
        return Resp(b"abc", 10) if calls["n"] == 2 else Resp(b"abcdefghij", 10)

    monkeypatch.setattr(fi.urllib.request, "urlopen", urlopen)
    assert fi.fetch("http://x/y", 5, host="cloud", most=100) == b"abcdefghij" and calls["n"] == 3
    calls["n"] = 10  # every answer short from here
    monkeypatch.setattr(fi, "RETRIES", 1)
    monkeypatch.setattr(fi.urllib.request, "urlopen", lambda req, timeout: Resp(b"abc", 10))
    with pytest.raises(fi.Unreachable):
        fi.fetch("http://x/y", 5, host="cloud", most=100)


def test_services_that_cannot_be_reached_settle_nothing(tmp_path):
    lib, conn, key = library(tmp_path)  # every base points at a port nothing answers on
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.unreachable) == (0, False, True)
    assert conn.execute("SELECT settled FROM figure_image_asks WHERE paper = ?", (key,)).fetchone()[0] == 0


def test_europe_pmc_is_not_asked_in_the_ask_that_found_the_cloud_unreachable(tmp_path, monkeypatch, epmc):
    lib, conn, key = library(tmp_path)
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (200, "application/zip", THUMBS)  # Europe PMC has them all
    monkeypatch.setenv("LITRAG_PMC_CLOUD_URL", "http://127.0.0.1:9/cloud")  # nothing answers there
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.unreachable) == (0, False, True)
    assert asked(epmc) == []  # the network is the trouble: the second service is left for the next ask
    assert fi.wanting(fi.states(conn, lib.dir)[key])
    monkeypatch.setenv("LITRAG_PMC_CLOUD_URL", f"{epmc.url}/cloud")  # the Cloud answering again
    b = fi.from_services(conn, lib.dir, key)
    assert (b.had, b.settled, b.sources) == (6, True, {"europepmc": 6})


def test_a_cloud_file_that_keeps_breaking_off_keeps_the_files_before_it_and_ends_the_ask(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    epmc.routes[f"/rest/{PMCID}/supplementaryFiles"] = (200, "application/zip", THUMBS)
    epmc.routes[f"/cloud/{PMCID}.1/{NAMES[2]}"] = (200, "image/jpeg", b"cut", {"Content-Length": "999"})  # every answer cut short
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.sources, a.settled, a.unreachable) == (2, {"pmc-cloud": 2}, False, True)
    assert not [p for p in asked(epmc) if p.startswith("/rest/")]
    assert all(f"/cloud/{PMCID}.1/{n}" not in epmc.seen for n in NAMES[3:])  # nor the files after it
    assert sorted(r[0] for r in conn.execute("SELECT ordinal FROM figure_images WHERE paper = ?", (key,))) == [1, 2]


def test_no_pmcid_is_asked_nothing_and_a_pdf_paper_has_no_images(tmp_path, epmc):
    lib, conn, key = library(tmp_path, pmcid=None)
    a = fi.from_services(conn, lib.dir, key)
    assert (a.had, a.settled, a.why) == (0, True, ["no PMCID to ask by"]) and not asked(epmc)
    conn.execute("UPDATE papers SET format = 'pdf' WHERE key = ?", (key,))
    assert fi.from_services(conn, lib.dir, key).figures == 0 and fi.images_for(conn, lib.dir, key) == {}


def test_an_image_joins_only_the_node_its_number_or_its_caption_names(tmp_path, epmc):
    xml = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta><article-id pub-id-type="pmcid">PMC11278924</article-id></article-meta></front>
    <body><sec><fig id="a"><label>Figure 1</label><caption><p>The first.</p></caption><graphic xlink:href="micromachines-15-00851-g001"/></fig>
    <fig id="b"><caption><p>A figure the paper gives no number, said at length.</p></caption><graphic xlink:href="micromachines-15-00851-g002"/></fig>
    <fig id="c"><label>Figure 1</label><caption><p>Numbered as the first is.</p></caption><graphic xlink:href="micromachines-15-00851-g003"/></fig>
    <fig id="d"><label>Figure 9</label><caption><p>No node gives this number.</p></caption><graphic xlink:href="micromachines-15-00851-g004"/></fig></sec></body></article>"""
    lib, conn, key = library(tmp_path, xml=xml, caps=["Figure 1 The first.", "A figure the paper gives no number, said at length.", "Figure 1 Numbered as the first is.", "Figure 4 Another."])
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES)})
    assert fi.from_services(conn, lib.dir, key).had == 4
    joined = {n: [i["label"] for i in v] for n, v in fi.images_for(conn, lib.dir, key).items()}
    # figure 1 to the first node numbered 1; the unnumbered one by its caption; the repeat of 1 and the 9 to none — kept, not shown
    assert joined == {f"{key}#s#picture-1": ["Figure 1"], f"{key}#s#picture-2": [None]}
    assert conn.execute("SELECT COUNT(*) FROM figure_images WHERE paper = ?", (key,)).fetchone()[0] == 4


# -- from a PDF of the paper ------------------------------------------------------------------------------------


def test_a_figure_without_an_image_is_cut_from_the_pdf_kept_beside_the_xml(tmp_path):
    xml = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec>""" + b"".join(
        f'<fig id="f{i}"><label>Figure {i}</label><caption><p>What figure {i} shows.</p></caption><graphic xlink:href="f{i}"/></fig>'.encode() for i in (1, 2, 3)) + b"</sec></body></article>"
    lib, conn, key = library(tmp_path, xml=xml, caps=[f"Figure {i} What figure {i} shows." for i in (1, 2, 3)])
    pdf = lib.papers_dir / "paper.figures.pdf"
    shutil.copy(FIXTURES / "charts" / "page_paper.pdf", pdf)  # figure 1 drawn as vectors on page 1, figure 2 an image on page 2
    out = fi.from_pdf(conn, lib.dir, key, pdf)
    assert out == {"cut": 1, "figures": 3}
    row = conn.execute("SELECT ordinal, part, figure_label, label, source, href, mime, width FROM figure_images WHERE paper = ?", (key,)).fetchone()
    assert row[:7] == (2, 1, "2", "Figure 2", "pdf", None, "image/png") and row[7] > 100
    assert list(fi.images_for(conn, lib.dir, key)) == [f"{key}#s#picture-2"]
    assert fi.from_pdf(conn, lib.dir, key, pdf) == {"cut": 0, "figures": 2}  # what was cut is not cut again


def test_the_summary_counts_papers_figures_and_who_lacks_them(tmp_path, epmc):
    lib, conn, key = library(tmp_path)
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES[:4])})
    fi.from_services(conn, lib.dir, key)
    s = fi.summary(conn, lib.dir)
    assert {k: s[k] for k in ("papers", "answered", "with_figures", "any", "every", "figures", "had", "sources")} == {
        "papers": 1, "answered": 1, "with_figures": 1, "any": 1, "every": 0, "figures": 6, "had": 4, "sources": {"pmc-cloud": 4}}
    assert s["without"] == {"MDPI": 1} and s["why"] == {"PMC Cloud: no file of that name; Europe PMC: HTTP 404": 1}


# -- the worker -------------------------------------------------------------------------------------------------


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _parse_as_reader(monkeypatch, caps=None):
    """parse_one stood in for: an XML read into PMC11278924's six figures."""
    from litrag_parser.worker import Worker

    def parse_one(self, lib, key, path, *rest, **kw):
        conn = open_store(lib.store_path)
        conn.execute("DELETE FROM nodes WHERE paper = ?", (key,))
        rows = [(f"{key}#s", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "")]
        for i, cap in enumerate(caps or [f"Figure {i} What figure {i} shows." for i in range(1, 7)], 1):
            rows.append((f"{key}#s#picture-{i}", key, f"{key}#s", i, 2, "picture", "picture", "results", None, "[]", ""))
            rows.append((f"{key}#s#picture-{i}#caption-1", key, f"{key}#s#picture-{i}", 0, 3, "caption", "caption", "results", None, "[]", cap))
        conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("UPDATE papers SET status = 'parsed', title = 'Computational and Experimental Characterization of Aligned Collagen across Varied Crosslinking Degrees' WHERE key = ?", (key,))
        conn.commit()
        conn.close()

    monkeypatch.setattr(Worker, "parse_one", parse_one)


def test_an_xml_filed_fetches_its_figure_images_and_the_tree_draws_them(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_FIGURE_IMAGES", "on")
    monkeypatch.setenv("LITRAG_FIGURES", "off")  # as a library is filed in bulk: the images come all the same
    _parse_as_reader(monkeypatch)
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES)})
    lib = create_library(tmp_path, "Filed")
    (lib.inbox_dir / "PMC11278924.xml").write_bytes(XML)
    capsys.readouterr()
    w = Worker(tmp_path)
    w.do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(lib.inbox_dir / "PMC11278924.xml")]})
    events = _events(capsys)
    assert [e["message"] for e in events if e["event"] == "log"] == ["figure images: 6 of 6 figures have one (6 from the PMC Cloud Service)"]
    assert any(e["event"] == "progress" and e["label"] == "Fetching the figure images of paper 1 of 1" for e in events)
    assert not [p for p in epmc.seen if p.endswith(".pdf") or p.startswith("/oa/")]  # every figure had: no PDF asked, the figures being off
    conn = open_store(lib.store_path)
    key = conn.execute("SELECT key FROM papers").fetchone()[0]
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'figure-images'", (key,))] == ["figure images: 6 of 6 figures have one (6 from the PMC Cloud Service)"]
    conn.close()
    w.answer({"id": "t", "op": "tree", "lib": lib.id, "key": key})
    tree = _events(capsys)[-1]
    assert sorted(tree["images"]) == sorted(f"{key}#s#picture-{i}" for i in range(1, 7)) and tree["images"][f"{key}#s#picture-1"][0]["mime"] == "image/jpeg"
    w.answer({"id": "p", "op": "papers", "lib": lib.id})
    assert _events(capsys)[-1]["papers"][0]["images"]["had"] == 6


def test_with_the_figures_off_a_filing_asks_for_no_pdf_and_the_op_asks_pmc_and_cuts_from_it(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_FIGURE_IMAGES", "on")
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    monkeypatch.setenv("LITRAG_OPEN_COPIES", "on")
    monkeypatch.setenv("LITRAG_OPENALEX", "on")
    monkeypatch.setenv("LITRAG_OPENALEX_URL", f"{epmc.url}/openalex")
    _parse_as_reader(monkeypatch, caps=["Figure 1 Fold change.", "Figure 2 Gene expression.", "Figure 3 More."])
    monkeypatch.setattr("litrag_parser.acquire.first_pages", lambda path, pages=3: "doi:10.3390/mi15070851")  # page_paper.pdf names the paper
    pdf = (FIXTURES / "charts" / "page_paper.pdf").read_bytes()
    record = {**RECORD, "media_urls": [], "pdf_url": f"s3://pmc-oa-opendata/{PMCID}.1/{PMCID}.1.pdf?md5={hashlib.md5(pdf).hexdigest()}"}
    epmc.json(f"/cloud/{PMCID}.1/{PMCID}.1.json", record)
    epmc.routes[f"/cloud/{PMCID}.1/{PMCID}.1.pdf"] = (200, "application/pdf", pdf)
    lib = create_library(tmp_path, "Fallback")
    (lib.inbox_dir / "PMC11278924.xml").write_bytes(XML)
    capsys.readouterr()
    w = Worker(tmp_path)
    w.do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(lib.inbox_dir / "PMC11278924.xml")]})
    messages = [e["message"] for e in _events(capsys) if e["event"] == "log"]
    assert messages == ["figure images: 0 of 6 figures have one; for the rest — PMC Cloud: 6 of 6: no file of that name; Europe PMC: HTTP 404"]
    # the figures off: the image files were asked for, and no PDF of the paper, of PMC, OpenAlex or an open copy's host
    assert not [p for p in epmc.seen if p.endswith(".pdf") or p.startswith(("/oa/", "/openalex/"))]
    w.answer({"id": "p", "op": "papers", "lib": lib.id})
    images = _events(capsys)[-1]["papers"][0]["images"]
    assert (images["had"], images["settled"], images["fallback"]) == (0, True, False) and fi.wanting(images)  # the fetch is still to be asked for
    w.do_figure_images({"id": "f", "op": "figure_images", "lib": lib.id})  # asked for: the off switch does not stop it
    events = _events(capsys)
    messages = [e["message"] for e in events if e["event"] == "log"]
    assert any(m.endswith("kept for its figures, from the PMC Cloud Service") for m in messages)
    assert messages[-1] == "figure images: 1 of 3 figures without one cut from doi_10.3390_mi15070851.figures.pdf"
    done = [e for e in events if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["kept"], done["had"]) == (1, 0, 1, 1)
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT figure_label, source FROM figure_images")] == [("2", "pdf")]
    state = fi.states(conn, lib.dir)[conn.execute("SELECT key FROM papers").fetchone()[0]]
    conn.close()
    assert (state["had"], state["fallback"]) == (1, True) and not fi.wanting(state)  # a PDF searched: answered for good
    seen = len(epmc.seen)
    w.do_figure_images({"id": "g", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"]) == (0, 1) and len(epmc.seen) == seen


def test_a_fallback_that_broke_off_is_tried_again_and_one_answered_for_good_is_not(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_OPEN_COPIES", "on")
    monkeypatch.setenv("LITRAG_OPENALEX", "on")
    monkeypatch.setenv("LITRAG_OPENALEX_URL", f"{epmc.url}/openalex")
    lib, conn, key = library(tmp_path)
    conn.close()
    epmc.cloud(PMCID, manuscript=True)  # the services settle at once: an author manuscript, no image file and no PDF
    work = "/openalex/works/doi:10.3390/mi15070851"
    epmc.routes[work] = (500, "text/plain", b"OpenAlex is down")
    capsys.readouterr()
    w = Worker(tmp_path)

    def run(req_id):
        w.do_figure_images({"id": req_id, "op": "figure_images", "lib": lib.id})
        done = [e for e in _events(capsys) if e["event"] == "done"][-1]
        conn = open_store(lib.store_path)
        try:
            return done, fi.states(conn, lib.dir)[key], len([p for p in epmc.seen if p.startswith(work)])
        finally:
            conn.close()

    done, state, asked_openalex = run("a")
    assert (state["settled"], state["fallback"], asked_openalex) == (True, False, 1)  # the services answered; OpenAlex could not be asked
    assert fi.wanting(state)
    epmc.json(work, {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.3390/mi15070851", "open_access": {"is_oa": False, "oa_url": None}})
    done, state, asked_openalex = run("b")
    assert (done["asked"], done["passed"], asked_openalex) == (1, 0, 2)  # tried again, though the services had settled
    assert (state["settled"], state["fallback"]) == (True, True) and not fi.wanting(state)  # PMC has no PDF and OpenAlex names no open copy: for good
    seen = len(epmc.seen)
    done, state, asked_openalex = run("c")
    assert (done["asked"], done["passed"], asked_openalex) == (0, 1, 2) and len(epmc.seen) == seen  # passed by: nothing asked
    w.do_figure_images({"id": "d", "op": "figure_images", "lib": lib.id, "again": True})
    assert len([p for p in epmc.seen if p.startswith(work)]) == 3  # unless asked again


def _manuscript_with_no_pdf_anywhere(tmp_path, monkeypatch, epmc, oa_url):
    """A library of one author manuscript (no image file, no PDF at PMC) whose open copy OpenAlex names at `oa_url`."""
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    monkeypatch.setenv("LITRAG_OPEN_COPIES", "on")
    monkeypatch.setenv("LITRAG_OPENALEX", "on")
    monkeypatch.setenv("LITRAG_OPENALEX_URL", f"{epmc.url}/openalex")
    lib, conn, key = library(tmp_path, caps=["Figure 1 Fold change.", "Figure 2 Gene expression.", "Figure 3 More."])
    conn.close()
    epmc.cloud(PMCID, manuscript=True)
    epmc.json("/openalex/works/doi:10.3390/mi15070851", {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.3390/mi15070851", "open_access": {"is_oa": bool(oa_url), "oa_url": oa_url}})
    return lib, key


def _state(lib, key):
    conn = open_store(lib.store_path)
    try:
        return fi.states(conn, lib.dir)[key]
    finally:
        conn.close()


def test_an_open_copy_s_host_refusing_a_program_is_an_answer_for_good(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    lib, key = _manuscript_with_no_pdf_anywhere(tmp_path, monkeypatch, epmc, f"{epmc.url}/repo/paper.pdf")
    epmc.routes["/repo/paper.pdf"] = (403, "text/html", b"<html>Forbidden</html>")
    w = Worker(tmp_path)
    w.do_figure_images({"id": "a", "op": "figure_images", "lib": lib.id})
    assert epmc.seen.count("/repo/paper.pdf") == 1
    state = _state(lib, key)
    assert (state["settled"], state["fallback"]) == (True, True) and not fi.wanting(state)  # a 403 is the host's answer, not a fault on the way
    seen = len(epmc.seen)
    capsys.readouterr()
    w.do_figure_images({"id": "b", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"]) == (0, 1) and len(epmc.seen) == seen  # the host that refused is not asked again


def test_a_fallback_passed_by_with_open_copies_off_stands_while_off_and_is_asked_once_on(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    lib, key = _manuscript_with_no_pdf_anywhere(tmp_path, monkeypatch, epmc, None)
    work = "/openalex/works/doi:10.3390/mi15070851"
    w = Worker(tmp_path)
    for switch, value in (("LITRAG_OPEN_COPIES", "off"), ("LITRAG_OPENALEX", "off")):
        monkeypatch.setenv("LITRAG_OPEN_COPIES", "on")
        monkeypatch.setenv("LITRAG_OPENALEX", "on")
        monkeypatch.setenv(switch, value)
        w.do_figure_images({"id": f"a{switch}", "op": "figure_images", "lib": lib.id, "again": True})
        state = _state(lib, key)
        assert (state["settled"], state["fallback"]) == (True, True) and not fi.wanting(state), switch  # nothing more to ask while it is off
        capsys.readouterr()
        seen = len(epmc.seen)
        w.do_figure_images({"id": f"b{switch}", "op": "figure_images", "lib": lib.id})
        done = [e for e in _events(capsys) if e["event"] == "done"][-1]
        assert (done["asked"], done["passed"]) == (0, 1) and len(epmc.seen) == seen, switch  # neither counted nor asked again
    assert not [p for p in epmc.seen if p.startswith(work)]
    monkeypatch.setenv("LITRAG_OPENALEX", "on")  # both on again: the answer given with a switch off no longer stands
    state = _state(lib, key)
    assert not state["fallback"] and fi.wanting(state)
    w.do_figure_images({"id": "c", "op": "figure_images", "lib": lib.id})
    assert [p for p in epmc.seen if p.startswith(work)]
    state = _state(lib, key)
    assert state["fallback"] and not fi.wanting(state)  # OpenAlex names no open copy: for good


def test_a_pdf_kept_beside_a_paper_after_the_fallback_answered_is_cut_from(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    lib, key = _manuscript_with_no_pdf_anywhere(tmp_path, monkeypatch, epmc, None)
    monkeypatch.setattr("litrag_parser.acquire.first_pages", lambda path, pages=3: "doi:10.3390/mi15070851")  # page_paper.pdf names the paper
    w = Worker(tmp_path)
    w.do_figure_images({"id": "a", "op": "figure_images", "lib": lib.id})
    state = _state(lib, key)
    assert (state["had"], state["fallback"]) == (0, True) and not fi.wanting(state)  # no PDF anywhere: answered for good
    dropped = tmp_path / "dropped.pdf"
    shutil.copy(FIXTURES / "charts" / "page_paper.pdf", dropped)  # then a person drops its PDF on the paper
    w.do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped)], "known": {str(dropped): {"paper": key}}, "offline": True})
    assert fi.wanting(_state(lib, key))  # a PDF to cut from now: the earlier answer stands no longer
    capsys.readouterr()
    w.do_figure_images({"id": "b", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["had"]) == (1, 0, 1)
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT figure_label, source FROM figure_images")] == [("2", "pdf")]  # figure 2, an image on page 2 of it
    conn.close()
    assert not fi.wanting(_state(lib, key))


def test_a_settled_paper_whose_image_is_gone_from_disk_says_so_and_is_fetched_again(tmp_path, capsys, epmc):
    from litrag_parser.worker import Worker

    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    w = Worker(tmp_path)
    w.do_figure_images({"id": "f", "op": "figure_images", "lib": lib.id})
    gone = lib.dir / conn.execute("SELECT file FROM figure_images WHERE paper = ? AND ordinal = 3", (key,)).fetchone()[0]
    gone.unlink()
    capsys.readouterr()
    w.answer({"id": "p", "op": "papers", "lib": lib.id})
    state = _events(capsys)[-1]["papers"][0]["images"]
    assert (state["figures"], state["had"], state["settled"]) == (6, 5, False) and fi.wanting(state)  # five on disk, not the six once had
    seen = len(epmc.seen)
    w.do_figure_images({"id": "g", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["had"]) == (1, 0, 6) and gone.read_bytes() == images[NAMES[2]]
    assert [p.rsplit("/", 1)[1] for p in epmc.seen[seen:] if "g00" in p] == [NAMES[2]]  # the one file, not the six
    state = fi.states(conn, lib.dir)[key]
    conn.close()
    assert (state["had"], state["settled"]) == (6, True) and not fi.wanting(state)


def test_the_figure_images_op_over_a_library_resumes_and_counts(tmp_path, monkeypatch, capsys, epmc):
    from litrag_parser.worker import Worker

    lib, conn, key = library(tmp_path)
    conn.close()
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES)})
    capsys.readouterr()
    w = Worker(tmp_path)
    w.do_figure_images({"id": "f", "op": "figure_images", "lib": lib.id})  # the off switch is not looked at: this is asked for
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert {k: done[k] for k in ("op", "asked", "passed", "kept", "papers", "every", "figures", "had", "sources")} == {
        "op": "figure_images", "asked": 1, "passed": 0, "kept": 0, "papers": 1, "every": 1, "figures": 6, "had": 6, "sources": {"pmc-cloud": 6}}
    seen = len(epmc.seen)
    w.do_figure_images({"id": "g", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["every"]) == (0, 1, 1) and len(epmc.seen) == seen


def test_a_merge_brings_a_paper_s_figure_images_and_its_settled_ask(tmp_path, epmc):
    from litrag_parser import projects
    from litrag_parser.library import open_library

    lib, conn, key = library(tmp_path)
    cloud(epmc, {n: jpeg(i) for i, n in enumerate(NAMES)})
    fi.from_services(conn, lib.dir, key)
    conn.close()
    out = projects.merge(tmp_path, [lib.id], "Merged")
    assert out["filed"] == 1
    t = open_library(tmp_path, "merged")
    tconn = open_store(t.store_path)
    rows = tconn.execute("SELECT file, sha256 FROM figure_images WHERE paper = ? ORDER BY ordinal", (key,)).fetchall()
    assert len(rows) == 6 and all(hashlib.sha256((t.home / r[0]).read_bytes()).hexdigest() == r[1] for r in rows)
    assert (lib.dir / "figures").is_dir()  # the source keeps its own
    seen = len(epmc.seen)
    assert fi.from_services(tconn, t.home, key).settled and len(epmc.seen) == seen  # nothing asked again


# -- the shared store: a paper's images live with the paper, and a project asks for its own -------------------


def test_a_project_of_the_shared_store_keeps_its_images_with_the_paper_and_asks_only_for_its_own(tmp_path, monkeypatch, capsys, epmc):
    """The images are the paper's, so they are stored where its rows and files are (`<root>/_shared`),
    the tree draws them from there, and another project's Fetch figure images neither asks for this
    paper nor counts it (DESIGN.md R4.6: a project is the papers it holds)."""
    from litrag_parser.library import shared_dir
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_FIGURE_IMAGES", "off")  # filed with no images: the op is what fetches them
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    _parse_as_reader(monkeypatch)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    mine = create_library(tmp_path, "Mine", shared=True)
    create_library(tmp_path, "Other", shared=True)
    (mine.inbox_dir / "PMC11278924.xml").write_bytes(XML)
    w = Worker(tmp_path)
    w.do_ingest({"id": "i", "op": "ingest", "lib": mine.id, "paths": [str(mine.inbox_dir / "PMC11278924.xml")], "figures_pdf": False})
    capsys.readouterr()
    seen = len(asked(epmc))
    w.do_figure_images({"id": "o", "op": "figure_images", "lib": "other"})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["papers"], done["had"], done["sources"]) == (0, 0, 0, 0, {}) and len(asked(epmc)) == seen  # not its paper
    w.do_figure_images({"id": "m", "op": "figure_images", "lib": "mine"})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["papers"], done["every"], done["had"], done["sources"]) == (1, 1, 1, 6, {"pmc-cloud": 6})
    conn = open_store(mine.store_path)
    key = conn.execute("SELECT key FROM papers").fetchone()[0]
    files = [r[0] for r in conn.execute("SELECT file FROM figure_images WHERE paper = ? ORDER BY ordinal", (key,))]
    conn.close()
    assert [(shared_dir(tmp_path) / f).read_bytes() for f in files] == [images[n] for n in NAMES]  # beside the store that names them
    assert not (mine.dir / "figures").exists()
    w.answer({"id": "t", "op": "tree", "lib": "mine", "key": key})
    tree = _events(capsys)[-1]
    assert [Path(tree["images"][f"{key}#s#picture-{i}"][0]["path"]).read_bytes() for i in range(1, 7)] == [images[n] for n in NAMES]
    w.do_figure_images({"id": "o2", "op": "figure_images", "lib": "other"})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["papers"], done["had"], done["sources"]) == (0, 0, {})  # another project's images are not this one's to count


def test_a_library_moved_into_the_shared_store_brings_its_figure_images_and_its_settled_ask(tmp_path, capsys, epmc):
    """migrate.py moves a paper "with every row derived from it and its files": its images are both."""
    from litrag_parser import migrate
    from litrag_parser.library import open_library, shared_dir
    from litrag_parser.worker import Worker

    lib, conn, key = library(tmp_path)
    images = {n: jpeg(i) for i, n in enumerate(NAMES)}
    cloud(epmc, images)
    assert fi.from_services(conn, lib.dir, key).had == 6
    conn.close()
    out = migrate.migrate(tmp_path)
    assert out["moved"][0]["new"] == 1
    moved = open_library(tmp_path, lib.id)
    assert moved.shared
    tconn = open_store(moved.store_path)
    files = [r[0] for r in tconn.execute("SELECT file FROM figure_images WHERE paper = ? ORDER BY ordinal", (key,))]
    tconn.close()
    assert [(shared_dir(tmp_path) / f).read_bytes() for f in files] == [images[n] for n in NAMES]
    assert (lib.dir / files[0]).is_file()  # the old files left where they were
    capsys.readouterr()
    seen = len(asked(epmc))
    w = Worker(tmp_path)
    w.do_figure_images({"id": "f", "op": "figure_images", "lib": lib.id})
    done = [e for e in _events(capsys) if e["event"] == "done"][-1]
    assert (done["asked"], done["passed"], done["every"], done["had"]) == (0, 1, 1, 6) and len(asked(epmc)) == seen  # nothing asked again
    w.answer({"id": "t", "op": "tree", "lib": lib.id, "key": key})
    tree = _events(capsys)[-1]
    assert [Path(tree["images"][f"{key}#s#picture-{i}"][0]["path"]).read_bytes() for i in range(1, 7)] == [images[n] for n in NAMES]
