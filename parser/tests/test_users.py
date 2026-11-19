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


def test_a_password_is_kept_only_as_a_salted_scrypt_hash():
    h = users.hash_password("correct horse", n=2 ** 10)
    kind, n, r, p, salt, key = h.split("$")
    assert (kind, n, r, p) == ("scrypt", "1024", "8", "1") and "correct horse" not in h
    assert users.check_password("correct horse", h) and not users.check_password("correct hors", h)
    assert users.hash_password("correct horse", n=2 ** 10) != h  # a new salt every time
    assert not users.check_password("x", "md5$abc") and not users.check_password("x", "garbage")
    assert users.N >= 2 ** 15 and users.R >= 8  # the shipped cost: OWASP's floor for scrypt


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
