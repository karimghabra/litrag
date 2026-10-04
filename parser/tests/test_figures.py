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
    assert figures.remap(conn, key) == 3
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


def _xml_paper(conn, figures_n=3, doi="10.3390/mi15070851"):
    """A paper read from XML: its figures numbered by their captions, no page, no box."""
    key = file_paper(conn, title="read from XML", file="paper.xml", sha256="x1", fmt="jats", doi=doi, pmid=None, pmcid=None, now="t").key
    rows = [(f"{key}#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "")]
    for i in range(1, figures_n + 1):
        rows.append((f"{key}#section-1#picture-{i}", key, f"{key}#section-1", i, 2, "picture", "picture", "results", None, '["3 Results"]', ""))
        rows.append((f"{key}#section-1#picture-{i}#caption-1", key, f"{key}#section-1#picture-{i}", 0, 3, "caption", "caption", "results", None, '["3 Results"]', f"Figure {i} What figure {i} shows."))
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed', file = 'paper.xml' WHERE key = ?", (key,))
    conn.commit()
    return key


def test_a_caption_is_told_from_a_sentence_that_begins_with_a_figure():
    from litrag_parser.charts import Word

    def caps(texts):
        return [n for n, _ in figures.captions([Word(10, 10 + 20 * i, 400, 22 + 20 * i, t) for i, t in enumerate(texts)])]

    assert caps(["Figure 2. Fold change", "FIGURE 3 Percent reduction", "Fig. 4. SEM images", "Figure 5: Strength", "Figure 6 | Modulus", "Fig. S1. More"]) == ["2", "3", "4", "5", "6", "S1"]
    assert caps(["Figure 2 shows the strength", "(Figure 2)", "Figures 3 and 4 show", "as Figure 1 shows", "Figure 2.5 times"]) == []


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
    # a rebuild renumbers the XML's figures: the plots follow their number
    conn.execute("UPDATE nodes SET node_id = replace(node_id, '#picture-', '#fig-'), parent = replace(parent, '#picture-', '#fig-') WHERE paper = ?", (key,))
    conn.commit()
    assert figures.remap(conn, key) == 3 and {r[0] for r in conn.execute("SELECT DISTINCT figure FROM chart_values")} == {f"{key}#section-1#fig-1", f"{key}#section-1#fig-2"}


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
    assert paper["kept"] and paper["figures_file"].endswith(".figures.pdf")
    conn = open_store(lib / "store.sqlite")
    row = conn.execute("SELECT file, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    assert (row["file"], row["format"]) == ("paper.xml", "jats")  # the paper is its XML still
    assert (lib / "papers" / row["figures_file"]).exists() and not dropped.exists()
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ? AND status = 'read'", (key,)).fetchone()[0] == 3
    assert figures.figures_wanted(conn) == [] and figures.is_read(conn, key)
    assert any(e["event"] == "log" and "charts read from the PDF beside the XML" in e["message"] for e in events)
    conn.close()
