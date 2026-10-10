# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A model of the sync state design on SQLite, used to check its rules before any library
code exists. Not part of the library.

Each section of output names the section of the design page (docs/explanation/design.rst) it
checks. Run it with no arguments; it exits 1 if any check fails.
"""

import hashlib
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta

SYNCED, PENDING, IN_FLIGHT, CONFLICT, REMOVED = (
    "synced",
    "pending",
    "in flight",
    "conflict",
    "removed",
)
UNCHANGED, REMOTE_ONLY, LOCAL_ONLY = "UNCHANGED", "REMOTE_ONLY", "LOCAL_ONLY"
DELETED_REMOTE, DELETED_LOCAL, CONFLICT_C = "DELETED_REMOTE", "DELETED_LOCAL", "CONFLICT"
BOTH_MODIFIED, REMOTE_REMOVED_LOCAL_EDIT = "both modified", "remote removed against local edit"
LOCAL_DELETE_REMOTE_EDIT, BOTH_CREATED, CREATE_COLLISION = (
    "local delete against remote edit",
    "both created",
    "create collision",
)

SCHEMA = """
create table if not exists scope (
  name text primary key, token text, last_synced text, last_listing text);
create table if not exists obj (
  key integer primary key, scope text, oid text, version text, content text, synced_fp text,
  lkey text, synced_at text,
  edit_kind text, edit_content text, edit_base text,
  in_flight integer not null default 0, outcome text,
  conflict_kind text, remote_content text, remote_version text,
  remote_removed integer not null default 0,
  collides_with text,
  unique(scope, oid));
"""


def fp(text):
    return None if text is None else hashlib.sha256(text.encode()).hexdigest()[:12]


class StaleChangeSet(Exception):
    pass


class SessionOrder(Exception):
    pass


class ClaimLost(Exception):
    pass


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 10, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t.isoformat()


class Replica:
    def __init__(self, path, clock):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.executescript(SCHEMA)
        self.clock = clock
        self.sessions = {}  # sessions are per scope
        self.listing_seen = {}
        # an in-flight edit with no outcome after reopen is unknown
        self.db.execute("update obj set outcome='unknown' where in_flight=1 and outcome is null")

    # helpers
    def find(self, key=None, scope=None, oid=None):
        """The record with this local key, or with this object id in the scope; None if none."""
        cur = self.db.execute(
            "select * from obj where key=?" if key else "select * from obj where scope=? and oid=?",
            (key,) if key else (scope, oid),
        )
        r = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], r, strict=True)) if r else None

    def row(self, key=None, scope=None, oid=None):
        """The record find() returns, for callers that know it exists."""
        found = self.find(key, scope, oid)
        if found is None:
            raise LookupError(f"no record for key={key} scope={scope} oid={oid}")
        return found

    def state_of(self, key):
        r = self.find(key)
        if r is None:
            return REMOVED
        if r["conflict_kind"]:
            return CONFLICT
        if r["edit_kind"]:
            return IN_FLIGHT if r["in_flight"] else PENDING
        return SYNCED

    def set(self, key, **kw):
        self.db.execute(
            f"update obj set {', '.join(f'{k}=?' for k in kw)} where key=?", (*kw.values(), key)
        )

    def token(self, scope):
        r = self.db.execute("select token from scope where name=?", (scope,)).fetchone()
        return r[0] if r else None

    def lookup(self, scope, lkey):  # every match, not the first
        return [
            k
            for (k,) in self.db.execute(
                "select key from obj where scope=? and lkey=?", (scope, lkey)
            )
        ]

    def _synced(self, key, version, content):
        self.set(
            key,
            version=version,
            content=content,
            synced_fp=fp(content),
            synced_at=self.clock(),
            edit_kind=None,
            edit_content=None,
            edit_base=None,
            in_flight=0,
            outcome=None,
            conflict_kind=None,
            remote_content=None,
            remote_version=None,
            remote_removed=0,
            collides_with=None,
        )

    # receiving: a change set is applied all at once
    def ingest(self, scope, cs):
        if self.sessions.get(scope) in ("pushing", "done"):
            raise SessionOrder("ingest after push")
        out = {}
        self.db.execute("begin immediate")
        try:
            self.db.execute("insert or ignore into scope(name) values (?)", (scope,))
            if self.token(scope) != cs["against"]:
                raise StaleChangeSet(
                    f"fetched against {cs['against']}, store has {self.token(scope)}"
                )
            for oid, ch in cs["changes"].items():
                out[oid] = self._apply(scope, oid, ch)
            final = cs["complete"]
            if cs["kind"] == "listing":
                seen = self.listing_seen.setdefault(scope, set())
                seen.update(cs["changes"])
                if final:
                    for (oid,) in self.db.execute(
                        "select oid from obj where scope=? and synced_fp is not null", (scope,)
                    ).fetchall():
                        if oid not in seen:
                            out[oid] = self._apply(scope, oid, ("removed",))
                    self.listing_seen.pop(scope)
                    self.db.execute(
                        "update scope set last_listing=? where name=?", (self.clock(), scope)
                    )
            if final:
                for key, oid in self.db.execute(
                    "select key, oid from obj where scope=? and outcome='unknown'", (scope,)
                ).fetchall():
                    if oid is None or oid not in cs["changes"]:
                        self.set(key, in_flight=0, outcome=None)
                        out[f"local:{key}"] = "unmentioned in-flight edit: pending again"
                self.db.execute(
                    "update scope set last_synced=? where name=?", (self.clock(), scope)
                )
            if cs["kind"] == "incremental" or final:
                self.db.execute("update scope set token=? where name=?", (cs["new"], scope))
            self.db.execute("commit")
        except Exception:
            self.db.execute("rollback")
            raise
        self.sessions[scope] = "ingested" if final else "ingesting"
        return out

    def _apply(self, scope, oid, ch):
        r = self.find(scope=scope, oid=oid)
        if r is None and ch[0] == "changed" and ch[3]:
            for key in self.lookup(scope, ch[3]):
                c = self.row(key)
                if c["oid"] is None and c["edit_kind"] == "create" and c["outcome"] == "unknown":
                    self.set(key, oid=oid)
                    self._synced(key, ch[1], ch[2])
                    return REMOTE_ONLY + " (own create recovered by logical key)"
        if ch[0] == "removed":
            if r is None:
                return UNCHANGED + " (unknown id removed: no-op)"
            if r["conflict_kind"]:
                self.set(r["key"], remote_removed=1, remote_content=None)
                return CONFLICT_C + " (refreshed)"
            if r["edit_kind"] == "delete":
                self.db.execute("delete from obj where key=?", (r["key"],))
                return DELETED_REMOTE + " (both deleted)"
            if r["edit_kind"] == "update":
                self.set(
                    r["key"],
                    conflict_kind=REMOTE_REMOVED_LOCAL_EDIT,
                    remote_removed=1,
                    in_flight=0,
                    outcome=None,
                )
                return CONFLICT_C
            self.db.execute("delete from obj where key=?", (r["key"],))
            return DELETED_REMOTE
        if ch[0] == "unchanged":
            return UNCHANGED
        _, version, content, lkey = ch
        if r is None:
            self.db.execute(
                "insert into obj(scope, oid, version, content, synced_fp, lkey, synced_at) "
                "values (?,?,?,?,?,?,?)",
                (scope, oid, version, content, fp(content), lkey, self.clock()),
            )
            return REMOTE_ONLY
        if r["conflict_kind"]:
            if r["edit_content"] is not None and fp(content) == fp(r["edit_content"]):
                self._synced(r["key"], version, content)
                return UNCHANGED + " (conflict resolved itself)"
            self.set(r["key"], remote_content=content, remote_version=version, remote_removed=0)
            return CONFLICT_C + " (refreshed)"
        if r["edit_kind"] == "create" and r["synced_fp"] is None:
            if (
                r["outcome"] == "unknown"
            ):  # own create by reserved id; servers rewrite, so no compare
                self._synced(r["key"], version, content)
                return REMOTE_ONLY + " (own create recovered by reserved id)"
            self.set(
                r["key"], conflict_kind=BOTH_CREATED, remote_content=content, remote_version=version
            )
            return CONFLICT_C
        if r["edit_kind"] == "update" and r["outcome"] == "unknown":
            if fp(content) == fp(r["edit_content"]):
                self._synced(r["key"], version, content)
                return UNCHANGED + " (own update landed)"
            self.set(
                r["key"],
                conflict_kind=BOTH_MODIFIED,
                remote_content=content,
                remote_version=version,
                in_flight=0,
                outcome=None,
            )
            return CONFLICT_C
        if r["synced_fp"] == fp(content):
            self.set(r["key"], version=version)
            return UNCHANGED + " (version-only)"
        if r["edit_kind"] in ("update", "delete"):
            if r["edit_kind"] == "update" and fp(r["edit_content"]) == fp(content):
                self._synced(r["key"], version, content)
                return UNCHANGED + " (both sides equal)"
            kind = BOTH_MODIFIED if r["edit_kind"] == "update" else LOCAL_DELETE_REMOTE_EDIT
            self.set(
                r["key"],
                conflict_kind=kind,
                remote_content=content,
                remote_version=version,
                in_flight=0,
                outcome=None,
            )
            return CONFLICT_C
        self._synced(r["key"], version, content)
        return REMOTE_ONLY

    # local edits are recorded, not discovered
    def record(self, scope, kind, key=None, content=None, oid=None, lkey=None):
        if kind == "create":
            return self.db.execute(
                "insert into obj(scope, oid, lkey, edit_kind, edit_content) values (?,?,?,?,?)",
                (scope, oid, lkey, "create", content),
            ).lastrowid
        r = self.row(key)
        if r["conflict_kind"]:
            raise SessionOrder("edit refused: open conflict")
        if r["edit_kind"] == "create" and kind == "delete":
            self.db.execute("delete from obj where key=?", (key,))
            return None
        if kind == "update" and fp(content) == r["synced_fp"]:
            self.set(key, edit_kind=None, edit_content=None, edit_base=None)
            return key
        self.set(
            key,
            edit_kind="create" if r["edit_kind"] == "create" else kind,
            edit_content=content,
            edit_base=r["version"],
        )
        return key

    def local_class(self, key):
        r = self.row(key)
        if r["conflict_kind"]:
            return CONFLICT_C
        return {"update": LOCAL_ONLY, "create": LOCAL_ONLY, "delete": DELETED_LOCAL}.get(
            r["edit_kind"], UNCHANGED
        )

    # pushing: a sync reads before it writes
    def claim(self, scope, key):
        if self.sessions.get(scope) not in ("ingested", "pushing"):
            raise SessionOrder(f"push refused in session state {self.sessions.get(scope)!r}")
        if (
            self.db.execute(
                "update obj set in_flight=1 where key=? and in_flight=0 and edit_kind is not null",
                (key,),
            ).rowcount
            != 1
        ):
            raise ClaimLost(f"key {key} already in flight")
        self.sessions[scope] = "pushing"

    def outcome(self, key, result, oid=None, collides_with=None):
        r = self.row(key)
        if result == "applied":
            if r["edit_kind"] == "delete":
                self.db.execute("delete from obj where key=?", (key,))
                return
            self.set(
                key,
                oid=oid or r["oid"],
                version=None,
                content=r["edit_content"],
                synced_fp=fp(r["edit_content"]),
                synced_at=self.clock(),
                edit_kind=None,
                edit_content=None,
                edit_base=None,
                in_flight=0,
                outcome=None,
            )
        elif result == "rejected":
            self.set(key, in_flight=0, outcome=None)
        elif result == "gone":
            if r["edit_kind"] == "delete":
                self.db.execute("delete from obj where key=?", (key,))
            else:
                self.set(
                    key, in_flight=0, conflict_kind=REMOTE_REMOVED_LOCAL_EDIT, remote_removed=1
                )
        elif result == "collision":
            self.set(key, in_flight=0, conflict_kind=CREATE_COLLISION, collides_with=collides_with)
        elif result == "unknown":
            self.set(key, outcome="unknown")

    def end_session(self, scope):
        self.sessions[scope] = "idle"

    # conflicts are records
    def resolve(self, key, how, content=None):
        r = self.row(key)
        self.db.execute("begin immediate")
        try:
            if r["conflict_kind"] == CREATE_COLLISION:
                if how == "discard":
                    self.db.execute("delete from obj where key=?", (key,))
                elif how == "update colliding":
                    target = self.find(scope=r["scope"], oid=r["collides_with"])
                    if target is None:
                        raise SessionOrder("colliding object not known yet")
                    self.set(
                        target["key"],
                        edit_kind="update",
                        edit_content=r["edit_content"],
                        edit_base=target["version"],
                    )
                    self.db.execute("delete from obj where key=?", (key,))
                elif how == "merged":
                    self.set(
                        key, conflict_kind=None, collides_with=None, edit_content=content, lkey=None
                    )
                else:
                    raise SessionOrder(f"{how!r} isn't offered for a create collision")
            elif how == "keep remote":
                if r["remote_removed"]:
                    self.db.execute("delete from obj where key=?", (key,))
                else:
                    self._synced(key, r["remote_version"], r["remote_content"])
            else:
                kind = (
                    "delete" if how == "delete" else ("create" if r["remote_removed"] else "update")
                )
                self.set(
                    key,
                    conflict_kind=None,
                    edit_kind=kind,
                    edit_base=r["remote_version"],
                    edit_content=None
                    if how == "delete"
                    else (content if how == "merged" else r["edit_content"]),
                    remote_content=None,
                    remote_removed=0,
                )
            self.db.execute("commit")
        except Exception:
            self.db.execute("rollback")
            raise

    def resolve_conflicts(self, resolver):
        for (key,) in self.db.execute(
            "select key from obj where conflict_kind is not null"
        ).fetchall():
            choice = resolver(self.row(key))
            if choice is not None:
                self.resolve(key, *choice)

    def drop_scope(self, scope):
        discarded = self.db.execute(
            "select key, edit_kind, conflict_kind from obj where scope=? and "
            "(edit_kind is not null or conflict_kind is not null)",
            (scope,),
        ).fetchall()
        self.db.execute("begin immediate")
        self.db.execute("delete from obj where scope=?", (scope,))
        self.db.execute("delete from scope where name=?", (scope,))
        self.db.execute("commit")
        return discarded


# ---------------------------------------------------------------- cases
def cs(against, new, changes=None, kind="incremental", complete=True):
    """A change set fetched against one token and leading to another; changes maps id to change."""
    return {
        "against": against,
        "new": new,
        "kind": kind,
        "complete": complete,
        "changes": changes or {},
    }


def ch(version, content, lkey=None):
    return ("changed", version, content, lkey)


RM, UNCH = ("removed",), ("unchanged",)
TMP = tempfile.mkdtemp()
results = []


def check(label, cond):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + label)


def fresh(name):
    path = os.path.join(TMP, name + ".db")
    r = Replica(path, Clock())
    r.ingest(
        "s",
        cs(None, "t1", {"a": ch("v1", "A", "uid-a"), "b": ch("v1", "B", "uid-b")}, kind="listing"),
    )
    r.end_session("s")
    return r, path


def k(r, oid):
    return r.row(scope="s", oid=oid)["key"]


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


print("== Why conflicts are reported, not resolved: every class")
r, _ = fresh("cc")
check(
    "UNCHANGED version-only",
    r.ingest("s", cs("t1", "t2", {"a": ch("v2", "A")}))["a"].startswith(UNCHANGED),
)
r.end_session("s")
check(
    "REMOTE_ONLY update", r.ingest("s", cs("t2", "t3", {"a": ch("v3", "A2")}))["a"] == REMOTE_ONLY
)
r.end_session("s")
check("REMOTE_ONLY create", r.ingest("s", cs("t3", "t4", {"c": ch("v1", "C")}))["c"] == REMOTE_ONLY)
r.end_session("s")
check("DELETED_REMOTE", r.ingest("s", cs("t4", "t5", {"c": RM}))["c"] == DELETED_REMOTE)
r.end_session("s")
kb = k(r, "b")
r.record("s", "update", kb, "B2")
check("LOCAL_ONLY", r.local_class(kb) == LOCAL_ONLY)
ka = k(r, "a")
r.record("s", "delete", ka)
check("DELETED_LOCAL", r.local_class(ka) == DELETED_LOCAL)
check(
    "CONFLICT both modified",
    r.ingest("s", cs("t5", "t6", {"b": ch("v2", "Bx")}))["b"] == CONFLICT_C
    and r.row(kb)["conflict_kind"] == BOTH_MODIFIED,
)
r.end_session("s")
check(
    "CONFLICT local delete against remote edit",
    r.ingest("s", cs("t6", "t7", {"a": ch("v4", "Ax")}))["a"] == CONFLICT_C
    and r.row(ka)["conflict_kind"] == LOCAL_DELETE_REMOTE_EDIT,
)
r.end_session("s")
r, _ = fresh("cc2")
ka = k(r, "a")
r.record("s", "update", ka, "A-mine")
check(
    "CONFLICT remote removed against local edit",
    r.ingest("s", cs("t1", "t2", {"a": RM}))["a"] == CONFLICT_C
    and r.row(ka)["conflict_kind"] == REMOTE_REMOVED_LOCAL_EDIT,
)
r.end_session("s")
kb = k(r, "b")
r.record("s", "delete", kb)
check(
    "DELETED_REMOTE both deleted",
    r.ingest("s", cs("t2", "t3", {"b": RM}))["b"] == DELETED_REMOTE + " (both deleted)",
)
r.end_session("s")
r, _ = fresh("cc3")
kb = k(r, "b")
r.record("s", "update", kb, "SAME")
check(
    "UNCHANGED both sides equal",
    r.ingest("s", cs("t1", "t2", {"b": ch("v2", "SAME")}))["b"]
    == UNCHANGED + " (both sides equal)",
)
r.end_session("s")
kn = r.record("s", "create", content="MINE", oid="n.ics", lkey="uid-n")
check(
    "CONFLICT both created at a reserved id before sending",
    r.ingest("s", cs("t2", "t3", {"n.ics": ch("v1", "THEIRS")}))["n.ics"] == CONFLICT_C
    and r.row(kn)["conflict_kind"] == BOTH_CREATED,
)
r.end_session("s")

print("== Why a sync reads before it writes: sessions")
r, _ = fresh("sess")
kb = k(r, "b")
r.record("s", "update", kb, "B2")
r.ingest("s", cs("t1", "t2", {"a": ch("v2", "A2")}, complete=False))
check("push refused while paging", raises(SessionOrder, lambda: r.claim("s", kb)))
r.ingest("s", cs("t2", "t3"))
r.claim("s", kb)
check("ingest after push refused", raises(SessionOrder, lambda: r.ingest("s", cs("t3", "t4"))))
r.ingest("other", cs(None, "o1", {"x": ch("v1", "X")}, kind="listing"))
check("another scope ingests independently while this one pushes", r.token("other") == "o1")
r.outcome(kb, "applied")
r.end_session("s")
check(
    "applied push: version cleared, synced",
    r.row(kb)["version"] is None and r.state_of(kb) == SYNCED,
)
check(
    "own write comes back rewritten: stored, no conflict",
    r.ingest("s", cs("t3", "t4", {"b": ch("v9", "B2 rewritten")}))["b"] == REMOTE_ONLY,
)
r.end_session("s")

print("== Why a sync reads before it writes: push claims and a crash with an edit in flight")
r1, path = fresh("claim")
r2 = Replica(path, Clock())
ka = k(r1, "a")
r1.record("s", "update", ka, "A2")
r1.ingest("s", cs("t1", "t2"))
r2.ingest("s", cs("t2", "t3"))
r1.claim("s", ka)
check(
    "second process loses the claim on the same edit", raises(ClaimLost, lambda: r2.claim("s", ka))
)
del r1  # crash: request may or may not have gone out
r3 = Replica(path, Clock())
check(
    "after reopen the in-flight edit is unknown",
    r3.row(ka)["outcome"] == "unknown" and r3.state_of(ka) == IN_FLIGHT,
)
r3.ingest("s", cs("t3", "t4"))
check("unknown edit never mentioned: pending again", r3.state_of(ka) == PENDING)
r3.end_session("s")
r4 = Replica(path, Clock())
r4.ingest("s", cs("t4", "t5"))
r4.claim("s", ka)
r4.outcome(ka, "unknown")
r4.end_session("s")
r5 = Replica(path, Clock())
check(
    "unknown edit that landed is settled by the next ingest",
    r5.ingest("s", cs("t5", "t6", {"a": ch("v5", "A2")}))["a"]
    == UNCHANGED + " (own update landed)",
)
r5.end_session("s")

print("== Why a sync reads before it writes: unknown outcomes")
r, _ = fresh("inflight")
kb = k(r, "b")
r.record("s", "update", kb, "B3")
r.ingest("s", cs("t1", "t2"))
r.claim("s", kb)
r.outcome(kb, "unknown")
r.end_session("s")
check(
    "in-flight update back different: both modified",
    r.ingest("s", cs("t2", "t3", {"b": ch("v3", "B3 rewritten")}))["b"] == CONFLICT_C,
)
r.end_session("s")
r2, _ = fresh("inflight2")
ka = k(r2, "a")
r2.record("s", "delete", ka)
r2.ingest("s", cs("t1", "t2"))
r2.claim("s", ka)
r2.outcome(ka, "unknown")
r2.end_session("s")
check(
    "in-flight delete back removed: applied",
    r2.ingest("s", cs("t2", "t3", {"a": RM}))["a"] == DELETED_REMOTE + " (both deleted)",
)
r2.end_session("s")
kc = r2.record("s", "create", content="NEW", lkey="uid-n")
r2.ingest("s", cs("t3", "t4"))
r2.claim("s", kc)
r2.outcome(kc, "unknown")
r2.end_session("s")
check(
    "in-flight create recovered by logical key despite rewrite",
    r2.ingest("s", cs("t4", "t5", {"srv-1": ch(None, "NEW+props", "uid-n")}))["srv-1"].startswith(
        REMOTE_ONLY
    )
    and r2.state_of(kc) == SYNCED,
)
r2.end_session("s")
kr = r2.record("s", "create", content="R", oid="r.ics")
r2.ingest("s", cs("t5", "t6"))
r2.claim("s", kr)
r2.outcome(kr, "unknown")
r2.end_session("s")
check(
    "in-flight create recovered by reserved id despite rewrite",
    r2.ingest("s", cs("t6", "t7", {"r.ics": ch("v1", "R rewritten")}))["r.ics"].startswith(
        REMOTE_ONLY
    )
    and r2.state_of(kr) == SYNCED,
)
r2.end_session("s")
ke = r2.record("s", "create", content="X", oid="x.ics")
check(
    "create then delete before sending removes it",
    r2.record("s", "delete", ke) is None and r2.find(ke) is None,
)
kb2 = k(r2, "b")
r2.record("s", "update", kb2, "tmp")
r2.record("s", "update", kb2, "B")
check("edit back to synced content cancels", r2.state_of(kb2) == SYNCED)

print("== Why the server's answer to a write isn't trusted: rejections")
r, _ = fresh("rej")
ka = k(r, "a")
r.record("s", "update", ka, "A2")
r.ingest("s", cs("t1", "t2"))
r.claim("s", ka)
r.outcome(ka, "rejected")
r.end_session("s")
r.ingest("s", cs("t2", "t3", {"b": ch("v2", "B2")}))
check("scope-wide rejection, object unchanged: pending, no conflict", r.state_of(ka) == PENDING)
r.claim("s", ka)
r.outcome(ka, "applied")
r.end_session("s")
check("pushed again and applied", r.state_of(ka) == SYNCED)
r.record("s", "update", ka, "A3")
r.ingest("s", cs("t3", "t4"))
r.claim("s", ka)
r.outcome(ka, "rejected")
r.end_session("s")
check(
    "rejection with the object changed remotely: both modified",
    r.ingest("s", cs("t4", "t5", {"a": ch("v5", "Ar")}))["a"] == CONFLICT_C,
)
r.end_session("s")

print("== Why conflicts are reported, not resolved: refresh and resolve_conflicts")
check(
    "edit refused on open conflict",
    raises(SessionOrder, lambda: r.record("s", "update", ka, "nope")),
)
check(
    "conflict refreshes its remote side",
    r.ingest("s", cs("t5", "t6", {"a": ch("v6", "Ar2")}))["a"] == CONFLICT_C + " (refreshed)"
    and r.row(ka)["remote_content"] == "Ar2",
)
r.end_session("s")
check(
    "conflict resolves itself when remote equals local",
    r.ingest("s", cs("t6", "t7", {"a": ch("v7", "A3")}))["a"]
    == UNCHANGED + " (conflict resolved itself)",
)
r.end_session("s")
r, _ = fresh("res")
ka, kb = k(r, "a"), k(r, "b")
r.record("s", "update", ka, "A-mine")
r.record("s", "update", kb, "B-mine")
r.ingest("s", cs("t1", "t2", {"a": ch("v2", "A-theirs"), "b": ch("v2", "B-theirs")}))
r.end_session("s")


def resolver(rec):
    if rec["oid"] == "a":
        return ("merged", "A-merged")
    raise RuntimeError("resolver failed")


check("resolver exception propagates", raises(RuntimeError, lambda: r.resolve_conflicts(resolver)))
check(
    "earlier resolution kept, based on the remote version",
    r.row(ka)["edit_content"] == "A-merged"
    and r.row(ka)["edit_base"] == "v2"
    and r.row(ka)["conflict_kind"] is None,
)
check("record that raised stays open", r.row(kb)["conflict_kind"] == BOTH_MODIFIED)
r.resolve_conflicts(lambda rec: None)
check("None leaves a record open", r.row(kb)["conflict_kind"] == BOTH_MODIFIED)
r.resolve_conflicts(lambda rec: ("keep remote",))
check(
    "keep remote marks synced with the remote content",
    r.state_of(kb) == SYNCED and r.row(kb)["content"] == "B-theirs",
)
check(
    "resolution then another remote change: classified against the new base",
    r.ingest("s", cs("t2", "t3", {"a": ch("v3", "A-theirs-2")}))["a"] == CONFLICT_C,
)
r.end_session("s")

print("== Create collisions, and why identity isn't the calendar UID")
for how, expect in (
    ("discard", "local create gone"),
    ("update colliding", "colliding object gets the edit"),
    ("merged", "fresh create with new content"),
):
    r, _ = fresh("coll-" + how.replace(" ", "-"))
    kc = r.record("s", "create", content="MINE uid-a", oid="new.ics", lkey="uid-a")
    r.ingest("s", cs("t1", "t2"))
    r.claim("s", kc)
    r.outcome(kc, "collision", collides_with="a")
    r.end_session("s")
    if how == "discard":
        r.resolve(kc, "discard")
        ok = r.find(kc) is None
    elif how == "update colliding":
        r.resolve(kc, "update colliding")
        ok = (
            r.find(kc) is None
            and r.row(k(r, "a"))["edit_kind"] == "update"
            and r.row(k(r, "a"))["edit_content"] == "MINE uid-a"
        )
    else:
        r.resolve(kc, "merged", "MINE with uid-z")
        ok = (
            r.state_of(kc) == PENDING
            and r.row(kc)["edit_kind"] == "create"
            and r.row(kc)["edit_content"] == "MINE with uid-z"
        )
    check(f"create collision resolved by {how}: {expect}", ok)
r, _ = fresh("coll-keep")
kc = r.record("s", "create", content="M", oid="n2.ics", lkey="uid-a")
r.ingest("s", cs("t1", "t2"))
r.claim("s", kc)
r.outcome(kc, "collision", collides_with="a")
r.end_session("s")
check(
    "keep local isn't offered for a create collision",
    raises(SessionOrder, lambda: r.resolve(kc, "keep local")),
)
r, _ = fresh("dupuid")
r.ingest("s", cs("t1", "t2", {"dup.ics": ch("v1", "second object, same UID", "uid-a")}))
r.end_session("s")
check("logical-key lookup returns every match", len(r.lookup("s", "uid-a")) == 2)

print("== Why a change set is applied all at once, and why an invalid token is routine")
r, _ = fresh("list")
page1 = r.ingest("s", cs("t1", "t9", {"a": UNCH, "zz": RM}, kind="listing", complete=False))
check("listing page leaves the token alone", r.token("s") == "t1")
check(
    "removal of an unknown id inside a listing is a no-op",
    page1["zz"] == UNCHANGED + " (unknown id removed: no-op)",
)
out = r.ingest("s", cs("t1", "t9", kind="listing", complete=True))
check(
    "final page infers the unmentioned removal",
    out.get("b") == DELETED_REMOTE and r.token("s") == "t9",
)
r.end_session("s")
r, _ = fresh("loss")
r.ingest("s", cs("t1", "t2", {"c": ch("v1", "C")}))
r.end_session("s")  # a lossy incremental: b's removal and a's edit missing
out = r.ingest("s", cs("t2", "t3", {"a": ch("v2", "A edited silently"), "c": UNCH}, kind="listing"))
check("reconciling listing repairs a lost removal", out.get("b") == DELETED_REMOTE)
check("reconciling listing repairs a lost edit", out.get("a") == REMOTE_ONLY)
check(
    "last listing time recorded",
    r.db.execute("select last_listing from scope where name='s'").fetchone()[0] is not None,
)
r.end_session("s")
r, _ = fresh("stale")
check(
    "stale change set refused, token unchanged",
    raises(StaleChangeSet, lambda: r.ingest("s", cs("t0", "t2"))) and r.token("s") == "t1",
)

print("== Timestamps, why a removed scope is yours to drop, and two processes on one store")
r, path = fresh("misc")
t_scope = r.db.execute("select last_synced from scope").fetchone()[0]
check(
    "timestamps come from the injected clock",
    t_scope.startswith("2026-10-10") and r.row(k(r, "a"))["synced_at"].startswith("2026-10-10"),
)
r.ingest("s", cs("t1", "t2", complete=False))
check(
    "a truncated page leaves last_synced alone",
    r.db.execute("select last_synced from scope").fetchone()[0] == t_scope,
)
r.record("s", "update", k(r, "a"), "A-mine")
dropped = r.drop_scope("s")
check(
    "drop_scope reports the pending edit it discards",
    len(dropped) == 1 and dropped[0][1] == "update",
)
r1, path = fresh("race")
r2 = Replica(path, Clock())
r1.ingest("s", cs("t1", "t2", {"a": ch("v2", "A2")}))
check(
    "second handle with the old token is refused",
    raises(StaleChangeSet, lambda: r2.ingest("s", cs("t1", "t2b", {"a": ch("v9", "other")}))),
)
check(
    "store holds the first writer's result only",
    r2.row(scope="s", oid="a")["content"] == "A2" and r2.token("s") == "t2",
)

print("== Why an object has five states")
r, _ = fresh("states")
ka = k(r, "a")
seen = {r.state_of(ka)}
r.record("s", "update", ka, "A2")
seen.add(r.state_of(ka))
r.ingest("s", cs("t1", "t2"))
r.claim("s", ka)
seen.add(r.state_of(ka))
r.outcome(ka, "rejected")
r.end_session("s")
r.ingest("s", cs("t2", "t3", {"a": ch("v2", "Ar")}))
seen.add(r.state_of(ka))
r.end_session("s")
r.resolve(ka, "keep remote")
r.ingest("s", cs("t3", "t4", {"a": RM}))
seen.add(r.state_of(ka))
check(
    "synced, pending, in flight, conflict and removed all reached",
    seen == {SYNCED, PENDING, IN_FLIGHT, CONFLICT, REMOVED},
)

print(f"\n{sum(results)} of {len(results)} passed")
raise SystemExit(0 if all(results) else 1)
