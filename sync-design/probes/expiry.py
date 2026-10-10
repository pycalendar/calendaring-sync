# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A token or state the server has expired. Run 'before', expire on the server, then 'after'.

Usage:
  expiry.py caldav SERVER BASE_URL CALENDAR_PATH USER PASSWORD before|tick|after STATE_FILE
  expiry.py jmap   SERVER BASE_URL USER PASSWORD NEEDS_VERSION(yes|no) before|after STATE_FILE

caldav: 'before' syncs two known objects, then deletes one ('known'), edits the other ('edited')
and creates three more; it saves the token. 'tick' makes a change and syncs, for servers that
only discard old tokens when they issue a new one. 'after' reuses the saved token: a server that
rejects it answers DAV:valid-sync-token; a server that answers 207 is checked for whether the
removal and the edit are still reported. It then shows python-caldav on the same token, with the
adapter's retry: an empty token after AuthorizationError.
jmap: 'before' saves a state and creates and destroys events; 'after' calls /changes from it.
"""

import sys
import uuid
from pathlib import Path

from _common import DAV, JMAP, caldav_sync_report, finish, ics, result

mode, SERVER = sys.argv[1:3]

if mode == "caldav":
    BASE, CALENDAR, USER, PASSWORD, phase, state_file = sys.argv[3:9]
    saved = Path(state_file)
    dav = DAV(BASE, CALENDAR, USER, PASSWORD)
    if phase == "before":
        dav.request("create calendar", "MKCALENDAR", CALENDAR)
        dav.put("create known", "known.ics", ics("known", "v1"))
        dav.put("create edited", "edited.ics", ics("edited", "v1"))
        _, _, token = dav.sync("full sync", "")
        dav.delete("delete known", "known.ics")
        dav.put("edit edited", "edited.ics", ics("edited", "v2"))
        for n in range(3):
            dav.put(f"create later{n}", f"later{n}.ics", ics(f"later{n}", "x"))
        _, m, _ = dav.sync("incremental from the saved token, before expiry", token)
        result(SERVER, "before expiry, incremental from the saved token", m)
        saved.write_text(token, encoding="utf-8")
    elif phase == "tick":
        name = f"tick-{uuid.uuid4()}.ics"
        created = dav.put("tick: create", name, ics(name, "tick"))
        _, _, token = dav.sync("tick: sync, so the server issues a new token", "")
        result(SERVER, "tick", f"create {created.status_code}, new token issued: {bool(token)}")
    else:
        token = saved.read_text(encoding="utf-8").strip()
        r, m, _ = dav.sync("incremental from the saved token, after expiry", token)
        rejected = "valid-sync-token" in r.text
        result(
            SERVER,
            "after expiry, the saved token",
            f"{r.status_code}, valid-sync-token: {rejected}, members {m}",
        )
        if r.status_code == 207 and not rejected:
            result(
                SERVER,
                "after expiry, changes still reported",
                f"known.ics removal: {('known.ics', '404') in m}; "
                f"edited.ics: {any(n == 'edited.ics' for n, _ in m)}",
            )
        from caldav.davclient import DAVClient
        from caldav.lib import error

        client = DAVClient(url=BASE + "/", username=USER, password=PASSWORD)
        try:
            caldav_sync_report(client, BASE + CALENDAR, token)
            result(SERVER, "python-caldav with the saved token", "no exception")
        except error.AuthorizationError as e:
            retry = caldav_sync_report(client, BASE + CALENDAR)
            result(
                SERVER,
                "python-caldav with the saved token",
                f"AuthorizationError {vars(e)}; retry with an empty token: {retry.status}",
            )
else:
    BASE, USER, PASSWORD, NEEDS_VERSION, phase, state_file = sys.argv[3:9]
    saved = Path(state_file)
    jmap = JMAP(BASE, USER, PASSWORD, NEEDS_VERSION == "yes")
    if phase == "before":
        state = jmap.state()
        for round_no in range(3):
            ids = jmap.create(
                f"create round {round_no}",
                {f"e{i}": (f"expiry-{round_no}-{i}", "x") for i in range(3)},
            )
            jmap.call(
                f"destroy round {round_no}", "CalendarEvent/set", {"destroy": list(ids.values())}
            )
        name, out = jmap.call(
            "changes from the saved state, before expiry",
            "CalendarEvent/changes",
            {"sinceState": state},
        )
        result(
            SERVER,
            "before expiry, /changes from the saved state",
            f"{name} {out.get('type', 'answered')}",
        )
        saved.write_text(state, encoding="utf-8")
    else:
        state = saved.read_text(encoding="utf-8").strip()
        name, out = jmap.call(
            "changes from the saved state, after expiry",
            "CalendarEvent/changes",
            {"sinceState": state},
        )
        result(
            SERVER,
            "after expiry, /changes from the saved state",
            f"{name} {out.get('type')} {out.get('description', '')}",
        )

finish()
