# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP CalendarEvent sync behavior over raw JMAP.

Usage: jmap_sync.py SERVER BASE_URL USER PASSWORD NEEDS_VERSION(yes|no)

Each numbered section is one row of the server compatibility page.
"""

import json
import sys

from _common import JMAP, JMAP_CALENDARS, JMAP_TASKS, check, finish, result

SERVER, BASE, USER, PASSWORD, NEEDS_VERSION = sys.argv[1:6]
jmap = JMAP(BASE, USER, PASSWORD, NEEDS_VERSION == "yes")

# 1. capabilities
caps = jmap.session["capabilities"]
result(
    SERVER, "1 capabilities", f"calendars: {JMAP_CALENDARS in caps}, tasks: {JMAP_TASKS in caps}"
)

# 2. paging with maxChanges 1 (RFC 8620 section 5.2)
s0 = jmap.state()
ids = jmap.create("2 create a, b, c", {k: (f"probe-{k}", k) for k in "abc"})
_, out = jmap.call(
    "2 update a, destroy b, create d",
    "CalendarEvent/set",
    {
        "update": {ids["a"]: {"title": "a v2"}},
        "destroy": [ids["b"]],
        "create": {"d": jmap.event("probe-d", "d")},
    },
)
tok, pages, echoed, paged = s0, 0, True, {"created": set(), "updated": set(), "destroyed": set()}
while pages < 12:
    _, out = jmap.call(
        f"2 page {pages}", "CalendarEvent/changes", {"sinceState": tok, "maxChanges": 1}
    )
    pages += 1
    echoed &= out.get("oldState") == tok
    for key in paged:
        paged[key] |= set(out.get(key, []))
    tok = out["newState"]
    if not out["hasMoreChanges"]:
        break
_, full = jmap.call("2 unpaged", "CalendarEvent/changes", {"sinceState": s0})
result(
    SERVER,
    "2 paging",
    f"pages {pages}; oldState echoed on every page: {echoed}; "
    f"ends at the unpaged newState: {tok == full['newState']}; "
    f"paged { {k: sorted(v) for k, v in paged.items()} }; "
    f"unpaged created {full['created']} destroyed {full['destroyed']}",
)

# 3. created and destroyed between two states (RFC 8620 section 5.2: should be left out)
s2 = full["newState"]
e = jmap.create("3 create e", {"e": ("probe-e", "e")})["e"]
jmap.call("3 destroy e", "CalendarEvent/set", {"destroy": [e]})
_, out = jmap.call(
    "3 changes after e was created and destroyed", "CalendarEvent/changes", {"sinceState": s2}
)
listed = [k for k in ("created", "updated", "destroyed") if e in out.get(k, [])]
result(SERVER, "3 created and destroyed between states", f"listed in {listed or 'nothing'}")

# 4. states the server never issued, and the oldState echo (RFC 8620 section 5.2)
for label, st in (
    ("garbage", "garbage-state"),
    ("empty", ""),
    ("real state with its last character changed", s0[:-1] + ("A" if s0[-1] != "A" else "B")),
    ("current state with a suffix", s2 + "x"),
):
    name, out = jmap.call(f"4 sinceState {label}", "CalendarEvent/changes", {"sinceState": st})
    detail = (
        out.get("type")
        if name == "error"
        else f"answered, oldState {out.get('oldState')!r} for sinceState {st!r}, "
        f"echoed: {out.get('oldState') == st}"
    )
    result(SERVER, f"4 sinceState {label}", detail)

# 5. ifInState (RFC 8620 section 5.3)
name, out = jmap.call(
    "5 ifInState stale",
    "CalendarEvent/set",
    {"ifInState": s0, "update": {ids["a"]: {"title": "a v3"}}},
)
result(SERVER, "5 ifInState stale", f"{name} {out.get('type')}")
name, out = jmap.call(
    "5 ifInState current",
    "CalendarEvent/set",
    {"ifInState": jmap.state(), "update": {ids["a"]: {"title": "a v3"}}},
)
result(SERVER, "5 ifInState current", f"updated {list(out.get('updated') or {})}")

# 6. properties the server adds; no per-record version exists in RFC 8620
_, out = jmap.call("6 get a", "CalendarEvent/get", {"ids": [ids["a"]]})
result(
    SERVER,
    "6 properties the server added",
    sorted(set(out["list"][0]) - set(jmap.event("x", "x")) - {"id"}),
)

# 7. create without version (draft-ietf-jmap-calendars-32 section 5.9: the server sets it)
bare = {k: v for k, v in jmap.event("probe-noversion", "no version").items() if k != "version"}
_, out = jmap.call("7 create without version", "CalendarEvent/set", {"create": {"n": bare}})
result(SERVER, "7 create without version", json.dumps(out.get("notCreated") or "accepted"))

# 8. duplicate uid (draft-ietf-jmap-calendars-32 section 1.4.1), and ids that are gone
_, out = jmap.call(
    "8 create with an existing uid",
    "CalendarEvent/set",
    {"create": {"dup": jmap.event("probe-a", "dup")}},
)
result(SERVER, "8 duplicate uid", json.dumps(out.get("notCreated") or out.get("created")))
_, out = jmap.call(
    "8 update a destroyed id", "CalendarEvent/set", {"update": {ids["b"]: {"title": "gone"}}}
)
result(SERVER, "8 update a destroyed id", json.dumps(out.get("notUpdated")))
_, out = jmap.call("8 destroy a destroyed id", "CalendarEvent/set", {"destroy": [ids["b"]]})
result(SERVER, "8 destroy a destroyed id", json.dumps(out.get("notDestroyed")))

# 9. a full listing that reads the state first loses nothing changed while it runs
#    (RFC 8620 sections 5.1, 5.2)
jmap.create("9 create five", {f"l{i}": (f"probe-list-{i}", f"before {i}") for i in range(5)})
first_state, position, page_no, new_id = jmap.state(), 0, 0, None
changed_id = ids["c"]
while True:
    _, q = jmap.call(
        f"9 query page {page_no}", "CalendarEvent/query", {"position": position, "limit": 2}
    )
    if not q["ids"]:
        break
    jmap.call(
        f"9 get page {page_no}", "CalendarEvent/get", {"ids": q["ids"], "properties": ["title"]}
    )
    if page_no == 0:
        jmap.call(
            "9 change an event mid-listing",
            "CalendarEvent/set",
            {"update": {changed_id: {"title": "changed mid-listing"}}},
        )
        new_id = jmap.create(
            "9 create an event mid-listing", {"m": ("probe-mid", "created mid-listing")}
        )["m"]
    page_no += 1
    position += len(q["ids"])
_, out = jmap.call(
    "9 changes from the state read before listing",
    "CalendarEvent/changes",
    {"sinceState": first_state},
)
reported = set(out.get("created", [])) | set(out.get("updated", []))
check(SERVER, "9 the event changed mid-listing is reported", changed_id in reported)
check(SERVER, "9 the event created mid-listing is reported", new_id in reported)

finish()
