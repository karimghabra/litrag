"""What every test starts from: no oracle configured, no test writing verdicts into
the user's real library root, OpenAlex and open copies off, and Europe PMC, NCBI and the PMC
Cloud Service pointed at a port nothing answers on. `fake_oracle` is an oracle over a toy embedder for the tests
that need one; `epmc` a canned Europe PMC, NCBI and PMC Cloud Service on 127.0.0.1 (`Canned`), for
the tests that ask them."""

import hashlib
import json
import re
import threading
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from litrag_parser import acquire, lanes, meaning


@pytest.fixture(autouse=True)
def _no_oracle_leaks(monkeypatch):
    lanes._active = None
    monkeypatch.setenv("LITRAG_LANES", "off")  # the subprocess worker in test_worker inherits this
    monkeypatch.setenv("LITRAG_OPENALEX", "off")  # no test reaches OpenAlex: those that ask it point it at a canned one
    monkeypatch.setenv("LITRAG_OPENALEX_URL", "http://127.0.0.1:9")
    for env in ("LITRAG_EPMC_URL", "LITRAG_EPMC_PDF_URL", "LITRAG_NCBI_URL", "LITRAG_PMC_CLOUD_URL"):
        monkeypatch.setenv(env, "http://127.0.0.1:9")  # nor Europe PMC, NCBI or the PMC Cloud Service: the `epmc` fixture points them at a canned one
    monkeypatch.setenv("LITRAG_OPEN_COPIES", "off")  # nor any open copy's host: those that fetch one turn it on, against a canned one
    yield
    lanes._active = None


def bag_embed(texts):
    """A toy embedder: a bag of words hashed into 256 buckets, plus a few features of shape
    (digits, initials, punctuation), so texts sharing words or shape lie near each other.
    Tests over it prove the plumbing — where the oracle is asked, where it is not, and that
    every answer replays from the store — not that the thresholds separate anything; the
    thresholds come from measurements on the corpora (NOTES.md)."""
    out = []
    for t in texts:
        t = t.split(": ", 1)[1] if ": " in t[:20] else t  # the task prefix stripped
        v = [0.0] * 264
        words = re.findall(r"[A-Za-z]+", t.lower())
        for w in words:
            v[zlib.crc32(w.encode()) % 256] += 1.0
        n = max(len(t), 1)
        v[256] = 4.0 * sum(ch.isdigit() for ch in t) / n
        v[257] = 4.0 * len(re.findall(r"\b[A-Z]\.", t)) / max(len(words), 1)
        v[258] = 4.0 * (t.count(",") + t.count(";")) / max(len(words), 1)
        v[259] = 2.0 * (t.count("(") + t.count(")")) / n
        v[260] = 1.0 if re.match(r"^\s*(?:\[\d+\]|\d+\.?)\s", t) else 0.0
        v[261] = 1.0 if re.search(r"\b(?:19|20)\d{2}\b", t) else 0.0
        v[262] = 3.0 * sum(1 for w in t.split() if w[:1].isupper()) / max(len(t.split()), 1)
        v[263] = 0.2  # so nothing is orthogonal to everything
        out.append(v)
    return out


@pytest.fixture
def fake_oracle(tmp_path, monkeypatch):
    """The process-wide oracle, over the toy embedder, with its verdicts in a temporary store."""
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: bag_embed(texts))
    monkeypatch.setenv("LITRAG_LANES", "on")
    o = lanes.configure(tmp_path / "lanes.sqlite")
    o.register(meaning.Kind("block", meaning.BLOCK_EXAMPLES, threshold=0.62, margin=0.03))  # the example paragraphs, in the toy embedder's space, not the shipped centroids
    o.register(meaning.Kind("heading", meaning.HEADING_PROTOTYPES, threshold=0.75, margin=0.08, prefix=meaning.QUERY))  # likewise the example headings, not data/headings.json
    o.register(meaning.Kind("canonical", {}, threshold=0.7, margin=0.05, prefix=meaning.QUERY, source="silent in the tests"))
    return o


class Canned:
    """A tiny Europe PMC: routes by path (a callable route answers by the query), every request
    remembered. Under `/cloud/`, a listing (`?list-type=2&prefix=...&delimiter=/`) is answered as
    S3 answers one, from the routes there."""

    def __init__(self):
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        self.seen: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.seen.append(self.path)
                split = urllib.parse.urlsplit(self.path)
                path, q = split.path, urllib.parse.parse_qs(split.query)
                if path == "/cloud/" and q.get("list-type") == ["2"]:
                    status, ctype, body, extra = 200, "application/xml", outer.listing(q["prefix"][0]), []
                else:
                    route = outer.routes.get(path, (404, "text/html", b"<html>not found</html>"))
                    status, ctype, body, *extra = route(q) if callable(route) else route  # a route that answers by its query
                self.send_response(status)
                for k, v in (extra[0] if extra else {}).items():  # a route's own headers, when it has any
                    self.send_header(k, v)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def json(self, path, obj, status=200):
        self.routes[path] = (status, "application/json", json.dumps(obj).encode())

    def listing(self, prefix):
        folders = sorted({k.removeprefix("/cloud/").split("/")[0] for k in self.routes if k.startswith(f"/cloud/{prefix}")})
        common = "".join(f"<CommonPrefixes><Prefix>{f}/</Prefix></CommonPrefixes>" for f in folders)
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Name>pmc-oa-opendata</Name><Prefix>{prefix}</Prefix><KeyCount>{len(folders)}</KeyCount><Delimiter>/</Delimiter>"
                f"<IsTruncated>false</IsTruncated>{common}</ListBucketResult>").encode()

    def cloud(self, pmcid, version=1, pdf=b"%PDF-1.5 from the cloud", manuscript=False, md5=None):
        """An article in the Cloud Service, shaped like a live record (PMC11278924.1 and, for an
        author manuscript, PMC5653421.1, checked 2026-10-04): a PDF with its MD5 in `pdf_url`;
        a manuscript with XML and text and no PDF."""
        v = f"{pmcid}.{version}"
        meta = {"pmcid": pmcid, "version": version, "is_pmc_openaccess": not manuscript, "is_manuscript": manuscript,
                "license_code": "TDM" if manuscript else "CC BY", "xml_url": f"s3://pmc-oa-opendata/{v}/{v}.xml",
                "text_url": f"s3://pmc-oa-opendata/{v}/{v}.txt"}
        if not manuscript:
            meta["pdf_url"] = f"s3://pmc-oa-opendata/{v}/{v}.pdf?md5={md5 or hashlib.md5(pdf).hexdigest()}"
            self.routes[f"/cloud/{v}/{v}.pdf"] = (200, "application/pdf", pdf)
        self.json(f"/cloud/{v}/{v}.json", meta)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def epmc(monkeypatch):
    c = Canned()
    monkeypatch.setenv("LITRAG_EPMC_URL", f"{c.url}/rest")
    monkeypatch.setenv("LITRAG_EPMC_PDF_URL", f"{c.url}/oa")
    monkeypatch.setenv("LITRAG_NCBI_URL", f"{c.url}/ncbi")
    monkeypatch.setenv("LITRAG_PMC_CLOUD_URL", f"{c.url}/cloud")
    monkeypatch.delenv("LITRAG_NCBI_EMAIL", raising=False)
    monkeypatch.delenv("LITRAG_NCBI_API_KEY", raising=False)
    monkeypatch.setattr(acquire, "NCBI_GAP", 0)
    yield c
    c.close()
