"""A paper's figures read into rows: the PDF's own words joined into phrases at any angle, a
figure cut from its page as vectors or as the image it is, every plot and value a row a person
can SELECT, found again after a rebuild, carried by the worker's ops and into a query's
hydrated hit."""

import json
import shutil
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from litrag_parser import figures, retrieve
from litrag_parser.store import file_paper, open_store

CHARTS = Path(__file__).parent / "fixtures" / "charts"


def _words(name):
    pdf = pdfium.PdfDocument(str(CHARTS / f"{name}.pdf"))
    w, h = pdf[0].get_size()
    return [x.text for x in figures.text_words(pdf[0], (0, 0, w, h), 1.0)]


def test_the_text_layer_gives_phrases_at_any_angle():
    simple = _words("bars_simple")
    assert ["COL/PLA_Dry", "COL/PLA_Wet", "PLA_Dry", "PLA_Wet"] == simple[:4]  # touching labels kept apart
    assert "Maximum Load (N)" in simple  # set on its side, read upwards
    assert {"Circle 50", "Rhombus 50", "TCP", "FA length (µm)"} <= set(_words("bars_outlined"))  # slanted at 45°
    assert ["10^2", "10^3", "10^4", "10^5"] == [w for w in _words("bars_log") if w.startswith("10")]  # an exponent raised off the line
    assert "Days of Culture" in _words("points_days")


def _paper_pdf(tmp_path, names):
    """One PDF, a fixture chart a page."""
    out = pdfium.PdfDocument.new()
    for n in names:
        src = pdfium.PdfDocument(str(CHARTS / f"{n}.pdf"))
        out.import_pages(src)
    path = tmp_path / "paper.pdf"
    out.save(str(path))
    return path


def _store(tmp_path, names, key_doi="10.1000/fig"):
    """A store with one paper whose pictures are the fixture pages, whole."""
    conn = open_store(tmp_path / "store.sqlite")
    pdf_path = _paper_pdf(tmp_path, names)
    key = file_paper(conn, title="figures", file=pdf_path.name, sha256="f1", fmt="pdf", doi=key_doi, pmid=None, pmcid=None, now="t").key
    pdf = pdfium.PdfDocument(str(pdf_path))
    rows = [(f"{key}#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "", None, None, None, None, None)]
    for i in range(len(pdf)):
        w, h = pdf[i].get_size()
        rows.append((f"{key}#section-1#picture-{i + 1}", key, f"{key}#section-1", i + 1, 2, "picture", "picture", "results", None, '["3 Results"]', "", i + 1, 0.0, 0.0, w, h))
    rows.append((f"{key}#section-1#paragraph-1", key, f"{key}#section-1", 0, 2, "paragraph", "text", "results", None, '["3 Results"]',
                 "The modulus doubled with alignment (Figure 1B), as the strength did.", 1, None, None, None, None))
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (key,))
    conn.commit()
    return conn, key, pdf_path


def test_a_figure_is_cut_as_vectors_or_as_the_image_it_is(tmp_path):
    pdf = pdfium.PdfDocument(str(CHARTS / "bars_grouped.pdf"))
    w, h = pdf[0].get_size()
    img, words, source, scale = figures.crop(pdf, 1, (0, 0, w, h))
    assert source == "vector" and words and round(scale * 72) == figures.VECTOR_DPI
    raster = tmp_path / "scan.pdf"
    Image.open(CHARTS / "bars_grouped.png").convert("RGB").save(raster, resolution=300)
    pdf = pdfium.PdfDocument(str(raster))
    w, h = pdf[0].get_size()
    img, words, source, scale = figures.crop(pdf, 1, (0, 0, w, h))
    assert source == "raster" and words is None and abs(scale * 72 - 300) < 5  # at its own resolution, for OCR


def test_a_paper_s_figures_become_rows_and_are_found_again_after_a_rebuild(tmp_path):
    conn, key, pdf_path = _store(tmp_path, ["panels_two", "bars_grouped"])
    out = figures.read_paper(conn, key, pdf_path)
    assert out == {**out, "figures": 2, "plots": 3, "read": 3} and out["values"] == 2 + 2 + 18
    assert figures.is_read(conn, key)
    rows = conn.execute("SELECT figure, plot, panel, kind, status, source, y_label, y_unit, y_scale FROM charts ORDER BY figure, plot").fetchall()
    assert [tuple(r)[2:] for r in rows] == [("A", "bar", "read", "vector", "Young's modulus (MPa)", "MPa", "linear"),
                                            ("B", "bar", "read", "vector", "UTS (MPa)", "MPa", "linear"),
                                            (None, "bar", "read", "vector", "Fold change relative to TCP", None, "linear")]
    fig1 = f"{key}#section-1#picture-1"
    v = conn.execute("SELECT category, y, err_hi FROM chart_values WHERE figure = ? AND plot = 1 ORDER BY ordinal", (fig1,)).fetchall()
    assert [r["category"] for r in v] == ["Random", "ELAC"] and abs(v[1]["y"] - 31.0) < 0.5 and abs(v[1]["err_hi"] - 4.4) < 0.5
    grouped = figures.of_figure(conn, f"{key}#section-1#picture-2")[0]
    assert [s["name"] for s in grouped["series"]] == ["TCP", "Circle 50", "Rhombus 50"] and grouped["categories"][0] == "Scx"
    csv = figures.csv_of(grouped).splitlines()
    assert csv[0] == "series,category,x,y,err_lo,err_hi" and len(csv) == 19 and csv[1].startswith("TCP,Scx,,")

    # a rebuild renumbers the nodes: the plots follow their figure by its page and place
    conn.execute("UPDATE nodes SET node_id = replace(node_id, '#picture-', '#figure-x') WHERE type = 'picture'")
    conn.commit()
    assert figures.remap(conn, key) == {"moved": 3, "removed": 0, "lost": []}
    assert figures.is_read(conn, key)  # every plot followed its figure: nothing to read again
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE figure LIKE '%#figure-x%'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM chart_values WHERE figure LIKE '%#picture-%'").fetchone()[0] == 0
    # read again: the rows replaced, not added to
    figures.read_paper(conn, key, pdf_path)
    assert conn.execute("SELECT COUNT(*) FROM charts").fetchone()[0] == 3


def test_a_query_hit_carries_the_numbers_of_the_figure_it_cites_the_panel_named_first(tmp_path):
    conn, key, pdf_path = _store(tmp_path, ["panels_two"])
    para, fig = f"{key}#section-1#paragraph-1", f"{key}#section-1#picture-1"
    conn.execute("INSERT INTO edges(paper, src, dst, kind, evidence, detail, score) VALUES (?,?,?,?,?,?,?)", (key, para, fig, "cites_figure", "mention", "Figure 1", 1.0))
    conn.commit()
    figures.read_paper(conn, key, pdf_path)
    h = retrieve.hydrate(conn, para)
    (f,) = h["figures"]
    assert [p["panel"] for p in f["data"]] == ["B", "A"] and f["data"][0]["cited"] and not f["data"][1]["cited"]
    assert f["data"][0]["y"]["label"] == "UTS (MPa)"


def test_a_caption_hit_carries_the_numbers_of_the_figure_it_captions(tmp_path):
    conn, key, pdf_path = _store(tmp_path, ["panels_two"])
    h = conn.execute("SELECT bbox_b FROM nodes WHERE type = 'picture'").fetchone()[0]
    cap = f"{key}#section-1#paragraph-2"
    conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cap, key, f"{key}#section-1", 9, 2, "paragraph", "text", "results", '["3 Results"]', "FIGURE 1 (A) Modulus and (B) strength of the threads.", 1, 10.0, h - 20, 300.0, h - 5))
    conn.commit()
    figures.read_paper(conn, key, pdf_path)
    (f,) = retrieve.hydrate(conn, cap)["figures"]
    assert f["node_id"] == f"{key}#section-1#picture-1" and [p["panel"] for p in f["data"]] == ["A", "B"]


def test_small_pictures_and_unplaced_ones_are_not_read(tmp_path):
    conn, key, pdf_path = _store(tmp_path, ["bars_log"])
    conn.execute("UPDATE nodes SET bbox_r = 40, bbox_b = 30 WHERE type = 'picture'")  # a logo's size
    conn.commit()
    assert figures.pictures(conn, key) == []
    assert figures.read_paper(conn, key, pdf_path)["figures"] == 0


def test_the_worker_reads_a_library_s_figures_and_answers_for_one(tmp_path, capsys):
    from litrag_parser.worker import Worker

    from test_worker import by, talk

    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Figures"}]), "1")[0]["event"] == "library"
    lib = tmp_path / "figures"
    conn, key, pdf_path = _store(lib, ["panels_two"])
    conn.execute("UPDATE papers SET file = ? WHERE key = ?", ("paper.pdf", key))
    conn.commit()
    conn.close()
    shutil.copy(pdf_path, lib / "papers" / "paper.pdf")
    Worker(tmp_path).do_figures({"id": "f", "op": "figures", "lib": "figures"})  # what the ingest thread does
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    done = events[-1]
    assert done["event"] == "done" and done["op"] == "figures" and (done["papers"], done["plots"], done["read"]) == (1, 2, 2)
    Worker(tmp_path).do_figures({"id": "g", "op": "figures", "lib": "figures"})  # read already, by this reader: nothing to do
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["papers"] == 0
    events = talk(tmp_path, [{"id": "c", "op": "charts", "lib": "figures", "figure": f"{key}#section-1#picture-1"},
                             {"id": "p", "op": "charts", "lib": "figures", "key": key}])
    one, paper = by(events, "c")[0], by(events, "p")[0]
    assert one["event"] == "charts" and [p["panel"] for p in one["plots"]] == ["A", "B"]
    assert [f["figure"] for f in paper["figures"]] == [f"{key}#section-1#picture-1"]


# -- an XML paper's figures, from a PDF of it ------------------------------------------------------------------


def _xml_paper(conn, figures_n=3, doi="10.3390/mi15070851", caps=None):
    """A paper read from XML: its figures numbered by their captions (`caps`, one a picture), no
    page, no box."""
    caps = caps or [f"Figure {i} What figure {i} shows." for i in range(1, figures_n + 1)]
    key = file_paper(conn, title="read from XML", file="paper.xml", sha256=f"x:{doi}", fmt="jats", doi=doi, pmid=None, pmcid=None, now="t").key
    rows = [(f"{key}#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "")]
    for i, cap in enumerate(caps, 1):
        rows.append((f"{key}#section-1#picture-{i}", key, f"{key}#section-1", i, 2, "picture", "picture", "results", None, '["3 Results"]', ""))
        rows.append((f"{key}#section-1#picture-{i}#caption-1", key, f"{key}#section-1#picture-{i}", 0, 3, "caption", "caption", "results", None, '["3 Results"]', cap))
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed', file = 'paper.xml' WHERE key = ?", (key,))
    conn.commit()
    return key


def test_a_caption_is_told_from_a_sentence_that_begins_with_a_figure():
    from litrag_parser.charts import Word

    def caps(texts):
        return [n for n, _ in figures.captions([Word(10, 10 + 20 * i, 400, 22 + 20 * i, t) for i, t in enumerate(texts)])]

    assert caps(["Figure 2. Fold change", "FIGURE 3 Percent reduction", "Fig. 4. SEM images", "Figure 5: Strength", "Figure 6 | Modulus", "Fig. S1. More", "FIG. 7 Images", "Fig. s2. Less"]) == ["2", "3", "4", "5", "6", "S1", "7", "S2"]
    assert caps(["Figure 2 shows the strength", "(Figure 2)", "Figures 3 and 4 show", "as Figure 1 shows", "Figure 2.5 times"]) == []
    assert caps(["Scheme 1. Route", "SCHEME 2 Synthesis", "Chart 1: Structures", "CHART 2 Ligands"]) == ["Scheme 1", "Scheme 2", "Chart 1", "Chart 2"]  # a scheme's or a chart's number carries its kind
    assert caps(["Scheme 2 shows the route", "as Chart 1 lists"]) == []
    assert caps(["Fıgure 8. A dotless i", "Fİgure 9: A dotted capital I", "ſcheme 3. A long s"]) == ["8", "9", "Scheme 3"]  # letters re.I takes for i and s: the kind still told


def test_a_scheme_is_keyed_by_its_kind_and_never_takes_a_figure_s_number(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn, caps=["Scheme 1 The route to the monomer.", "Figure 1 What figure 1 shows.", "Figure 2 What figure 2 shows.", "Figure 1 A repeat.",
                                 "Fıgure 3 Its i dotless.", "ſcheme 2 Its s long."])
    scheme, f1, f2, _, f3, s2 = (f"{key}#section-1#picture-{i}" for i in range(1, 7))
    assert figures.figure_numbers(conn, key) == {"Scheme 1": scheme, "1": f1, "2": f2, "3": f3, "Scheme 2": s2}  # the scheme came first, "1" is still figure 1's, not its repeat's


def test_a_paper_whose_captions_number_only_schemes_and_charts_is_not_wanted(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    mixed = _xml_paper(conn, caps=["Scheme 1 The route to the monomer.", "Figure 1 What figure 1 shows.", "Figure 2 What figure 2 shows."])
    only = _xml_paper(conn, doi="10.1021/schemes", caps=["Scheme 1 Synthesis of the ligand.", "Scheme 2 Its complexes.", "Chart 1 The structures."])
    assert set(figures.figure_numbers(conn, only)) == {"Scheme 1", "Scheme 2", "Chart 1"}  # looked for, when a PDF of it is read
    assert [(w["key"], w["figures"]) for w in figures.figures_wanted(conn)] == [(mixed, 2)]  # but only figures are counted, and wanted


def test_a_plot_under_a_caption_the_paper_lacks_is_counted_not_pinned_to_the_next(tmp_path, monkeypatch):
    from litrag_parser.charts import Word

    box = [100.0, 100.0, 400.0, 300.0]
    lost, kept = ("2", Word(100, 305, 400, 317, "Figure 2. Lost from the XML")), ("1", Word(100, 80, 400, 92, "Figure 1. Above the plot"))
    assert figures.owner(box, [lost, kept]) == "2"  # the caption under the plot is its own
    assert figures.claimed(box, [lost, kept], {"1": "fig-1"}) is None  # and the paper has no figure 2: not handed to figure 1
    assert figures.claimed(box, [lost, kept], {"1": "fig-1", "2": "fig-2"}) == "2"
    assert figures.claimed(box, [kept], {"1": "fig-1"}) == "1"  # with no caption under it, the one above claims it

    # a PDF of the paper prints captions the XML lost, one under panel A (drawn) and one under
    # Figure 2's image: A and the image are counted, B still pinned
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn, caps=["Figure 1 What figure 1 shows.", "Figure 2 What figure 2 shows.", "Scheme 1 A route this PDF does not print."])
    real = figures.captions
    lost = {"1": ("7", Word(70, 400, 260, 410, "Figure 7. Lost from the XML")),  # Figure 1's page: under panel A, its panels side by side above Figure 1
            "2": ("8", Word(60, 400, 400, 410, "Figure 8. Lost from the XML"))}  # Figure 2's page: between its image and its caption

    def with_lost_ones(words):
        caps = real(words)
        return caps + [lost[caps[0][0]]] if len(caps) == 1 and caps[0][0] in lost else caps

    def ocr(img):  # the words on Figure 2's image, which is bars_grouped drawn: its own text layer, scaled to the image
        src = pdfium.PdfDocument(str(CHARTS / "bars_grouped.pdf"))
        sx, sy = img.shape[1] / src[0].get_size()[0], img.shape[0] / src[0].get_size()[1]
        return [Word(x.x0 * sx, x.y0 * sy, x.x1 * sx, x.y1 * sy, x.text) for x in figures.text_words(src[0], (0, 0, *src[0].get_size()), 1.0)]

    monkeypatch.setattr(figures, "captions", with_lost_ones)
    out = figures.read_pages(conn, key, CHARTS / "page_paper.pdf", ocr=ocr)
    assert (out["figures"], out["plots"], out["read"], out["unmatched"]) == (3, 1, 1, 2)  # the keys looked for, the scheme's too
    assert [tuple(r) for r in conn.execute("SELECT figure_label, panel FROM charts")] == [("1", "B")]


def test_an_xml_paper_s_figures_are_read_from_a_pdf_of_it_and_pinned_by_their_captions(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn)
    assert figures.figure_numbers(conn, key) == {str(i): f"{key}#section-1#picture-{i}" for i in (1, 2, 3)}
    out = figures.read_pages(conn, key, CHARTS / "page_paper.pdf")
    assert (out["plots"], out["read"], out["unmatched"]) == (3, 3, 0)
    rows = conn.execute("SELECT figure, figure_label, plot, page, panel, source, y_label FROM charts ORDER BY figure, plot").fetchall()
    assert [tuple(r)[1:] for r in rows] == [("1", 0, 1, "A", "vector", "Young's modulus (MPa)"), ("1", 1, 1, "B", "vector", "UTS (MPa)"), ("2", 0, 2, None, "raster", rows[2]["y_label"])]
    assert rows[0]["figure"] == f"{key}#section-1#picture-1" and rows[2]["figure"] == f"{key}#section-1#picture-2"
    image = figures.of_figure(conn, f"{key}#section-1#picture-2")[0]  # an image in the PDF, read with OCR
    names = [s["name"] for s in image["series"]]  # named, or left unnamed: never misnamed
    assert names[:2] == ["TCP", "Circle 50"] and names[2] in ("Rhombus 50", None) and "change" in image["y"]["label"]
    assert abs(next(v["y"] for v in figures.of_figure(conn, f"{key}#section-1#picture-1")[1]["series"][0]["values"] if v["category"] == "ELAC") - 31.0) < 0.6
    # a rebuild renumbers the XML's figures: the plots follow their number, and the read stands
    conn.execute("UPDATE papers SET figures_file = 'page_paper.pdf' WHERE key = ?", (key,))
    conn.execute("UPDATE nodes SET node_id = replace(node_id, '#picture-', '#fig-'), parent = replace(parent, '#picture-', '#fig-') WHERE paper = ?", (key,))
    conn.commit()
    assert figures.is_read(conn, key)
    assert figures.remap(conn, key)["moved"] == 3 and {r[0] for r in conn.execute("SELECT DISTINCT figure FROM chart_values")} == {f"{key}#section-1#fig-1", f"{key}#section-1#fig-2"}
    assert figures.is_read(conn, key)


def _plots(conn, key, figure, label=None, n=1, page=None, bbox=None, y=0.0):
    """`n` plots of a figure as a read leaves them, one value each (`y`, `y + 1`, …)."""
    figures.ensure_schema(conn)
    for j in range(n):
        conn.execute("INSERT INTO charts(paper, figure, figure_label, plot, page, bbox, status, source, reader, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (key, figure, label, j, page, json.dumps(bbox) if bbox else None, "read", "vector", figures.READER, "t"))
        conn.execute("INSERT INTO chart_values(paper, figure, plot, series, ordinal, y) VALUES (?,?,?,?,?,?)", (key, figure, j, 0, 0, y + j))
    conn.commit()


def test_two_figures_that_trade_numbers_trade_their_plots(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn)
    p1, p2 = f"{key}#section-1#picture-1", f"{key}#section-1#picture-2"
    _plots(conn, key, p1, "1", n=2, y=10)
    _plots(conn, key, p2, "2", y=20)
    # a reading that sets the two the other way round: the XML's figure 1 is the second picture now
    conn.execute("UPDATE nodes SET text = CASE parent WHEN ? THEN 'Figure 2 What figure 2 shows.' ELSE 'Figure 1 What figure 1 shows.' END WHERE parent IN (?, ?)", (p1, p1, p2))
    conn.commit()
    assert figures.remap(conn, key) == {"moved": 3, "removed": 0, "lost": []}  # both had a plot 0: moved through a name of their own, never onto each other
    assert [tuple(r) for r in conn.execute("SELECT figure, figure_label, plot FROM charts ORDER BY figure, plot")] == [(p1, "2", 0), (p2, "1", 0), (p2, "1", 1)]
    assert [tuple(r) for r in conn.execute("SELECT figure, plot, y FROM chart_values ORDER BY figure, plot")] == [(p1, 0, 20.0), (p2, 0, 10.0), (p2, 1, 11.0)]


def test_a_plot_whose_number_this_reading_has_not_is_removed_and_the_paper_read_again(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn)
    p1, p2 = f"{key}#section-1#picture-1", f"{key}#section-1#picture-2"
    _plots(conn, key, p1, "1", n=2)
    _plots(conn, key, p2, "2")
    conn.execute("UPDATE papers SET figures_file = 'paper.figures.pdf' WHERE key = ?", (key,))
    conn.execute("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, at, file) VALUES (?,?,?,?,?,?,?,?)", (key, figures.READER, 3, 3, 3, 3, "t", "paper.figures.pdf"))
    conn.commit()
    assert figures.is_read(conn, key)
    conn.execute("UPDATE nodes SET text = 'Scheme 9 What was figure 2.' WHERE parent = ?", (p2,))  # this reading's caption: figure 2 is a scheme
    conn.commit()
    assert figures.remap(conn, key) == {"moved": 0, "removed": 1, "lost": ["2"]}
    assert [tuple(r) for r in conn.execute("SELECT figure, figure_label FROM charts")] == [(p1, "1"), (p1, "1")]
    assert conn.execute("SELECT COUNT(*) FROM chart_values WHERE figure = ?", (p2,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM figure_reads").fetchone()[0] == 0 and not figures.is_read(conn, key)  # the figures op reads it again
    assert figures.remap(conn, key) == {"moved": 0, "removed": 0, "lost": []}  # twice is once


def _read(conn, key, file):
    """The paper's figures read, by this reader, from `file`."""
    figures.ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO figure_reads(paper, reader, figures, plots, read, values_, at, file) VALUES (?,?,?,?,?,?,?,?)", (key, figures.READER, 2, 2, 2, 2, "t", file))
    conn.commit()


def _pictures_at(conn, key, places):
    """The paper's pictures as a reading draws them, numbered in the order given: `places`,
    [(page, (left, top, right, bottom))]."""
    conn.execute("DELETE FROM nodes WHERE paper = ? AND type = 'picture'", (key,))
    for i, (page, (l, t, r, b)) in enumerate(places, 1):
        conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"{key}#section-1#picture-{i}", key, f"{key}#section-1", i, 2, "picture", "picture", "results", '["3 Results"]', "", page, l, t, r, b))
    conn.commit()
    return [f"{key}#section-1#picture-{i}" for i in range(1, len(places) + 1)]


A, B, C = (50.0, 80.0, 400.0, 330.0), (50.0, 400.0, 400.0, 650.0), (225.0, 80.0, 575.0, 330.0)  # two figures one above the other, and one half across the first


def _nudged(box, pt):
    """A box drawn by another reading: its left edge `pt` further in."""
    return (box[0] + pt, *box[1:])


def test_a_pdf_figure_drawn_a_little_differently_keeps_its_plots_whatever_its_id_now(tmp_path):
    conn, key, _ = _store(tmp_path, ["panels_two", "bars_grouped"])
    p1, p2 = (f"{key}#section-1#picture-{i}" for i in (1, 2))
    for i, f in ((1, p1), (2, p2)):
        r = conn.execute("SELECT bbox_l, bbox_t, bbox_r, bbox_b FROM nodes WHERE node_id = ?", (f,)).fetchone()
        _plots(conn, key, f, page=i, bbox=[round(v, 2) for v in tuple(r)], y=10 * i)
    conn.execute("UPDATE nodes SET bbox_r = bbox_r - 3 WHERE type = 'picture'")  # this reading draws both boxes 3 pt narrower
    conn.execute("UPDATE nodes SET node_id = ? WHERE node_id = ?", (f"{key}#section-1#picture-9", p2))  # and the second under another id
    conn.commit()
    assert figures.remap(conn, key) == {"moved": 1, "removed": 0, "lost": []}  # the first where it was; the second found by its place
    assert [tuple(r) for r in conn.execute("SELECT figure, y FROM chart_values ORDER BY y")] == [(p1, 10.0), (f"{key}#section-1#picture-9", 20.0)]


@pytest.mark.parametrize("reading", [
    [(1, _nudged(B, 1)), (1, _nudged(A, 1))],  # the two the other way round, both a point off
    [(1, B), (1, _nudged(A, 1))],  # the other way round, only one of them off: the one exactly in place is not crowded out
    [(1, C), (1, _nudged(A, 0.5)), (1, _nudged(B, 0.5))],  # a picture read before both: every id after it one on
])
def test_two_figures_on_a_page_whose_ids_another_reading_trades_trade_their_plots(tmp_path, reading):
    """Each plot follows the place its figure had, never the id another picture has now — once,
    a plot kept its id while that was still a picture on its page, whichever."""
    conn, key, _ = _store(tmp_path, ["panels_two"])
    a, b = _pictures_at(conn, key, [(1, A), (1, B)])
    _plots(conn, key, a, page=1, bbox=list(A), y=10)
    _plots(conn, key, b, page=1, bbox=list(B), y=20)
    _read(conn, key, "paper.pdf")
    _pictures_at(conn, key, reading)
    assert figures.remap(conn, key) == {"moved": 2, "removed": 0, "lost": []}
    assert [tuple(r) for r in conn.execute("SELECT v.y, n.bbox_t FROM chart_values v JOIN nodes n ON n.node_id = v.figure ORDER BY v.y")] == [(10.0, A[1]), (20.0, B[1])]
    assert figures.is_read(conn, key)  # moved, every one: nothing to read again


def test_a_node_s_id_alone_never_keeps_a_plot(tmp_path):
    conn, key, _ = _store(tmp_path, ["panels_two"])
    a, b = _pictures_at(conn, key, [(1, A), (2, B)])
    _plots(conn, key, a, page=1, bbox=list(A))
    _plots(conn, key, b, page=2, bbox=list(B), y=5)
    _plots(conn, key, f"{key}#section-1#picture-8", y=9)  # a plot with neither a number nor a place
    _read(conn, key, "paper.pdf")
    _pictures_at(conn, key, [(1, C), (1, B)])  # picture-1 still on page 1, drawn half a figure across; picture-2 on page 1 now, and page 2 holds none
    assert figures.remap(conn, key) == {"moved": 0, "removed": 3, "lost": ["page 1", "page 2", "no page"]}
    assert conn.execute("SELECT COUNT(*) FROM charts").fetchone()[0] == 0 and not figures.is_read(conn, key)


def test_two_pictures_drawn_in_one_place_keep_their_own_plots_until_neither_is_there(tmp_path):
    """A picture read twice over: its place cannot tell the two apart, so each keeps its plots
    while its own id is one of the two there, and none once neither is."""
    conn, key, _ = _store(tmp_path, ["panels_two"])
    a, b = _pictures_at(conn, key, [(1, A), (1, A)])
    _plots(conn, key, a, page=1, bbox=list(A), y=10)
    _plots(conn, key, b, page=1, bbox=list(A), y=20)
    _pictures_at(conn, key, [(1, A), (1, _nudged(A, 1))])
    assert figures.remap(conn, key) == {"moved": 0, "removed": 0, "lost": []}
    _pictures_at(conn, key, [(1, C), (1, B), (1, A), (1, A)])
    assert figures.remap(conn, key) == {"moved": 0, "removed": 2, "lost": ["page 1"]}


def test_a_plot_pinned_to_a_number_this_reading_lacks_is_removed_even_where_a_picture_sits(tmp_path):
    conn, key, _ = _store(tmp_path, ["panels_two"])
    (a,) = _pictures_at(conn, key, [(1, A)])
    _plots(conn, key, a, "7", page=1, bbox=list(A))  # pinned by its caption: to that number or nowhere, never to a place
    assert figures.remap(conn, key) == {"moved": 0, "removed": 1, "lost": ["7"]}


def test_a_removal_is_said_as_a_person_reads_it():
    assert [figures.figure_name(k) for k in ("2", "S2", "Scheme 1", "Chart 2", "page 4", "no page")] == ["Figure 2", "Figure S2", "Scheme 1", "Chart 2", "page 4", "no page"]
    assert figures.removed_note({"moved": 3, "removed": 0, "lost": []}) is None
    assert figures.removed_note({"moved": 0, "removed": 1, "lost": ["Scheme 1"]}) == "1 plot no longer has a figure in this reading (Scheme 1): removed; the figures op reads it again"
    assert figures.removed_note({"moved": 0, "removed": 3, "lost": ["2", "page 4"]}) == "3 plots no longer have a figure in this reading (Figure 2, page 4): removed; the figures op reads them again"


def test_a_figure_claimed_twice_or_holding_plots_of_its_own_takes_none(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn)
    p3 = f"{key}#section-1#picture-3"
    _plots(conn, key, p3, "3")  # figure 3's own plot: it stays
    _plots(conn, key, f"{key}#section-1#picture-7", "3", n=2)  # an earlier reading's figure 3, and figure 3 already holds a plot 0
    _plots(conn, key, f"{key}#gone-a", "1")
    _plots(conn, key, f"{key}#gone-b", "1")  # two figures pinned to "1": which is it is not known
    assert figures.remap(conn, key) == {"moved": 0, "removed": 4, "lost": ["3", "1"]}  # no IntegrityError
    assert [tuple(r) for r in conn.execute("SELECT figure, plot FROM charts")] == [(p3, 0)]
    assert [tuple(r) for r in conn.execute("SELECT figure, plot FROM chart_values")] == [(p3, 0)]


def test_a_pdf_arriving_for_an_xml_paper_is_kept_for_its_figures_and_read(tmp_path, capsys):
    from litrag_parser.worker import Worker

    from test_worker import by, talk

    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Threads"}]), "1")[0]["event"] == "library"
    lib = tmp_path / "threads"
    conn = open_store(lib / "store.sqlite")
    key = _xml_paper(conn)
    (lib / "papers" / "paper.xml").write_text("<article/>")
    assert [w["key"] for w in figures.figures_wanted(conn)] == [key]
    conn.close()
    dropped = lib / "inbox" / "from-the-publisher.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": "threads", "paths": [str(dropped)], "offline": True,
                                "known": {str(dropped): {"doi": "10.3390/mi15070851"}}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    paper = next(e for e in events if e["event"] == "paper")
    assert paper["kept"] and paper["figures_file"].endswith(".figures.pdf") and paper["by"] == "doi"
    conn = open_store(lib / "store.sqlite")
    row = conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    assert (row["file"], row["format"]) == ("paper.xml", "jats")  # the paper is its XML still
    assert conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed'", (key,)).fetchone()[0] == "a PDF of the XML (by DOI), kept for its figures: from-the-publisher.pdf"
    assert (lib / "papers" / row["figures_file"]).exists() and not dropped.exists()
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ? AND status = 'read'", (key,)).fetchone()[0] == 3
    assert figures.figures_wanted(conn) == [] and figures.is_read(conn, key)
    assert any(e["event"] == "log" and "charts read from the PDF beside the XML" in e["message"] for e in events)
    conn.close()


#: Lines page_paper.pdf prints, each long enough to stand for a paper's title, and one that is not.
PRINTED = "Fold change of tenogenic markers on each substrate"
ALSO_PRINTED = "The threads were stronger when aligned"
SHORT = "Young's modulus (MPa)"


def _holding(tmp_path, monkeypatch, held):
    """A new library holding `held` papers — (title, format, pmcid) each, read — with parse_one
    stood in for."""
    from litrag_parser.library import create_library

    _rereading(monkeypatch)
    lib = create_library(tmp_path, f"Twins {len(list(tmp_path.iterdir()))}")
    conn = open_store(lib.store_path)
    for i, (title, fmt, pmcid) in enumerate(held):
        key = file_paper(conn, title=title, file="", sha256=f"held{i}", fmt=fmt, doi=f"10.1/held{i}", pmid=None, pmcid=pmcid, now="t").key
        conn.execute("UPDATE papers SET file = ?, status = 'parsed' WHERE key = ?", (f"held{i}.{'xml' if fmt == 'jats' else 'pdf'}", key))
    conn.commit()
    conn.close()
    return lib


def _drop(lib, capsys, name="scan.pdf", known=None):
    """page_paper.pdf, which prints no DOI and no PMID, dropped into the library's inbox as `name`
    and filed offline, `known` what the caller knows of it: its `paper` event, the library's
    `twin` rows, and the log lines said of it."""
    from litrag_parser.worker import Worker

    dropped = lib.inbox_dir / name
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    capsys.readouterr()
    Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped)], "offline": True,
                                      "known": {str(dropped): known} if known else {}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    conn = open_store(lib.store_path)
    twins = [tuple(r) for r in conn.execute("SELECT paper, detail FROM events WHERE stage = 'twin'")]
    conn.close()
    return next(e for e in events if e["event"] == "paper"), twins, [e for e in events if e["event"] == "log" and e.get("logger") == "twin"]


def _scan_dropped(tmp_path, monkeypatch, capsys, held, name="scan.pdf"):
    """page_paper.pdf dropped into a new library holding `held`: its key, the `twin` rows, and the
    log lines said of it."""
    paper, twins, said = _drop(_holding(tmp_path, monkeypatch, held), capsys, name)
    return paper["paper"], twins, said


def _filed(lib, key):
    conn = open_store(lib.store_path)
    try:
        return [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed' ORDER BY id", (key,))]
    finally:
        conn.close()


def test_a_pdf_naming_nothing_that_prints_an_xml_paper_s_whole_title_is_said_to_be_its_twin(tmp_path, monkeypatch, capsys):
    key, twins, said = _scan_dropped(tmp_path, monkeypatch, capsys, [(PRINTED, "jats", None), ("read from XML", "jats", None)])
    note = "its first pages print the whole title of doi:10.1/held0, read from XML; filed apart, since no DOI or PMID was found for it"
    assert key.startswith("sha:") and twins == [(key, note)]  # filed as today, by its hash: only said
    assert [(e["paper"], e["message"]) for e in said] == [(key, note)]
    # the same PDF dropped where that paper is titled otherwise is said to be nobody's
    assert _scan_dropped(tmp_path, monkeypatch, capsys, [("Fold change of fibroblast markers on each substrate", "jats", None)])[1:] == ([], [])


def test_a_twin_is_said_only_when_one_paper_read_from_xml_is_the_only_one_printed(tmp_path, monkeypatch, capsys):
    def twins(*held, name="scan.pdf"):
        return _scan_dropped(tmp_path, monkeypatch, capsys, list(held), name=name)[1]

    assert twins((PRINTED, "jats", None), (ALSO_PRINTED, "jats", None)) == []  # two XML papers printed: which is not known
    assert twins((SHORT, "jats", None)) == []  # too short to stand for a paper
    assert twins((PRINTED, "pdf", None)) == []  # a paper read from its PDF is not an XML paper's twin
    assert twins((PRINTED, "jats", None), (ALSO_PRINTED, "pdf", None)) == []  # nor is one printed beside another paper's title
    assert twins((PRINTED, "jats", "PMC7654321"), name="PMC1234567.pdf") == []  # a PMCID that is not the paper's
    same = twins((PRINTED, "jats", "PMC1234567"), name="PMC1234567.pdf")
    assert [k for k, _ in same] == ["pmcid:PMC1234567"]  # filed apart under the PMCID it names, which filing does not go by


def test_a_twin_dropped_again_is_met_by_its_hash_and_said_once(tmp_path, monkeypatch, capsys):
    from litrag_parser import worker

    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)])
    first, said_first, _ = _drop(lib, capsys)
    again, said_again, logged = _drop(lib, capsys)
    assert (first["existed"], first["by"]) == (False, None)
    assert (again["paper"], again["existed"], again["by"], again["kept"]) == (first["paper"], True, "sha", False)
    assert len(said_first) == len(said_again) == 1 and logged == []  # said once: dropping it again changes nothing
    # read now, and dropped a third time: kept as it was read, and still said once
    conn = open_store(lib.store_path)
    conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (first["paper"],))
    conn.commit()
    conn.close()
    monkeypatch.setattr(worker, "_has_a_tree", lambda *a: True)
    kept, said_kept, _ = _drop(lib, capsys)
    assert (kept["paper"], kept["kept"], kept["by"]) == (first["paper"], True, "sha") and said_kept == said_first
    assert _filed(lib, first["paper"]) == ["new: scan.pdf", "seen before (by file hash): scan.pdf", "seen before (by file hash), kept: scan.pdf"]


def test_a_pdf_with_a_doi_or_a_pmid_of_its_own_is_no_twin(tmp_path, monkeypatch, capsys):
    # filed by its own identifier, not apart for want of one: the note would not be true of it
    by_doi = _drop(_holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)]), capsys, "a.pdf", known={"doi": "10.9/its-own"})
    assert (by_doi[0]["paper"], by_doi[1:]) == ("doi:10.9/its-own", ([], []))
    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)])
    by_pmid = _drop(lib, capsys, "b.pdf", known={"pmid": "999"})
    assert (by_pmid[0]["paper"], by_pmid[1:]) == ("pmid:999", ([], []))
    # and the same paper again, met by its PMID before its file's hash, says so
    again = _drop(lib, capsys, "c.pdf", known={"pmid": "999"})
    assert (again[0]["paper"], again[0]["existed"], again[0]["by"], again[1:]) == ("pmid:999", True, "pmid", ([], []))
    assert _filed(lib, "pmid:999") == ["new: b.pdf", "seen before (by PMID): c.pdf"]


# -- plots through a rebuild, a reparse and a reread ----------------------------------------------------------


def test_a_rebuild_moves_each_plot_by_its_number_and_removes_the_one_it_has_no_figure_for(tmp_path, capsys):
    """The micromachines paper's saved Docling document rebuilt (the test_studio_ops pattern), its
    plots left by an earlier reading: figures 1 and 2 on each other's ids, and a figure 99 this
    reading has not. The two trade their plots — once an IntegrityError that refused the paper's
    rebuild — and figure 99's go, said in an events row and a log line."""
    from litrag_parser.library import safe_key
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES, by, talk

    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Micromachines"}]), "1")[0]["event"] == "library"
    lib = tmp_path / "micromachines"
    conn = open_store(lib / "store.sqlite")
    key = file_paper(conn, title="x", file="", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.xml", lib / "papers" / "paper.xml")
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib / "parsed" / f"{safe_key(key)}.docling.json")
    conn.execute("UPDATE papers SET file = 'paper.xml', status = 'parsed', figures_file = 'paper.figures.pdf' WHERE key = ?", (key,))
    one, two = f"{key}#section-3#section-8#picture-2", f"{key}#section-3#section-8#picture-4"  # figures 1 and 2, in this reading
    _plots(conn, key, two, "1", y=10)
    _plots(conn, key, one, "2", y=20)
    _plots(conn, key, f"{key}#section-4#section-9#picture-1", "99", n=2)
    conn.execute("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, at, file) VALUES (?,?,?,?,?,?,?,?)", (key, figures.READER, 7, 4, 4, 4, "t", "paper.figures.pdf"))
    conn.commit()
    conn.close()
    Worker(tmp_path).do_rebuild({"id": "r", "op": "rebuild", "lib": "micromachines"})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert (events[-1]["rebuilt"], events[-1]["refused"]) == ([key], [])
    conn = open_store(lib / "store.sqlite")
    assert [tuple(r) for r in conn.execute("SELECT figure, figure_label FROM charts ORDER BY figure_label")] == [(one, "1"), (two, "2")]
    assert [tuple(r) for r in conn.execute("SELECT figure, y FROM chart_values ORDER BY y")] == [(one, 10.0), (two, 20.0)]
    said = "2 plots no longer have a figure in this reading (Figure 99): removed; the figures op reads them again"
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'figures'", (key,))] == [said]
    assert [(e["id"], e["paper"], e["logger"], e["message"]) for e in events if e["event"] == "log"] == [("r", key, "figures", said)]
    assert not figures.is_read(conn, key)
    conn.close()
    Worker(tmp_path).do_rebuild({"id": "s", "op": "rebuild", "lib": "micromachines"})  # again: every plot where it belongs, nothing to say
    conn = open_store(lib / "store.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM events WHERE paper = ? AND stage IN ('figures', 'rebuild-refused')", (key,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM charts").fetchone()[0] == 2
    conn.close()


def test_a_reparse_moves_the_plots_and_removes_those_it_has_no_figure_for_with_the_figures_off(tmp_path, monkeypatch, capsys):
    """parse_one, the layout stood in by the micromachines paper's saved Docling document, over a
    paper an earlier reading had read into three figures, 1, 2 and 9: figure 2's plots follow
    their number into the new nodes and figure 9's go — with the figures off, so before any read."""
    from litrag_parser.library import create_library
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    monkeypatch.setenv("LITRAG_BOUNDARY", "off")
    monkeypatch.delenv("LITRAG_JUDGE", raising=False)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn, caps=["Figure 1 What figure 1 shows.", "Figure 2 What figure 2 shows.", "Figure 9 What figure 9 shows."])
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "paper.xml")
    _plots(conn, key, f"{key}#section-1#picture-2", "2", n=2)
    _plots(conn, key, f"{key}#section-1#picture-3", "9")
    doc = (FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")

    def layout(self, lib, key, path, raw_path, *rest):
        raw_path.write_text(doc, "utf-8")
        return json.loads(doc)

    monkeypatch.setattr(Worker, "layout", layout)
    Worker(tmp_path).parse_one(lib, key, lib.papers_dir / "paper.xml", "p")
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert conn.execute("SELECT status FROM papers WHERE key = ?", (key,)).fetchone()[0] == "parsed"
    assert [tuple(r) for r in conn.execute("SELECT figure, figure_label, plot FROM charts ORDER BY plot")] == [(f"{key}#section-3#section-8#picture-4", "2", 0), (f"{key}#section-3#section-8#picture-4", "2", 1)]
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE figure_label = '9'").fetchone()[0] == 0
    said = "1 plot no longer has a figure in this reading (Figure 9): removed; the figures op reads it again"
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'figures'", (key,))] == [said]
    assert any(e["event"] == "log" and e["logger"] == "figures" and e["message"] == said and e["id"] == "p" for e in events)


def test_a_reparse_that_fails_after_the_nodes_are_replaced_leaves_no_plot_on_a_node_that_is_gone(tmp_path, monkeypatch):
    """The plots follow their figures as soon as the nodes are replaced: a step after that which
    fails fails the paper, never the plots."""
    from litrag_parser import worker
    from litrag_parser.library import create_library
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    monkeypatch.setenv("LITRAG_BOUNDARY", "off")
    monkeypatch.delenv("LITRAG_JUDGE", raising=False)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn, caps=["Figure 1 What figure 1 shows.", "Figure 2 What figure 2 shows.", "Figure 9 What figure 9 shows."])
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "paper.xml")
    _plots(conn, key, f"{key}#section-1#picture-2", "2")
    _plots(conn, key, f"{key}#section-1#picture-3", "9")
    doc = (FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")

    def layout(self, lib, key, path, raw_path, *rest):
        raw_path.write_text(doc, "utf-8")
        return json.loads(doc)

    def fails(*a, **kw):
        raise RuntimeError("a later step fails")

    monkeypatch.setattr(Worker, "layout", layout)
    monkeypatch.setattr(worker, "link_edges", fails)
    Worker(tmp_path).parse_one(lib, key, lib.papers_dir / "paper.xml", "p")
    assert conn.execute("SELECT status FROM papers WHERE key = ?", (key,)).fetchone()[0] == "failed"
    assert [tuple(r) for r in conn.execute("SELECT figure, figure_label FROM charts")] == [(f"{key}#section-3#section-8#picture-4", "2")]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE paper = ? AND stage = 'figures'", (key,)).fetchone()[0] == 1


def _rereading(monkeypatch):
    """parse_one stood in for: which paper would be read, from which file."""
    from litrag_parser.worker import Worker

    read = []
    monkeypatch.setattr(Worker, "parse_one", lambda self, lib, key, path, *rest, **kw: read.append((key, path.name)))
    return read


def test_a_pdf_reread_over_an_xml_paper_no_longer_reads_the_pdf_kept_beside_it(tmp_path, monkeypatch):
    from litrag_parser.library import create_library, safe_key
    from litrag_parser.worker import Worker

    read = _rereading(monkeypatch)
    lib = create_library(tmp_path, "Threads")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn)
    (lib.papers_dir / "paper.xml").write_text("<article/>")
    shutil.copy(CHARTS / "page_paper.pdf", lib.papers_dir / "paper.figures.pdf")
    conn.execute("UPDATE papers SET figures_file = 'paper.figures.pdf' WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    dropped = lib.inbox_dir / "the-paper.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped)], "offline": True, "reread": True,
                                "known": {str(dropped): {"doi": "10.3390/mi15070851"}}})
    conn = open_store(lib.store_path)
    own = f"{safe_key(key)}.pdf"
    assert tuple(conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()) == (own, "pdf", None)
    assert read == [(key, own)]
    filed = [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed' ORDER BY id", (key,))]
    assert filed == ["read from its PDF now: paper.figures.pdf, kept beside its XML for its figures, is no longer read", "seen before (by DOI): the-paper.pdf"]
    assert (lib.papers_dir / "paper.figures.pdf").exists()  # not read, not thrown away
    conn.close()


def test_an_xml_reread_over_a_pdf_paper_keeps_its_pdf_beside_it_for_its_figures(tmp_path, monkeypatch):
    from litrag_parser.library import create_library, safe_key
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    read = _rereading(monkeypatch)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="", sha256="p1", fmt="pdf", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    own = f"{safe_key(key)}.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", lib.papers_dir / own)
    conn.execute("UPDATE papers SET file = ?, status = 'parsed' WHERE key = ?", (own, key))
    figures.ensure_schema(conn)
    conn.execute("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, at, file) VALUES (?,?,?,?,?,?,?,?)", (key, figures.READER, 2, 0, 0, 0, "t", own))
    conn.commit()
    assert figures.is_read(conn, key)
    conn.close()
    dropped = lib.inbox_dir / "PMC11278924.xml"
    shutil.copy(FIXTURES / "PMC11278924.xml", dropped)
    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped)], "offline": True, "reread": True})
    conn = open_store(lib.store_path)
    assert tuple(conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()) == (f"{safe_key(key)}.xml", "jats", own)
    assert read == [(key, f"{safe_key(key)}.xml")]
    filed = [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed' ORDER BY id", (key,))]
    assert filed == [f"read from its XML now: its PDF {own} is kept beside it for its figures", "seen before (by DOI): PMC11278924.xml"]
    # the same file draws its figures, but read as the paper's own pictures: the XML's are read from its pages, again
    assert not figures.is_read(conn, key)
    conn.close()


def _refile(tmp_path, lib, path, reread=True, doi=None):
    """A file dropped for a paper the library holds, offline, read again when `reread`."""
    from litrag_parser.worker import Worker

    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(path)], "offline": True, "reread": reread, **({"known": {str(path): {"doi": doi}}} if doi else {})})


def _pdf_paper(lib, status="parsed", on_disk=True):
    """A paper read from its own PDF, `<safe>.pdf`: its key and that file's name."""
    from litrag_parser.library import safe_key

    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="", sha256="p1", fmt="pdf", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    own = f"{safe_key(key)}.pdf"
    if on_disk:
        shutil.copy(CHARTS / "page_paper.pdf", lib.papers_dir / own)
    conn.execute("UPDATE papers SET file = ?, status = ? WHERE key = ?", (own, status, key))
    conn.commit()
    conn.close()
    return key, own


def test_a_reread_in_the_same_format_leaves_what_draws_the_figures_alone(tmp_path, monkeypatch):
    from litrag_parser.library import create_library

    from test_worker import FIXTURES

    read = _rereading(monkeypatch)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn)
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "paper.xml")
    shutil.copy(CHARTS / "page_paper.pdf", lib.papers_dir / "paper.figures.pdf")
    conn.execute("UPDATE papers SET figures_file = 'paper.figures.pdf' WHERE key = ?", (key,))
    _read(conn, key, "paper.figures.pdf")
    conn.close()
    dropped = lib.inbox_dir / "PMC11278924.xml"
    shutil.copy(FIXTURES / "PMC11278924.xml", dropped)
    _refile(tmp_path, lib, dropped)
    conn = open_store(lib.store_path)
    assert len(read) == 1 and conn.execute("SELECT figures_file FROM papers WHERE key = ?", (key,)).fetchone()[0] == "paper.figures.pdf"
    assert conn.execute("SELECT COUNT(*) FROM events WHERE paper = ? AND detail LIKE 'read from its%'", (key,)).fetchone()[0] == 0
    assert figures.is_read(conn, key)
    conn.close()


def test_an_xml_and_then_its_pdf_again_say_nothing_false_about_the_file_read(tmp_path, monkeypatch):
    """There and back: the PDF kept beside the XML is the paper's own, and read again as the paper
    is never said to be no longer read."""
    from litrag_parser.library import create_library

    from test_worker import FIXTURES

    _rereading(monkeypatch)
    lib = create_library(tmp_path, "Micromachines")
    key, own = _pdf_paper(lib)
    dropped = lib.inbox_dir / "PMC11278924.xml"
    shutil.copy(FIXTURES / "PMC11278924.xml", dropped)
    _refile(tmp_path, lib, dropped)
    back = lib.inbox_dir / "again.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", back)
    _refile(tmp_path, lib, back, doi="10.3390/mi15070851")
    conn = open_store(lib.store_path)
    assert tuple(conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()) == (own, "pdf", None)
    said = [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND detail LIKE 'read from its%' ORDER BY id", (key,))]
    assert said == [f"read from its XML now: its PDF {own} is kept beside it for its figures", "read from its PDF now"]
    conn.close()


def test_an_xml_reread_over_a_pdf_no_longer_on_disk_keeps_nothing_beside_it(tmp_path, monkeypatch):
    from litrag_parser.library import create_library

    from test_worker import FIXTURES

    _rereading(monkeypatch)
    lib = create_library(tmp_path, "Micromachines")
    key, _ = _pdf_paper(lib, on_disk=False)
    dropped = lib.inbox_dir / "PMC11278924.xml"
    shutil.copy(FIXTURES / "PMC11278924.xml", dropped)
    _refile(tmp_path, lib, dropped)
    conn = open_store(lib.store_path)
    assert tuple(conn.execute("SELECT format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()) == ("jats", None)
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND detail LIKE 'read from its%'", (key,))] == ["read from its XML now"]
    conn.close()


def test_an_xml_for_a_pdf_paper_that_was_never_read_keeps_the_pdf_beside_it_unasked(tmp_path, monkeypatch):
    """The format changes where the file is filed, so an XML arriving for a paper whose PDF failed
    keeps that PDF for its figures without a reread."""
    from litrag_parser.library import create_library

    from test_worker import FIXTURES

    read = _rereading(monkeypatch)
    lib = create_library(tmp_path, "Micromachines")
    key, own = _pdf_paper(lib, status="failed")
    dropped = lib.inbox_dir / "PMC11278924.xml"
    shutil.copy(FIXTURES / "PMC11278924.xml", dropped)
    _refile(tmp_path, lib, dropped, reread=False)
    conn = open_store(lib.store_path)
    assert len(read) == 1 and tuple(conn.execute("SELECT format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()) == ("jats", own)
    conn.close()


def test_the_judge_s_own_command_moves_the_plots_with_their_figures_too(tmp_path, monkeypatch, capsys):
    """`python -m litrag_parser.judge` replaces a paper's nodes as a rebuild does, and its plots
    follow their figures or go, said the same way. The model is stood in for: never asked."""
    from litrag_parser import judge
    from litrag_parser.library import create_library, safe_key

    from test_worker import FIXTURES

    monkeypatch.setattr(judge, "available", lambda *a, **kw: True)
    monkeypatch.setattr(judge, "ask_ollama", lambda *a, **kw: None)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "paper.xml")
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib.parsed_dir / f"{safe_key(key)}.docling.json")
    conn.execute("UPDATE papers SET file = 'paper.xml', status = 'parsed' WHERE key = ?", (key,))
    one = f"{key}#section-3#section-8#picture-2"  # figure 1, in this reading
    _plots(conn, key, f"{key}#an-earlier-reading#picture-1", "1", y=10)
    _plots(conn, key, f"{key}#an-earlier-reading#picture-9", "99")
    conn.close()
    judge.main(["--lib", str(lib.dir), "--key", key])
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT figure, y FROM chart_values")] == [(one, 10.0)]
    said = "1 plot no longer has a figure in this reading (Figure 99): removed; the figures op reads it again"
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'figures'", (key,))] == [said]
    assert said in capsys.readouterr().out
    conn.close()
