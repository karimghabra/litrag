"""Local accounts, and what each person keeps of their own: recently viewed papers, favourites,
notes on a paper (or on one passage of it), and collections — folders of papers that are theirs,
independent of any project.

What this protects, said plainly: on one shared machine, or a root on a shared folder, an
account tells the people of a lab apart and keeps each one's recent papers, favourites, notes and
collections out of the others' way. It is **not** a security boundary. Anyone who can read the
root can open `users.sqlite` and read every note in it, and anyone who can write it can change
it; the papers themselves were never per person, and every account reads every project. The
password keeps one person from signing in as another through the window, nothing more. It is
kept only as a slow, salted hash (scrypt, from Python's own `hashlib`, which is OpenSSL's), never
as itself, and nothing about an account leaves the machine.

The rows live in `<root>/users.sqlite`, apart from the papers, so a later server could keep them
instead: the window reaches them only through the worker's account ops (`worker.py`), each
carrying the session `sign_in` handed out, never through this file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any

from .library import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,                -- the name folded: what the account is known by
  name TEXT NOT NULL,                 -- as the person wrote it
  pw_hash TEXT NOT NULL,              -- scrypt$<n>$<r>$<p>$<salt>$<key>, base64; never the password
  created_at TEXT NOT NULL,
  signed_in_at TEXT
);
CREATE TABLE IF NOT EXISTS recent (
  user TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paper TEXT NOT NULL,                -- papers.key: the same paper in whichever project
  project TEXT,                       -- the project it was opened in, to open it there again
  viewed_at TEXT NOT NULL,
  PRIMARY KEY(user, paper)
);
CREATE TABLE IF NOT EXISTS favourites (
  user TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paper TEXT NOT NULL,
  project TEXT,
  added_at TEXT NOT NULL,
  PRIMARY KEY(user, paper)
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  user TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paper TEXT NOT NULL,
  node_id TEXT,                       -- a passage of it, or NULL for the paper as a whole
  quote TEXT,                         -- the passage's first words when the note was written, to find it again
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_paper ON notes(user, paper);
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY,
  user TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user, name)
);
CREATE TABLE IF NOT EXISTS collection_papers (
  collection INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  paper TEXT NOT NULL,
  project TEXT,
  added_at TEXT NOT NULL,
  PRIMARY KEY(collection, paper)
);
"""

#: scrypt's cost: 2^15 × 8 × 128 bytes = 32 MiB and a few hundred milliseconds a guess. Stored
#: with each hash, so raising it later leaves old hashes checkable.
N, R, P = 2 ** 15, 8, 1
KEY_BYTES = 32
MIN_PASSWORD = 8
RECENT_KEEP = 50


def move_paper(store: sqlite3.Connection, old: str, new: str) -> None:
    """Every person's recent entry, favourite, notes and collection entries of paper `old` are
    paper `new`'s: called when the store open on `store` replaces a paper's key, so what a person
    kept of the paper follows it as the projects holding it do (store.move_memberships). The
    accounts are found beside the libraries the store is one of (`<root>/users.sqlite`); a root
    with none has nothing to move. A note on a passage names the same passage of `new`."""
    file = store.execute("PRAGMA database_list").fetchone()[2]
    path = Path(file).parent.parent / "users.sqlite" if file else None
    if path is None or not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        with conn:
            for table in ("recent", "favourites", "collection_papers"):
                conn.execute(f"UPDATE OR IGNORE {table} SET paper = ? WHERE paper = ?", (new, old))
                conn.execute(f"DELETE FROM {table} WHERE paper = ?", (old,))
            conn.execute("UPDATE notes SET node_id = ? || substr(node_id, length(?) + 1) WHERE paper = ? AND substr(node_id, 1, length(?) + 1) = ? || '#'", (new, old, old, old, old))
            conn.execute("UPDATE notes SET paper = ? WHERE paper = ?", (new, old))
    finally:
        conn.close()


class AccountError(ValueError):
    """A request about accounts the store refuses, with the reason a person can read."""


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise AccountError("a password cannot be empty")
    n, r, p = N, R, P
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=4 * 128 * r * n)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${n}${r}${p}${b64(salt)}${b64(key)}"


def check_password(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` was made from, in time that does not depend on how
    much of it matched."""
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        n_, r_, p_ = int(n), int(r), int(p)
        want = base64.b64decode(key)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=n_, r=r_, p=p_, dklen=len(want), maxmem=4 * 128 * r_ * n_)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def user_id(name: str) -> str:
    """An account's id: its name with case and accents folded and its spaces made single, so
    "Ana López" and "ana lopez" are one account. Only the accents are dropped (the combining
    diacritical marks a decomposed letter leaves), never a letter: a name in any script keeps
    every character of its own."""
    s = "".join(c for c in unicodedata.normalize("NFKD", name) if not "\u0300" <= c <= "\u036f").casefold()
    return re.sub(r"\s+", " ", s).strip()


class Users:
    """The accounts store: one SQLite file, one connection, one writer at a time."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- accounts

    def list(self) -> list[dict[str, Any]]:
        """Every account's id and name, for the sign-in list: nothing else of it."""
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT id, name FROM users ORDER BY name COLLATE NOCASE")]

    def create(self, name: str, password: str) -> dict[str, Any]:
        name = (name or "").strip()
        uid = user_id(name)
        if not uid:
            raise AccountError("an account needs a name")
        if len(password or "") < MIN_PASSWORD:
            raise AccountError(f"a password needs {MIN_PASSWORD} characters or more")
        with self._lock, self.conn:
            if self.conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
                raise AccountError(f"there is an account called {name!r} already")
            now = now_iso()
            self.conn.execute("INSERT INTO users(id, name, pw_hash, created_at, signed_in_at) VALUES (?, ?, ?, ?, ?)", (uid, name, hash_password(password), now, now))
        return {"id": uid, "name": name}

    def sign_in(self, name: str, password: str) -> dict[str, Any]:
        uid = user_id(name or "")
        with self._lock:
            row = self.conn.execute("SELECT id, name, pw_hash FROM users WHERE id = ?", (uid,)).fetchone()
        # a name with no account costs as much as a wrong password, and says the same
        ok = check_password(password or "", row["pw_hash"] if row else _dummy())
        if row is None or not ok:
            raise AccountError("that name and password do not match an account")
        with self._lock, self.conn:
            self.conn.execute("UPDATE users SET signed_in_at = ? WHERE id = ?", (now_iso(), uid))
        return {"id": row["id"], "name": row["name"]}

    def set_password(self, uid: str, old: str, new: str) -> None:
        with self._lock:
            row = self.conn.execute("SELECT pw_hash FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None or not check_password(old or "", row["pw_hash"]):
            raise AccountError("the current password is not right")
        if len(new or "") < MIN_PASSWORD:
            raise AccountError(f"a password needs {MIN_PASSWORD} characters or more")
        with self._lock, self.conn:
            self.conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), uid))

    def name_of(self, uid: str) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
        return row["name"] if row else None

    # ---------------------------------------------------------------- recently viewed

    def viewed(self, uid: str, paper: str, project: str | None) -> None:
        with self._lock, self.conn:
            # out and in again: a new rowid, so two views in the same second keep their order
            self.conn.execute("DELETE FROM recent WHERE user = ? AND paper = ?", (uid, paper))
            self.conn.execute("INSERT INTO recent(user, paper, project, viewed_at) VALUES (?, ?, ?, ?)", (uid, paper, project, now_iso()))
            # the newest RECENT_KEEP are kept; older views fall off the end
            self.conn.execute("DELETE FROM recent WHERE user = ? AND paper NOT IN (SELECT paper FROM recent WHERE user = ? ORDER BY viewed_at DESC, rowid DESC LIMIT ?)",
                              (uid, uid, RECENT_KEEP))

    def recent(self, uid: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT paper, project, viewed_at FROM recent WHERE user = ? ORDER BY viewed_at DESC, rowid DESC LIMIT ?", (uid, int(limit)))]

    # ---------------------------------------------------------------- favourites

    def favourite(self, uid: str, paper: str, on: bool, project: str | None = None) -> bool:
        with self._lock, self.conn:
            if on:
                self.conn.execute("INSERT OR IGNORE INTO favourites(user, paper, project, added_at) VALUES (?, ?, ?, ?)", (uid, paper, project, now_iso()))
            else:
                self.conn.execute("DELETE FROM favourites WHERE user = ? AND paper = ?", (uid, paper))
        return on

    def favourites(self, uid: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT paper, project, added_at FROM favourites WHERE user = ? ORDER BY added_at DESC, rowid DESC", (uid,))]

    def is_favourite(self, uid: str, paper: str) -> bool:
        with self._lock:
            return self.conn.execute("SELECT 1 FROM favourites WHERE user = ? AND paper = ?", (uid, paper)).fetchone() is not None

    # ---------------------------------------------------------------- notes

    def notes(self, uid: str, paper: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, paper, node_id, quote, text, created_at, updated_at FROM notes WHERE user = ?"
        args: list[Any] = [uid]
        if paper is not None:
            sql += " AND paper = ?"
            args.append(paper)
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql + " ORDER BY created_at, id", args)]

    def save_note(self, uid: str, paper: str, text: str, *, note_id: int | None = None, node_id: str | None = None, quote: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise AccountError("a note cannot be empty")
        now = now_iso()
        with self._lock, self.conn:
            if note_id is not None:
                cur = self.conn.execute("UPDATE notes SET text = ?, updated_at = ? WHERE id = ? AND user = ?", (text, now, int(note_id), uid))
                if not cur.rowcount:
                    raise AccountError("no such note of yours")
                nid = int(note_id)
            else:
                nid = self.conn.execute("INSERT INTO notes(user, paper, node_id, quote, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        (uid, paper, node_id or None, (quote or "")[:200] or None, text, now, now)).lastrowid
            row = self.conn.execute("SELECT id, paper, node_id, quote, text, created_at, updated_at FROM notes WHERE id = ?", (nid,)).fetchone()
        return dict(row)

    def delete_note(self, uid: str, note_id: int) -> bool:
        with self._lock, self.conn:
            return bool(self.conn.execute("DELETE FROM notes WHERE id = ? AND user = ?", (int(note_id), uid)).rowcount)

    # ---------------------------------------------------------------- collections

    def collections(self, uid: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT c.id, c.name, c.created_at, COUNT(cp.paper) AS papers FROM collections c LEFT JOIN collection_papers cp ON cp.collection = c.id "
                "WHERE c.user = ? GROUP BY c.id ORDER BY c.name COLLATE NOCASE", (uid,))]

    def _own(self, uid: str, cid: int) -> None:
        if self.conn.execute("SELECT 1 FROM collections WHERE id = ? AND user = ?", (int(cid), uid)).fetchone() is None:
            raise AccountError("no such collection of yours")

    def create_collection(self, uid: str, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise AccountError("a collection needs a name")
        with self._lock, self.conn:
            try:
                cid = self.conn.execute("INSERT INTO collections(user, name, created_at) VALUES (?, ?, ?)", (uid, name, now_iso())).lastrowid
            except sqlite3.IntegrityError:
                raise AccountError(f"you have a collection called {name!r} already") from None
        return {"id": cid, "name": name, "papers": 0}

    def rename_collection(self, uid: str, cid: int, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise AccountError("a collection needs a name")
        with self._lock, self.conn:
            self._own(uid, cid)
            try:
                self.conn.execute("UPDATE collections SET name = ? WHERE id = ?", (name, int(cid)))
            except sqlite3.IntegrityError:
                raise AccountError(f"you have a collection called {name!r} already") from None

    def delete_collection(self, uid: str, cid: int) -> None:
        """The folder goes; the papers in it stay wherever else they are."""
        with self._lock, self.conn:
            self._own(uid, cid)
            self.conn.execute("DELETE FROM collections WHERE id = ?", (int(cid),))

    def add_to_collection(self, uid: str, cid: int, papers: list[str], project: str | None = None) -> int:
        with self._lock, self.conn:
            self._own(uid, cid)
            now = now_iso()
            return sum(self.conn.execute("INSERT OR IGNORE INTO collection_papers(collection, paper, project, added_at) VALUES (?, ?, ?, ?)",
                                         (int(cid), p, project, now)).rowcount for p in papers)

    def remove_from_collection(self, uid: str, cid: int, papers: list[str]) -> int:
        with self._lock, self.conn:
            self._own(uid, cid)
            return sum(self.conn.execute("DELETE FROM collection_papers WHERE collection = ? AND paper = ?", (int(cid), p)).rowcount for p in papers)

    def collection_papers(self, uid: str, cid: int) -> list[dict[str, Any]]:
        with self._lock:
            self._own(uid, cid)
            return [dict(r) for r in self.conn.execute("SELECT paper, project, added_at FROM collection_papers WHERE collection = ? ORDER BY added_at DESC, rowid DESC", (int(cid),))]

    def collections_of(self, uid: str, paper: str) -> list[int]:
        with self._lock:
            return [r[0] for r in self.conn.execute("SELECT c.id FROM collections c JOIN collection_papers cp ON cp.collection = c.id WHERE c.user = ? AND cp.paper = ?", (uid, paper))]


_DUMMY: list[str] = []


def _dummy() -> str:
    """A hash checked against when a name has no account, so that a wrong name takes as long as a
    wrong password. Made on first need, not at import."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    return _DUMMY[0]


class Sessions:
    """Who is signed in, by the token `sign_in` handed out: kept in the worker's memory only, so a
    restart signs everyone out."""

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._lock = threading.Lock()

    def open(self, uid: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._by_token[token] = uid
        return token

    def close(self, token: str) -> None:
        with self._lock:
            self._by_token.pop(token, None)

    def user(self, token: Any) -> str:
        with self._lock:
            uid = self._by_token.get(str(token or ""))
        if uid is None:
            raise AccountError("not signed in: sign in again")
        return uid
