"""users.py and the worker's account ops: passwords kept only as a slow salted hash, sign-in and
sessions, and each person's recent papers, favourites, notes and collections kept apart from
everyone else's — over papers every account reads."""

import sqlite3
import time

import pytest

from litrag_parser import members, users
from litrag_parser.library import create_library
from litrag_parser.store import open_store

from test_shared import _hold
from test_worker import by, talk


@pytest.fixture
def cheap(monkeypatch):
    """scrypt at a test's cost: the parameters are stored with each hash, so checking reads them back."""
    monkeypatch.setattr(users, "N", 2 ** 10)


def test_a_password_is_kept_only_as_a_salted_scrypt_hash(cheap):
    h = users.hash_password("correct horse")
    kind, n, r, p, salt, key = h.split("$")
    assert (kind, n, r, p) == ("scrypt", "1024", "8", "1") and "correct horse" not in h
    assert users.check_password("correct horse", h) and not users.check_password("correct hors", h)
    assert users.hash_password("correct horse") != h  # a new salt every time
    assert not users.check_password("x", "md5$abc") and not users.check_password("x", "garbage")


def test_the_shipped_cost_of_a_password_is_owasp_s_floor_for_scrypt():
    assert users.N >= 2 ** 15 and users.R >= 8


def test_an_account_is_made_signed_into_and_never_stores_its_password(tmp_path, cheap):
    u = users.Users(tmp_path / "users.sqlite")
    me = u.create("Ana López", "a long password")
    assert me == {"id": "ana lopez", "name": "Ana López"}
    with pytest.raises(users.AccountError, match="already"):
        u.create("ana lopez", "another password")
    with pytest.raises(users.AccountError, match="8 characters"):
        u.create("Ben", "short")
    with pytest.raises(users.AccountError, match="do not match"):
        u.sign_in("Ana López", "the wrong one")
    with pytest.raises(users.AccountError, match="do not match"):  # no such name: the same words
        u.sign_in("Nobody", "a long password")
    assert u.sign_in("ANA LOPEZ", "a long password")["id"] == "ana lopez"
    u.set_password("ana lopez", "a long password", "a new long password")
    assert u.sign_in("Ana López", "a new long password")["name"] == "Ana López"
    assert u.list() == [{"id": "ana lopez", "name": "Ana López"}]
    u.close()
    raw = (tmp_path / "users.sqlite").read_bytes()
    assert b"a long password" not in raw and b"a new long password" not in raw


def test_a_name_in_any_script_is_an_account_of_its_own(tmp_path, cheap):
    u = users.Users(tmp_path / "users.sqlite")
    made = [u.create(name, "a long password")["id"] for name in ("李 Wei", "王 Wei", "李明", "كريم", "Søren", "Soren", "Łukasz")]
    assert len(set(made)) == len(made) and all(made)
    assert u.sign_in("王 WEI", "a long password")["name"] == "王 Wei"
    assert u.sign_in("SØREN", "a long password")["name"] == "Søren"
    assert u.sign_in("李明", "a long password")["name"] == "李明"
    with pytest.raises(users.AccountError, match="already"):
        u.create("ŁUKASZ", "another password")
    u.close()


def test_each_person_s_recent_favourites_notes_and_collections_are_their_own(tmp_path, cheap):
    u = users.Users(tmp_path / "users.sqlite")
    ana, ben = u.create("Ana", "password-a")["id"], u.create("Ben", "password-b")["id"]
    for k in ("doi:1", "doi:2", "doi:1"):
        u.viewed(ana, k, "tendon")
        time.sleep(0.001)
    assert [r["paper"] for r in u.recent(ana)] == ["doi:1", "doi:2"] and u.recent(ben) == []
    u.favourite(ana, "doi:2", True, "tendon")
    assert [f["paper"] for f in u.favourites(ana)] == ["doi:2"] and u.favourites(ben) == []
    u.favourite(ana, "doi:2", False)
    assert u.favourites(ana) == []
    note = u.save_note(ana, "doi:1", "Check the buffer.", node_id="doi:1#p3", quote="crosslinked at 105 °C")
    assert u.notes(ana, "doi:1")[0]["node_id"] == "doi:1#p3" and u.notes(ben, "doi:1") == []
    with pytest.raises(users.AccountError):
        u.save_note(ben, "doi:1", "not mine", note_id=note["id"])  # another's note cannot be edited
    assert not u.delete_note(ben, note["id"]) and u.delete_note(ana, note["id"])
    c = u.create_collection(ana, "To print")
    with pytest.raises(users.AccountError, match="already"):
        u.create_collection(ana, "To print")
    assert u.create_collection(ben, "To print")["id"] != c["id"]  # names are per person
    assert u.add_to_collection(ana, c["id"], ["doi:1", "doi:2", "doi:1"]) == 2
    with pytest.raises(users.AccountError):
        u.add_to_collection(ben, c["id"], ["doi:3"])
    assert [x["papers"] for x in u.collections(ana)] == [2]
    u.rename_collection(ana, c["id"], "Printed")
    assert u.collections_of(ana, "doi:1") == [c["id"]]
    u.remove_from_collection(ana, c["id"], ["doi:1"])
    assert [p["paper"] for p in u.collection_papers(ana, c["id"])] == ["doi:2"]
    u.delete_collection(ana, c["id"])
    assert [x["name"] for x in u.collections(ana)] == []


def test_recent_keeps_the_newest_only(tmp_path, cheap, monkeypatch):
    monkeypatch.setattr(users, "RECENT_KEEP", 3)
    u = users.Users(tmp_path / "users.sqlite")
    ana = u.create("Ana", "password-a")["id"]
    for i in range(5):
        u.viewed(ana, f"doi:{i}", None)
    assert [r["paper"] for r in u.recent(ana)] == ["doi:4", "doi:3", "doi:2"]


def test_sessions_are_tokens_the_worker_keeps_in_memory():
    s = users.Sessions()
    t = s.open("ana")
    assert s.user(t) == "ana" and len(t) >= 40
    s.close(t)
    with pytest.raises(users.AccountError, match="not signed in"):
        s.user(t)


def test_the_worker_signs_in_and_hands_out_sessions_a_restart_forgets(tmp_path):
    events = talk(tmp_path, [
        {"id": "u0", "op": "users"},
        {"id": "c", "op": "user_create", "name": "Ana", "password": "password-a"},
        {"id": "b", "op": "user_create", "name": "Ben", "password": "password-b"},
        {"id": "bad", "op": "sign_in", "name": "Ana", "password": "nope-nope"},
        {"id": "u1", "op": "users"},
    ])
    assert by(events, "u0")[0]["users"] == []
    ana, ben = by(events, "c")[0], by(events, "b")[0]
    assert ana["event"] == "signed_in" and ana["user"]["name"] == "Ana" and ana["session"]
    assert by(events, "bad")[0]["event"] == "error"
    assert [x["name"] for x in by(events, "u1")[0]["users"]] == ["Ana", "Ben"]
    assert "pw_hash" not in by(events, "u1")[0]["users"][0]
    # a new worker: the sessions are gone, the accounts are not
    events = talk(tmp_path, [
        {"id": "s", "op": "sign_in", "name": "Ana", "password": "password-a"},
        {"id": "t", "op": "sign_in", "name": "Ben", "password": "password-b"},
        {"id": "x", "op": "favourites", "session": "stale"},
    ])
    sa, sb = by(events, "s")[0]["session"], by(events, "t")[0]["session"]
    assert by(events, "x")[0]["event"] == "error" and sa != sb


def test_a_project_merged_while_signed_in_is_the_account_s_that_merged_it(tmp_path):
    events = talk(tmp_path, [
        {"id": "a", "op": "user_create", "name": "Ana", "password": "password-a"},
        {"id": "b", "op": "user_create", "name": "Ben", "password": "password-b"},
        {"id": "i1", "op": "init", "name": "Tendon"},
        {"id": "i2", "op": "init", "name": "Bone"},
    ])
    assert by(events, "i2")[0]["library"]["owner"] is None
    events = _merged_as(tmp_path)
    assert by(events, "m")[-1]["event"] == "done"
    events = talk(tmp_path, [
        {"id": "p", "op": "projects"},
        {"id": "anon", "op": "delete_project", "lib": "tendon-all"},
    ])
    owners = {p["id"]: p["owner"] for p in by(events, "p")[0]["projects"]}
    assert owners == {"tendon": None, "bone": None, "tendon-all": "ana", "loose": None}
    assert by(events, "anon")[0]["event"] == "error"
    assert "tendon-all" in {p["id"] for p in by(talk(tmp_path, [{"id": "p", "op": "projects"}]), "p")[0]["projects"]}


def _merged_as(root):
    """One worker run: Ana signs in and merges Tendon and Bone, then a merge is asked with no
    session, and one with a session nobody holds. A session lives only in the worker that made
    it, so the requests that carry it are written as that worker answers."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    proc = subprocess.Popen([sys.executable, "-m", "litrag_parser.worker", f"--root={root}"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", cwd=Path(__file__).parent.parent)
    events: list[dict] = []

    def send(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()

    def until(req_id, kinds):
        while True:
            line = proc.stdout.readline()
            assert line, "the worker stopped"
            e = json.loads(line)
            events.append(e)
            if e.get("id") == req_id and e.get("event") in kinds:
                return e

    send({"id": "s", "op": "sign_in", "name": "Ana", "password": "password-a"})
    session = until("s", ("signed_in", "error"))["session"]
    send({"id": "m", "op": "merge", "sources": ["tendon", "bone"], "name": "Tendon all", "session": session})
    until("m", ("done", "error"))
    send({"id": "n", "op": "merge", "sources": ["tendon", "bone"], "name": "Loose"})
    until("n", ("done", "error"))
    send({"id": "x", "op": "merge", "sources": ["tendon", "bone"], "name": "Stale", "session": "nobody"})
    assert until("x", ("queued", "done", "error"))["event"] == "error"
    send({"id": "q", "op": "quit"})
    proc.stdin.close()
    assert proc.wait(timeout=120) == 0
    return events


def test_the_worker_s_per_person_ops_over_one_run(tmp_path, monkeypatch):
    from litrag_parser.worker import Worker

    monkeypatch.setattr(users, "N", 2 ** 10)
    lib = create_library(tmp_path, "Tendon", shared=True)
    lib.ensure_dirs()
    conn = open_store(lib.store_path)
    key = _hold(lib, conn, "10.1/one", "one")
    conn.close()
    seen: list[dict] = []
    monkeypatch.setattr("litrag_parser.worker.emit", seen.append)
    w = Worker(tmp_path)

    def ask(**req):
        req.setdefault("id", f"r{len(seen)}")
        w.answer(req)
        return seen[-1]

    sa = ask(op="user_create", name="Ana", password="password-a")["session"]
    sb = ask(op="user_create", name="Ben", password="password-b")["session"]
    assert ask(op="viewed", session=sa, lib="tendon", key=key)["event"] == "viewed"
    recent = ask(op="recent", session=sa)["papers"]
    assert [r["paper"] for r in recent] == [key] and recent[0]["title"] and recent[0]["projects"] == ["tendon"]
    assert ask(op="recent", session=sb)["papers"] == []
    assert ask(op="favourite", session=sa, key=key, on=True, lib="tendon")["on"] is True
    assert [f["paper"] for f in ask(op="favourites", session=sa)["papers"]] == [key]
    assert ask(op="favourites", session=sb)["papers"] == []
    n = ask(op="note_save", session=sa, key=key, text="Why 105 °C?", node_id=f"{key}#p1")["note"]
    assert [x["text"] for x in ask(op="notes", session=sa, key=key)["notes"]] == ["Why 105 °C?"]
    assert ask(op="notes", session=sb, key=key)["notes"] == []
    assert ask(op="note_delete", session=sb, id=n["id"])["deleted"] is False
    c = ask(op="collection_create", session=sa, name="To print")["collection"]
    assert ask(op="collection_add", session=sa, id=c["id"], keys=[key], lib="tendon")["changed"] == 1
    assert [p["paper"] for p in ask(op="collection", session=sa, id=c["id"])["papers"]] == [key]
    assert ask(op="collection", session=sb, id=c["id"])["event"] == "error"
    assert [x["name"] for x in ask(op="collections", session=sa)["collections"]] == ["To print"]
    assert ask(op="collections", session=sb)["collections"] == []
    mark = ask(op="paper_marks", session=sa, key=key)
    assert mark["favourite"] is True and mark["collections"] == [c["id"]] and mark["notes"] == 1
    # projects: made by one person, renamed and deleted only by them; read by everyone
    made = ask(op="init", session=sa, name="Ana's bench")["library"]
    assert made["owner"] == "ana"
    assert ask(op="describe", session=sb, lib=made["id"], name="Ben's now")["event"] == "error"
    assert ask(op="describe", session=sa, lib=made["id"], name="Ana's tendon bench")["project"]["name"] == "Ana's tendon bench"
    assert ask(op="papers", session=sb, lib="tendon")["papers"]  # every account reads every project
    assert ask(op="delete_project", session=sb, lib=made["id"])["event"] == "error"
    assert ask(op="delete_project", session=sa, lib=made["id"])["event"] == "deleted"
    ask(op="sign_out", session=sa)
    assert ask(op="recent", session=sa)["event"] == "error"
