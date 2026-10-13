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


def _xml_nodes(conn, key, title="read from XML", caps=None, figures_n=3):
    """A paper as a reading of its XML leaves it: its figures numbered by their captions (`caps`,
    one a picture), no page, no box, and its title."""
    caps = caps or [f"Figure {i} What figure {i} shows." for i in range(1, figures_n + 1)]
    rows = [(f"{key}#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "")]
    for i, cap in enumerate(caps, 1):
        rows.append((f"{key}#section-1#picture-{i}", key, f"{key}#section-1", i, 2, "picture", "picture", "results", None, '["3 Results"]', ""))
        rows.append((f"{key}#section-1#picture-{i}#caption-1", key, f"{key}#section-1#picture-{i}", 0, 3, "caption", "caption", "results", None, '["3 Results"]', cap))
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed', title = ? WHERE key = ?", (title, key))
    conn.commit()


def _xml_paper(conn, figures_n=3, doi="10.3390/mi15070851", caps=None, title="read from XML", pmcid=None):
    """A paper read from XML, `paper.xml`: its figures numbered by their captions (`caps`, one a
    picture), no page, no box."""
    key = file_paper(conn, title=title, file="paper.xml", sha256=f"x:{doi}", fmt="jats", doi=doi, pmid=None, pmcid=pmcid, now="t").key
    conn.execute("UPDATE papers SET file = 'paper.xml' WHERE key = ?", (key,))
    _xml_nodes(conn, key, title, caps, figures_n)
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


def test_a_pdf_arriving_for_an_xml_paper_is_kept_for_its_figures_and_read(tmp_path, capsys, _never_asked):
    from litrag_parser.worker import Worker

    from test_worker import by, talk

    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Threads"}]), "1")[0]["event"] == "library"
    lib = tmp_path / "threads"
    conn = open_store(lib / "store.sqlite")
    key = _xml_paper(conn, title=PRINTED)  # a line page_paper.pdf prints, which prints no DOI: its first pages name the paper by its title
    (lib / "papers" / "paper.xml").write_text("<article/>")
    assert [w["key"] for w in figures.figures_wanted(conn)] == [key]
    conn.close()
    dropped = lib / "inbox" / "from-the-publisher.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": "threads", "paths": [str(dropped)], "offline": True,
                                "known": {str(dropped): {"doi": "10.3390/mi15070851"}}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    paper = next(e for e in events if e["event"] == "paper")
    assert paper["kept"] and paper["figures_file"] == "doi_10.3390_mi15070851.figures.pdf" and paper["by"] == "doi"
    assert (paper["aside"], paper["same"], paper["file"], paper["status"]) == (None, False, "paper.xml", "parsed")
    assert (paper["paper"], paper["existed"], paper["doi"], paper["pmcid"]) == (key, True, "10.3390/mi15070851", None)  # the paper's row, not the PDF's
    conn = open_store(lib / "store.sqlite")
    row = conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    assert (row["file"], row["format"]) == ("paper.xml", "jats")  # the paper is its XML still
    assert conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed'", (key,)).fetchone()[0] == \
        "a PDF of the XML, kept for its figures: from-the-publisher.pdf (its pages print the captions of 2 of its 3 figures)"
    assert (lib / "papers" / row["figures_file"]).exists() and not dropped.exists()
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ? AND status = 'read'", (key,)).fetchone()[0] == 3
    assert figures.figures_wanted(conn, lib / "papers") == [] and figures.is_read(conn, key)
    assert any(e["event"] == "log" and "charts read from the PDF beside the XML" in e["message"] for e in events)
    assert (events[-1]["event"], events[-1]["figures"]) == ("done", [key])  # the window draws the figure shown again
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


def _figured(lib, key="doi:10.1/held0", title=PRINTED):
    """A figure in the paper's reading, as one read from XML has: a picture the window draws, which
    a PDF can be dropped on."""
    conn = open_store(lib.store_path)
    _xml_nodes(conn, key, title, figures_n=1)
    conn.close()


def test_a_pdf_naming_nothing_that_prints_an_xml_paper_s_whole_title_is_said_to_be_its_twin(tmp_path, monkeypatch, capsys):
    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None), ("read from XML", "jats", None)])
    _figured(lib)
    paper, twins, said = _drop(lib, capsys)
    key = paper["paper"]
    note = "its first pages print the whole title of doi:10.1/held0, read from XML; filed apart, since no DOI or PMID was found for it"
    act = "; if it is that paper, drop it on one of that paper's figures"
    assert key.startswith("sha:") and twins == [(key, note + act)]  # filed as today, by its hash: only said, and what to do
    assert [(e["paper"], e["message"]) for e in said] == [(key, note + act)]
    # a paper whose reading holds no figure gives the window none to drop it on: only said
    key, twins, said = _scan_dropped(tmp_path, monkeypatch, capsys, [(PRINTED, "jats", None)])
    assert twins == [(key, note)] and [e["message"] for e in said] == [note]
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


# -- a PDF kept beside an XML paper only when it names the paper ------------------------------------------------


THREADS = "doi:10.3390/mi15070851"
KEPT = "doi_10.3390_mi15070851.figures.pdf"


@pytest.fixture
def _never_asked(monkeypatch):
    """Offline, and the title lookup could not answer if it were asked."""
    from litrag_parser import worker

    def ask(*a, **kw):
        raise AssertionError("Europe PMC asked for a title")

    monkeypatch.setattr(worker, "lookup_by_title", ask)


def _threads(tmp_path, title=PRINTED, **kw):
    """A new library holding one paper read from XML, its XML on disk: the library."""
    from litrag_parser.library import create_library

    lib = create_library(tmp_path, f"Threads {len(list(tmp_path.iterdir()))}")
    conn = open_store(lib.store_path)
    assert _xml_paper(conn, title=title, **kw) == THREADS
    conn.close()
    (lib.papers_dir / "paper.xml").write_text("<article/>")
    return lib


def _attach(lib, capsys, pdf=CHARTS / "page_paper.pdf", name="from-the-publisher.pdf", known=None, **req):
    """`pdf` dropped into the library's inbox as `name` and filed offline, for the paper by its DOI
    unless `known` says otherwise, `req` anything else the request says: the events, and the
    dropped file."""
    from litrag_parser.worker import Worker

    dropped = lib.inbox_dir / name
    shutil.copy(pdf, dropped)
    capsys.readouterr()
    Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped)], "offline": True,
                                      "known": {str(dropped): known or {"doi": "10.3390/mi15070851"}}, **req})
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()], dropped


def _one(events, kind):
    (e,) = [e for e in events if e["event"] == kind]
    return e


def _rows(lib, stage, key=THREADS):
    conn = open_store(lib.store_path)
    try:
        return [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = ? ORDER BY id", (key, stage))]
    finally:
        conn.close()


def test_a_pdf_whose_first_pages_name_neither_the_paper_s_doi_nor_its_title_is_refused_and_changes_nothing(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser.store import sha256_of
    from litrag_parser.worker import Worker

    _rereading(monkeypatch)
    lib = _threads(tmp_path, title="Fold change of fibroblast markers on each substrate")  # a title the page does not print
    conn = open_store(lib.store_path)
    stub = file_paper(conn, title="its bytes", file="", sha256=sha256_of(CHARTS / "page_paper.pdf"), fmt="pdf", doi=None, pmid=None, pmcid=None, now="t").key  # filed once under its hash: a stub filing drops
    conn.commit()
    cols = "doi, pmid, pmcid, file, sha256, format, figures_file"
    before = tuple(conn.execute(f"SELECT {cols} FROM papers WHERE key = ?", (THREADS,)).fetchone())
    conn.close()
    # another paper filed after it in the same request: whatever was written for the refused PDF would be committed with it
    dropped, other = lib.inbox_dir / "from-the-publisher.pdf", lib.inbox_dir / "another-paper.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    shutil.copy(CHARTS / "bars_simple.pdf", other)
    capsys.readouterr()
    Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped), str(other)], "offline": True,
                                      "known": {str(dropped): {"doi": "10.3390/mi15070851", "pmid": "39064362", "pmcid": "PMC11278924"}}})  # identifiers filing would fill the XML paper's gaps with
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    reason = "its first pages name neither its DOI nor its title"
    assert _one(events, "skipped") == {"event": "skipped", "id": "i", "path": str(dropped), "paper": THREADS, "reason": reason}
    assert not [e for e in events if e["event"] == "paper" and e["paper"] == THREADS] and _one(events, "done")["figures"] == []
    assert dropped.exists()  # left where it is
    assert _rows(lib, "figures-refused") == [f"not kept for its figures: from-the-publisher.pdf: {reason}"] and _rows(lib, "figures-pdf") == []
    conn = open_store(lib.store_path)
    assert [r[0] for r in conn.execute("SELECT stage FROM events WHERE paper = ?", (THREADS,))] == ["figures-refused"]  # the one row, the caller's
    assert tuple(conn.execute(f"SELECT {cols} FROM papers WHERE key = ?", (THREADS,)).fetchone()) == before  # figures_file NULL, no gap filled
    filed = f"sha:{sha256_of(CHARTS / 'bars_simple.pdf')[:16]}"
    assert sorted(r[0] for r in conn.execute("SELECT key FROM papers")) == sorted([THREADS, stub, filed])  # the stub not dropped; only the other file filed
    assert [w["key"] for w in figures.figures_wanted(conn, lib.papers_dir)] == [THREADS]
    conn.close()


def test_the_same_pdf_again_is_kept_as_it_is_and_not_read_again(tmp_path, capsys, _never_asked):
    lib = _threads(tmp_path)
    _attach(lib, capsys)
    conn = open_store(lib.store_path)
    conn.execute("UPDATE figure_reads SET at = 'the first read' WHERE paper = ?", (THREADS,))
    conn.commit()
    conn.close()
    events, dropped = _attach(lib, capsys, name="again.pdf")
    paper = _one(events, "paper")
    assert (paper["kept"], paper["same"], paper["aside"], paper["figures_file"]) == (True, True, None, KEPT)
    assert not dropped.exists() and _one(events, "done")["figures"] == []  # the inbox copy goes; nothing is read
    conn = open_store(lib.store_path)
    assert conn.execute("SELECT at FROM figure_reads WHERE paper = ?", (THREADS,)).fetchone()[0] == "the first read"
    conn.close()
    assert len(_rows(lib, "filed")) == 1 and not (lib.papers_dir / "doi_10.3390_mi15070851.figures.1.pdf").exists()


def test_a_second_pdf_replaces_the_kept_one_only_when_it_prints_more_of_the_paper_s_captions(tmp_path, monkeypatch, capsys, _never_asked):
    monkeypatch.setenv("LITRAG_FIGURES", "off")  # kept or not is the question here, not what is read
    lib = _threads(tmp_path)
    _attach(lib, capsys)  # page_paper.pdf: the captions of figures 1 and 2 of 3
    longer = tmp_path / "longer.pdf"
    out = pdfium.PdfDocument.new()
    for n in ("page_paper", "bars_simple"):
        out.import_pages(pdfium.PdfDocument(str(CHARTS / f"{n}.pdf")))
    out.save(str(longer))
    events, dropped = _attach(lib, capsys, pdf=longer, name="longer.pdf")
    assert _one(events, "skipped")["reason"] == f"prints the captions of 2 of its 3 figures; the PDF kept, {KEPT}, prints 2"
    assert dropped.exists() and (lib.papers_dir / KEPT).read_bytes() == (CHARTS / "page_paper.pdf").read_bytes()
    real = figures.caption_numbers
    monkeypatch.setattr(figures, "caption_numbers", lambda pdf: {"1", "2", "3"} if pdf.name == "longer.pdf" else real(pdf))
    events, dropped = _attach(lib, capsys, pdf=longer, name="longer.pdf")
    aside = "doi_10.3390_mi15070851.figures.1.pdf"
    paper = _one(events, "paper")
    assert (paper["kept"], paper["same"], paper["aside"], paper["figures_file"]) == (True, False, aside, KEPT) and not dropped.exists()
    assert (lib.papers_dir / KEPT).read_bytes() == longer.read_bytes()
    assert (lib.papers_dir / aside).read_bytes() == (CHARTS / "page_paper.pdf").read_bytes()  # set aside, never deleted
    assert _rows(lib, "filed")[-1] == f"a PDF of the XML, kept for its figures: longer.pdf (its pages print the captions of 3 of its 3 figures); {KEPT} set aside as {aside}"
    assert _rows(lib, "figures-refused") == [f"not kept for its figures: longer.pdf: prints the captions of 2 of its 3 figures; the PDF kept, {KEPT}, prints 2"]


def test_a_kept_pdf_gone_from_disk_counts_as_none(tmp_path, monkeypatch, capsys, _never_asked):
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _threads(tmp_path)
    _attach(lib, capsys)
    (lib.papers_dir / KEPT).unlink()
    events, _ = _attach(lib, capsys)
    paper = _one(events, "paper")
    assert (paper["kept"], paper["same"], paper["aside"]) == (True, False, None)
    assert (lib.papers_dir / KEPT).exists() and sorted(p.name for p in lib.papers_dir.glob("*.figures*")) == [KEPT]


def test_a_pdf_fetched_beside_its_xml_is_that_paper_s_whatever_it_is_named(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser import worker
    from litrag_parser.library import create_library
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    def unasked(*a, **kw):
        raise AssertionError("a PDF beside its XML is not asked what it is")

    def parse_one(self, lib, key, path, *rest, **kw):  # the XML read: its figures, and a title page_paper.pdf prints
        conn = open_store(lib.store_path)
        _xml_nodes(conn, key, PRINTED)
        conn.close()

    monkeypatch.setattr(worker, "sniff_ids", unasked)
    monkeypatch.setattr(Worker, "parse_one", parse_one)
    lib = create_library(tmp_path, "Threads")
    xml, pdf = lib.inbox_dir / "PMC11278924.xml", lib.inbox_dir / "10.9999_other.pdf"  # a name that is another DOI
    shutil.copy(FIXTURES / "PMC11278924.xml", xml)
    shutil.copy(CHARTS / "page_paper.pdf", pdf)
    capsys.readouterr()
    keys = Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(pdf), str(xml)], "offline": True,
                                       "known": {str(pdf): {"beside": str(xml)}}})  # the PDF first
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert keys == {str(xml): THREADS, str(pdf): THREADS}
    kept = [e for e in events if e["event"] == "paper" and e.get("figures_file")]
    assert [(e["paper"], e["by"], e["figures_file"]) for e in kept] == [(THREADS, None, KEPT)]
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT key, format, figures_file FROM papers")] == [(THREADS, "jats", KEPT)]
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ? AND status = 'read'", (THREADS,)).fetchone()[0] == 3
    conn.close()
    assert _one(events, "done")["figures"] == [THREADS] and not pdf.exists()


def test_the_captions_a_pdf_prints_are_read_from_its_text_layer():
    assert figures.caption_numbers(CHARTS / "page_paper.pdf") == {"1", "2"}
    assert figures.caption_numbers(CHARTS / "bars_simple.pdf") == set()
    assert figures.caption_numbers(CHARTS / "make.py") == set()  # no PDF: none


def test_a_paper_whose_kept_pdf_is_gone_is_wanted_again(tmp_path):
    from litrag_parser.library import create_library

    from test_worker import by, talk

    lib = create_library(tmp_path, "Threads")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn)
    conn.execute("UPDATE papers SET figures_file = 'paper.figures.pdf' WHERE key = ?", (key,))
    conn.commit()
    assert figures.figures_wanted(conn) == []  # by the row alone
    assert figures.figures_wanted(conn, lib.papers_dir) == [{"key": key, "title": "read from XML", "doi": "10.3390/mi15070851", "pmid": None, "pmcid": None, "figures": 3}]  # by the disk: the file is not there
    assert [w["key"] for w in by(talk(tmp_path, [{"id": "w", "op": "wanted", "lib": lib.id}]), "w")[0]["figures"]] == [key]
    shutil.copy(CHARTS / "page_paper.pdf", lib.papers_dir / "paper.figures.pdf")
    assert figures.figures_wanted(conn, lib.papers_dir) == []
    conn.close()


def test_with_the_figures_off_a_pdf_is_kept_and_not_read_and_said_so(tmp_path, monkeypatch, capsys, _never_asked):
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _threads(tmp_path)
    events, dropped = _attach(lib, capsys)
    assert _one(events, "paper")["figures_file"] == KEPT and not dropped.exists() and _one(events, "done")["figures"] == []
    said = f"{KEPT} kept for its figures; not read: LITRAG_FIGURES=off"
    assert _rows(lib, "figures") == [said]
    assert [(e["paper"], e["message"]) for e in events if e["event"] == "log"] == [(THREADS, said)]
    conn = open_store(lib.store_path)
    figures.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM charts").fetchone()[0] == 0 and not figures.is_read(conn, THREADS)
    conn.close()
    events, _ = _attach(lib, capsys, name="again.pdf")  # the same PDF again: kept already, and nothing new to say of the switch
    assert _one(events, "paper")["same"] and _rows(lib, "figures") == [said] and not [e for e in events if e["event"] == "log"]


def test_keeping_a_pdf_beside_an_xml_paper_checks_the_paper_and_loses_no_file(tmp_path):
    """keep_beside alone: a paper not read from XML, or none, keeps nothing; a PDF printing none of
    the paper's captions is kept (it is the paper's, and the next caption fix reads it); a file of
    the kept name the paper no longer keeps is set aside, not overwritten, under the first number
    free; and the count is of figures, not schemes."""
    conn = open_store(tmp_path / "store.sqlite")
    pdf_paper = file_paper(conn, title=PRINTED, file="a.pdf", sha256="a", fmt="pdf", doi="10.1/pdf", pmid=None, pmcid=None, now="t").key
    page = CHARTS / "page_paper.pdf"
    assert figures.keep_beside(conn, tmp_path, pdf_paper, page, "t")["why"] == "not a paper read from XML"
    assert figures.keep_beside(conn, tmp_path, "doi:none", page, "t")["why"] == "no such paper"
    key = _xml_paper(conn, title=PRINTED, caps=["Figure 7 Not printed.", "Figure 8 Nor this.", "Scheme 1 A scheme, which is no figure to count."])
    stray = tmp_path / KEPT
    stray.write_bytes(b"%PDF-1.4 kept once, before a reread")
    (tmp_path / "doi_10.3390_mi15070851.figures.1.pdf").write_bytes(b"%PDF-1.4 set aside before")
    out = figures.keep_beside(conn, tmp_path, key, page, "t")
    assert (out["kept"], out["captions"], out["figures"], out["file"], out["aside"]) == (True, 0, 2, KEPT, "doi_10.3390_mi15070851.figures.2.pdf")  # the first free number
    assert (tmp_path / out["aside"]).read_bytes() == b"%PDF-1.4 kept once, before a reread" and (tmp_path / KEPT).read_bytes() == page.read_bytes()
    assert (tmp_path / "doi_10.3390_mi15070851.figures.1.pdf").read_bytes() == b"%PDF-1.4 set aside before"
    assert conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed'", (key,)).fetchone()[0] == \
        f"a PDF of the XML, kept for its figures: page_paper.pdf (its pages print the captions of 0 of its 2 figures); {KEPT} set aside as {out['aside']}"
    none = _xml_paper(conn, doi="10.1/none", title=PRINTED, caps=["A figure its caption gives no number."])
    assert figures.keep_beside(conn, tmp_path, none, page, "t")["kept"]
    assert conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed'", (none,)).fetchone()[0] == "a PDF of the XML, kept for its figures: page_paper.pdf (its XML numbers no figures)"
    one = _xml_paper(conn, doi="10.1/one", title=PRINTED, caps=["Figure 1 The one figure."])
    own = _xml_paper(conn, doi="10.1/own", title=PRINTED)
    figures.ensure_schema(conn)
    conn.executemany("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, at, file) VALUES (?, ?, 1, 0, 0, 0, 't', ?)",
                     [(one, figures.READER, None), (own, figures.READER, "doi_10.1_own.pdf")])  # a merge's copy, naming no file; a read of the file kept before
    assert figures.keep_beside(conn, tmp_path, one, page, "t")["kept"]
    assert conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'filed'", (one,)).fetchone()[0] == "a PDF of the XML, kept for its figures: page_paper.pdf (its pages print the captions of 1 of its 1 figure)"
    # a PDF kept under another name — the paper's own, kept beside it when its XML was read over it (worker._format_changed) — is set aside the same way
    shutil.copy(CHARTS / "bars_simple.pdf", tmp_path / "doi_10.1_own.pdf")  # prints none of its captions
    conn.execute("UPDATE papers SET figures_file = 'doi_10.1_own.pdf' WHERE key = ?", (own,))
    conn.commit()
    out = figures.keep_beside(conn, tmp_path, own, page, "t")
    assert (out["kept"], out["file"], out["aside"]) == (True, "doi_10.1_own.figures.pdf", "doi_10.1_own.figures.1.pdf")
    assert not (tmp_path / "doi_10.1_own.pdf").exists() and (tmp_path / out["aside"]).read_bytes() == (CHARTS / "bars_simple.pdf").read_bytes()
    assert [r[0] for r in conn.execute("SELECT paper FROM figure_reads")] == [one]  # the read of a file no longer kept goes; one naming none is its caller's
    assert not list(tmp_path.glob("*.part"))


def _longer(tmp_path, monkeypatch, first="page_paper"):
    """page_paper.pdf and bars_simple.pdf in one PDF, `first` first, taken to print the captions of
    figures 1, 2 and 3: more than page_paper.pdf alone, so it replaces it."""
    longer = tmp_path / "longer.pdf"
    out = pdfium.PdfDocument.new()
    for n in (first, ({"page_paper", "bars_simple"} - {first}).pop()):
        out.import_pages(pdfium.PdfDocument(str(CHARTS / f"{n}.pdf")))
    out.save(str(longer))
    real = figures.caption_numbers
    monkeypatch.setattr(figures, "caption_numbers", lambda pdf: {"1", "2", "3"} if pdf.name == "longer.pdf" else real(pdf))
    return longer


def _figures_op(lib, capsys):
    from litrag_parser.worker import Worker

    capsys.readouterr()
    Worker(lib.dir.parent).do_figures({"id": "g", "op": "figures", "lib": lib.id})
    return _one([json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()], "done")


def _pages_read(lib, key=THREADS):
    conn = open_store(lib.store_path)
    try:
        return figures.is_read(conn, key), sorted({r[0] for r in conn.execute("SELECT page FROM charts WHERE paper = ?", (key,))})
    finally:
        conn.close()


def test_a_pdf_kept_in_place_of_one_read_is_read_again_though_it_has_the_same_name(tmp_path, monkeypatch, capsys, _never_asked):
    lib = _threads(tmp_path)
    _attach(lib, capsys)  # the figures on: page_paper.pdf kept, and read
    assert _pages_read(lib) == (True, [1, 2])
    monkeypatch.setenv("LITRAG_FIGURES", "off")  # kept, not read (K6)
    events, _ = _attach(lib, capsys, pdf=_longer(tmp_path, monkeypatch, first="bars_simple"), name="longer.pdf")
    assert _one(events, "paper")["aside"] == "doi_10.3390_mi15070851.figures.1.pdf"
    assert not _pages_read(lib)[0]  # the same name, other bytes: what was read is not what is kept now
    monkeypatch.delenv("LITRAG_FIGURES")
    done = _figures_op(lib, capsys)
    assert (done["papers"], done["read"]) == (1, 3) and _pages_read(lib) == (True, [2, 3])  # page_paper's pages, where the kept PDF has them


def test_a_kept_pdf_whose_read_fails_is_left_to_the_figures_op(tmp_path, monkeypatch, capsys, _never_asked):
    lib = _threads(tmp_path)
    _attach(lib, capsys)
    longer = _longer(tmp_path, monkeypatch, first="bars_simple")
    read_for = figures.read_for

    def fails(*a, **kw):
        raise RuntimeError("pdfium gave up")

    monkeypatch.setattr(figures, "read_for", fails)
    events, _ = _attach(lib, capsys, pdf=longer, name="longer.pdf")
    assert _one(events, "paper")["kept"] and _one(events, "done")["figures"] == []  # kept; its figures not read
    assert [e["message"] for e in events if e["event"] == "log"] == ["RuntimeError: pdfium gave up"]
    assert not _pages_read(lib)[0]
    monkeypatch.setattr(figures, "read_for", read_for)
    assert _figures_op(lib, capsys)["papers"] == 1 and _pages_read(lib) == (True, [2, 3])


def test_two_pdfs_for_one_paper_in_one_request_are_kept_in_turn_and_read_once(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser.worker import Worker

    read = []
    monkeypatch.setattr(Worker, "figures_of", lambda self, lib, key, req_id: read.append(key) or {"read": 0})
    lib = _threads(tmp_path)
    first, second = lib.inbox_dir / "a.pdf", lib.inbox_dir / "longer.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", first)
    shutil.copy(_longer(tmp_path, monkeypatch), second)
    capsys.readouterr()
    Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(first), str(second)], "offline": True,
                                      "known": {str(first): {"doi": "10.3390/mi15070851"}, str(second): {"doi": "10.3390/mi15070851"}}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert [(e["aside"], e["same"]) for e in events if e["event"] == "paper"] == [(None, False), ("doi_10.3390_mi15070851.figures.1.pdf", False)]
    assert read == [THREADS] and _one(events, "done")["figures"] == [THREADS]  # the last kept, read once


def test_a_pdf_named_twice_in_one_request_is_kept_once_and_then_not_there(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _threads(tmp_path)
    dropped = lib.inbox_dir / "x.pdf"
    shutil.copy(CHARTS / "page_paper.pdf", dropped)
    capsys.readouterr()
    keys = Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(dropped), str(dropped)], "offline": True,
                                             "known": {str(dropped): {"doi": "10.3390/mi15070851"}}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert keys == {str(dropped): THREADS} and _one(events, "paper")["figures_file"] == KEPT
    assert _one(events, "skipped") == {"event": "skipped", "id": "i", "path": str(dropped), "paper": THREADS, "reason": "not there any more"}
    assert events[-1]["event"] == "done" and _rows(lib, "figures-refused") == []  # no PDF came the second time, so none was refused


def test_a_pdf_kept_beside_its_xml_paper_takes_the_place_of_a_stub_its_bytes_were_filed_as(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser.store import sha256_of

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _threads(tmp_path)
    conn = open_store(lib.store_path)
    stub = file_paper(conn, title="its bytes", file="", sha256=sha256_of(CHARTS / "page_paper.pdf"), fmt="pdf", doi=None, pmid=None, pmcid=None, now="t").key
    conn.commit()
    cols = "doi, pmid, pmcid, file, sha256, format"
    before = tuple(conn.execute(f"SELECT {cols} FROM papers WHERE key = ?", (THREADS,)).fetchone())
    conn.close()
    events, _ = _attach(lib, capsys, known={"doi": "10.3390/mi15070851", "pmid": "39064362"})
    assert _one(events, "paper")["figures_file"] == KEPT
    conn = open_store(lib.store_path)
    assert [r[0] for r in conn.execute("SELECT key FROM papers")] == [THREADS]  # filed once: the stub went, as filing drops one
    assert tuple(conn.execute(f"SELECT {cols} FROM papers WHERE key = ?", (THREADS,)).fetchone()) == before  # the XML's identifiers, not the arrival's
    conn.close()
    assert _rows(lib, "filed") == [f"a PDF of the XML, kept for its figures: from-the-publisher.pdf (its pages print the captions of 2 of its 3 figures); {stub}, the same bytes filed before for want of an identifier, goes with its rows"]


def test_a_pdf_kept_from_outside_the_inbox_is_left_where_it_was(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _threads(tmp_path)
    src = tmp_path / "Downloads" / "the-paper.pdf"
    src.parent.mkdir()
    shutil.copy(CHARTS / "page_paper.pdf", src)
    Worker(lib.dir.parent).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(src)], "offline": True, "known": {str(src): {"doi": "10.3390/mi15070851"}}})
    assert (lib.papers_dir / KEPT).read_bytes() == src.read_bytes()  # a copy kept; the person's file is theirs


def test_a_pdf_beside_an_xml_not_in_the_request_is_told_by_what_it_is(tmp_path, monkeypatch, capsys, _never_asked):
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    _rereading(monkeypatch)
    lib = _threads(tmp_path)
    events, _ = _attach(lib, capsys, known={"doi": "10.3390/mi15070851", "beside": str(lib.inbox_dir / "gone.xml")})
    paper = _one(events, "paper")
    assert (paper["paper"], paper["figures_file"], paper["by"]) == (THREADS, KEPT, "doi")


def test_a_pdf_known_by_its_pmid_alone_is_kept_beside_the_xml_paper_of_that_pmid(tmp_path, monkeypatch, capsys, _never_asked):
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    _rereading(monkeypatch)
    lib = _threads(tmp_path)
    conn = open_store(lib.store_path)
    conn.execute("UPDATE papers SET pmid = '39064362' WHERE key = ?", (THREADS,))
    conn.commit()
    conn.close()
    events, _ = _attach(lib, capsys, known={"pmid": "39064362"})
    paper = _one(events, "paper")
    assert (paper["paper"], paper["figures_file"], paper["by"]) == (THREADS, KEPT, "pmid") and (lib.papers_dir / KEPT).exists()


def test_a_pdf_that_names_the_paper_by_its_doi_alone_is_kept(tmp_path, monkeypatch):
    from litrag_parser import acquire

    conn = open_store(tmp_path / "store.sqlite")
    key = _xml_paper(conn, title="Fold change of fibroblast markers on each substrate")  # a title the page does not print
    page = CHARTS / "page_paper.pdf"
    assert figures.keep_beside(conn, tmp_path, key, page, "t")["why"] == "its first pages name neither its DOI nor its title"
    monkeypatch.setattr(acquire, "first_pages", lambda path, pages=3: "Micromachines 2024, 15, 851. https://doi.org/10.3390/mi15070851")
    assert figures.keep_beside(conn, tmp_path, key, page, "t")["kept"]


def test_a_fetch_keeps_the_pdf_it_fetched_beside_an_xml_for_that_xml_s_paper(tmp_path, monkeypatch, capsys, _never_asked):
    from litrag_parser import acquire, worker
    from litrag_parser.library import create_library
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    def parse_one(self, lib, key, path, *rest, **kw):
        conn = open_store(lib.store_path)
        _xml_nodes(conn, key, PRINTED)
        conn.close()

    def fetch_one(lib, conn, cand_id, **kw):
        xml, pdf = lib.inbox_dir / "PMC11278924.xml", lib.inbox_dir / "PMC11278924.pdf"
        shutil.copy(FIXTURES / "PMC11278924.xml", xml)
        shutil.copy(CHARTS / "page_paper.pdf", pdf)
        return {"cand_id": cand_id, "status": "fetched", "path": str(xml), "format": "jats", "error": None, "figures": str(pdf)}

    monkeypatch.setenv("LITRAG_FIGURES", "off")
    monkeypatch.setattr(Worker, "parse_one", parse_one)
    monkeypatch.setattr(acquire, "fetch_one", fetch_one)
    monkeypatch.setattr(worker, "lookup_record", lambda *a, **kw: None)  # a fetch is not offline: Europe PMC's record, not asked here
    monkeypatch.setattr(worker, "sniff_ids", lambda path: ("10.9999/other", None))  # the PDF, asked, would name another paper
    lib = create_library(tmp_path, "Threads")
    conn = open_store(lib.store_path)
    acquire.ensure_schema(conn)
    conn.execute("INSERT INTO candidates(doi, pmid, pmcid, title, status, found_at, updated_at) VALUES (?, ?, ?, ?, 'found', 't', 't')", ("10.3390/mi15070851", None, "PMC11278924", PRINTED))  # no PMID to meet the paper by
    conn.commit()
    cid = conn.execute("SELECT cand_id FROM candidates").fetchone()[0]
    conn.close()
    capsys.readouterr()
    Worker(tmp_path).do_fetch({"id": "f", "op": "fetch", "lib": lib.id, "ids": [cid]})
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT key, format, figures_file FROM papers")] == [(THREADS, "jats", KEPT)]  # the XML's paper's, not a paper of its own
    assert [tuple(r) for r in conn.execute("SELECT status, paper_key FROM candidates")] == [("ingested", THREADS)]
    conn.close()


# -- a PDF dropped on a figure, or caught for an XML paper's figures, goes to that paper -------------------------


def _unasked(monkeypatch):
    """Every way the worker has of asking a PDF, or Europe PMC, what a file is: none answers."""
    from litrag_parser import worker

    def unasked(*a, **kw):
        raise AssertionError("a PDF named for a paper is not asked what it is")

    for name in ("sniff_ids", "guess_title", "lookup_by_title", "lookup_record"):
        monkeypatch.setattr(worker, name, unasked)


def _held(lib):
    """Every row of the papers and of their events, to tell that nothing was written."""
    conn = open_store(lib.store_path)
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY key")], [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    finally:
        conn.close()


def test_a_pdf_named_for_a_paper_is_kept_beside_it_unasked_whatever_it_is_named(tmp_path, monkeypatch, capsys):
    _unasked(monkeypatch)
    read = _rereading(monkeypatch)
    lib = _threads(tmp_path)
    events, dropped = _attach(lib, capsys, name="10.9999_other.pdf", known={"paper": THREADS}, offline=False)  # a name that is another DOI; online, and nothing asked
    paper = _one(events, "paper")
    assert (paper["paper"], paper["existed"], paper["by"], paper["kept"], paper["figures_file"], paper["same"], paper["aside"]) == (THREADS, True, None, True, KEPT, False, None)
    assert not dropped.exists() and read == [] and _one(events, "done")["figures"] == [THREADS]  # nothing filed, and the figures read
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT key, file, format, figures_file FROM papers")] == [(THREADS, "paper.xml", "jats", KEPT)]
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ? AND status = 'read'", (THREADS,)).fetchone()[0] == 3
    conn.close()
    assert _rows(lib, "filed") == ["a PDF of the XML, kept for its figures: 10.9999_other.pdf (its pages print the captions of 2 of its 3 figures)"]


def test_a_pdf_named_for_a_paper_it_does_not_name_is_refused(tmp_path, monkeypatch, capsys):
    # named by the person, still checked: unassignable beats misassigned (K10)
    _unasked(monkeypatch)
    lib = _threads(tmp_path, title="Fold change of fibroblast markers on each substrate")  # a title the page does not print
    events, dropped = _attach(lib, capsys, name="10.9999_other.pdf", known={"paper": THREADS})
    reason = "its first pages name neither its DOI nor its title"
    assert _one(events, "skipped") == {"event": "skipped", "id": "i", "path": str(dropped), "paper": THREADS, "reason": reason}
    assert not [e for e in events if e["event"] == "paper"] and _one(events, "done")["figures"] == [] and dropped.exists()
    assert _rows(lib, "figures-refused") == [f"not kept for its figures: 10.9999_other.pdf: {reason}"]
    conn = open_store(lib.store_path)
    assert [tuple(r) for r in conn.execute("SELECT key, format, figures_file FROM papers")] == [(THREADS, "jats", None)]
    conn.close()


def test_a_pdf_named_for_no_paper_or_for_one_read_from_its_pdf_is_skipped_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _unasked(monkeypatch)
    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "pdf", None)])  # doi:10.1/held0, read from its PDF, its title one page_paper.pdf prints
    for key, reason in (("doi:none", "no such paper"), ("doi:10.1/held0", "not a paper read from XML")):
        before = _held(lib)
        events, dropped = _attach(lib, capsys, name="for-its-figures.pdf", known={"paper": key})
        assert _one(events, "skipped") == {"event": "skipped", "id": "i", "path": str(dropped), "paper": key, "reason": reason}
        assert not [e for e in events if e["event"] == "paper"] and (_one(events, "done")["parsed"], _one(events, "done")["figures"]) == ([], [])
        # no paper filed in its place, and no row saying a PDF came for figures nobody wanted one for
        assert _held(lib) == before and dropped.exists() and not list(lib.papers_dir.glob("*.figures*"))


def test_a_pdf_named_for_a_paper_is_kept_beside_it_though_the_request_says_reread(tmp_path, monkeypatch, capsys):
    # reread replaces a paper's reading with its file; a PDF dropped on a figure is not the paper's file
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    _unasked(monkeypatch)
    read = _rereading(monkeypatch)
    lib = _threads(tmp_path)
    events, _ = _attach(lib, capsys, known={"paper": THREADS}, reread=True)
    assert _one(events, "paper")["figures_file"] == KEPT and read == []
    conn = open_store(lib.store_path)
    assert tuple(conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (THREADS,)).fetchone()) == ("paper.xml", "jats", KEPT)  # read from its XML still
    conn.close()


def test_an_xml_named_for_a_paper_is_read_as_any_dropped_xml_is(tmp_path, monkeypatch, capsys):
    from litrag_parser.worker import Worker

    from test_worker import FIXTURES

    def dropped(known):
        lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)])  # doi:10.1/held0, read from XML
        xml = lib.inbox_dir / "PMC11278924.xml"
        shutil.copy(FIXTURES / "PMC11278924.xml", xml)
        capsys.readouterr()
        keys = Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(xml)], "offline": True, **({"known": {str(xml): known}} if known else {})})
        events = [{k: v for k, v in json.loads(line).items() if k != "lib"} for line in capsys.readouterr().out.splitlines() if line.strip()]
        conn = open_store(lib.store_path)
        rows = [tuple(r) for r in conn.execute("SELECT key, file, format, figures_file FROM papers ORDER BY key")]
        conn.close()
        return list(keys.values()), events, rows

    named = dropped({"paper": "doi:10.1/held0"})
    assert named == dropped(None)  # as though nothing were known of it
    keys, events, rows = named
    assert keys == [THREADS] and rows == [("doi:10.1/held0", "held0.xml", "jats", None), (THREADS, "doi_10.3390_mi15070851.xml", "jats", None)]
    assert not [e for e in events if e["event"] == "skipped"]


def test_a_twin_dropped_on_its_paper_s_figure_is_kept_there_and_the_paper_it_was_filed_as_goes(tmp_path, monkeypatch, capsys):
    # the twin note's advice, followed
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)])
    _figured(lib)
    first, twins, _ = _drop(lib, capsys)
    assert first["paper"].startswith("sha:") and [k for k, _ in twins] == [first["paper"]]
    assert twins[0][1].endswith("; if it is that paper, drop it on one of that paper's figures")
    _unasked(monkeypatch)
    events, dropped = _attach(lib, capsys, name="scan.pdf", known={"paper": "doi:10.1/held0"})
    assert _one(events, "paper")["figures_file"] == "doi_10.1_held0.figures.pdf" and not dropped.exists()
    conn = open_store(lib.store_path)
    assert [r[0] for r in conn.execute("SELECT key FROM papers")] == ["doi:10.1/held0"]  # filed once
    conn.close()
    assert _rows(lib, "filed", "doi:10.1/held0") == [f"a PDF of the XML, kept for its figures: scan.pdf (its pages print the captions of 1 of its 1 figure); {first['paper']}, the same bytes filed before for want of an identifier, goes with its rows"]


def test_the_pdf_kept_for_a_paper_s_figures_filed_again_apart_goes_when_dropped_on_its_figure(tmp_path, monkeypatch, capsys):
    # kept through a figure, then dropped where no figure is: filed apart by its hash, which filing
    # meets only in a paper's own file; the twin note followed finds it kept already, and the stub goes
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    lib = _holding(tmp_path, monkeypatch, [(PRINTED, "jats", None)])
    _figured(lib)
    _attach(lib, capsys, name="scan.pdf", known={"paper": "doi:10.1/held0"})
    first, twins, _ = _drop(lib, capsys)
    assert first["paper"].startswith("sha:") and [k for k, _ in twins] == [first["paper"]]
    events, dropped = _attach(lib, capsys, name="scan.pdf", known={"paper": "doi:10.1/held0"})
    paper = _one(events, "paper")
    assert (paper["paper"], paper["same"], paper["figures_file"], paper["aside"]) == ("doi:10.1/held0", True, "doi_10.1_held0.figures.pdf", None) and not dropped.exists()
    conn = open_store(lib.store_path)
    assert [r[0] for r in conn.execute("SELECT key FROM papers")] == ["doi:10.1/held0"]  # filed once
    conn.close()
    assert _rows(lib, "filed", "doi:10.1/held0") == [
        "a PDF of the XML, kept for its figures: scan.pdf (its pages print the captions of 1 of its 1 figure)",
        f"the PDF kept for its figures, doi_10.1_held0.figures.pdf, again: scan.pdf; {first['paper']}, the same bytes filed before for want of an identifier, goes with its rows",
    ]
    # and once more: nothing left to drop, and nothing said
    events, _ = _attach(lib, capsys, name="scan.pdf", known={"paper": "doi:10.1/held0"})
    assert _one(events, "paper")["same"] and len(_rows(lib, "filed", "doi:10.1/held0")) == 2


def test_a_pdf_dropped_from_elsewhere_for_a_paper_keyed_by_its_pmcid_is_kept_and_left_where_it_was(tmp_path, monkeypatch, capsys):
    # a window drop comes from anywhere on disk, and the key is the paper's as filed, capitals and all
    from litrag_parser.library import create_library
    from litrag_parser.worker import Worker

    _unasked(monkeypatch)
    _rereading(monkeypatch)
    lib = create_library(tmp_path, "Threads")
    conn = open_store(lib.store_path)
    key = _xml_paper(conn, doi=None, pmcid="PMC11278924", title=PRINTED)
    conn.close()
    assert key == "pmcid:PMC11278924"
    elsewhere = tmp_path / "Downloads" / "article.pdf"
    elsewhere.parent.mkdir()
    shutil.copy(CHARTS / "page_paper.pdf", elsewhere)
    capsys.readouterr()
    Worker(tmp_path).do_ingest({"id": "i", "op": "ingest", "lib": lib.id, "paths": [str(elsewhere)], "known": {str(elsewhere): {"paper": key}}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert not [e for e in events if e["event"] == "skipped"]
    paper = _one(events, "paper")
    assert (paper["paper"], paper["by"], paper["figures_file"]) == (key, None, "pmcid_PMC11278924.figures.pdf")
    assert elsewhere.exists() and (lib.papers_dir / "pmcid_PMC11278924.figures.pdf").exists()  # copied in; the person's file left where it was


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
