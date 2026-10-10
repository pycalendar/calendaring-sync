# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What calendaring-jmap does with sync responses from a real server.

Usage: jmap_client.py SERVER SESSION_URL USER PASSWORD
"""

import functools
import sys

import calendaring_jmap
import calendaring_jmap.client as jmap_client_module
from calendaring_jmap import JMAPClient
from calendaring_jmap._methods.event import build_event_changes

from _common import finish, ics, outcome, result

SERVER, SESSION_URL, USER, PASSWORD = sys.argv[1:5]
client = JMAPClient(url=SESSION_URL, username=USER, password=PASSWORD)


result(SERVER, "calendaring-jmap version", calendaring_jmap.__version__)
result(
    SERVER,
    "state the server never issued",
    outcome(lambda: client.get_objects_by_sync_token("garbage-state")),
)
token = client.get_sync_token()
calendar = client.get_calendars()[0]
for n in range(3):
    calendar.add_event(ics(f"jmap-client-{n}", "x"))
result(
    SERVER,
    "three creates since the token, unlimited",
    outcome(
        lambda: tuple(
            len(x) if isinstance(x, list) else x for x in client.get_objects_by_sync_token(token)
        )
    ),
)
# The client calls its internal build_event_changes by module-level name; patching that name
# puts maxChanges 1 into the client's own request.
patched = functools.partial(build_event_changes, max_changes=1)
jmap_client_module.build_event_changes = patched  # pyright: ignore[reportPrivateImportUsage]
result(
    SERVER,
    "the same, with maxChanges 1 in calendaring-jmap's own request",
    outcome(lambda: client.get_objects_by_sync_token(token)),
)
jmap_client_module.build_event_changes = build_event_changes  # pyright: ignore[reportPrivateImportUsage]
result(SERVER, "get_task_sync_token()", outcome(client.get_task_sync_token))

finish()
