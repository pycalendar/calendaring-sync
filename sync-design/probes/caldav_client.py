# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What python-caldav does with sync responses from a real server.

Usage: caldav_client.py SERVER BASE_URL CALENDAR_URL USER PASSWORD

The calendar at CALENDAR_URL must exist and may be empty.
"""

import sys

import caldav
from caldav.davclient import DAVClient

from _common import (
    caldav_sync_report,
    finish,
    ics,
    multiget_body,
    outcome,
    result,
    save_event,
    sync_body,
    sync_objects,
    tree,
)

SERVER, BASE, CALENDAR_URL, USER, PASSWORD = sys.argv[1:6]
client = DAVClient(url=BASE, username=USER, password=PASSWORD)
calendar = client.calendar(url=CALENDAR_URL)
NEVER_ISSUED = "http://example.invalid/never-issued"
LIMIT_1 = b"<D:limit><D:nresults>1</D:nresults></D:limit><D:prop>"


def limited(raw):
    """Adds DAV:limit nresults=1 to a sync-collection body."""
    return raw.replace(b"<D:prop>", LIMIT_1, 1)


def token_and_count(**kwargs):
    """The token and object count get_objects_by_sync_token() returns for these arguments."""
    objects = sync_objects(calendar, **kwargs)
    return objects.sync_token, len(list(objects))


result(SERVER, "python-caldav version", caldav.__version__)
result(
    SERVER,
    "unknown token, get_objects_by_sync_token(disable_fallback=True)",
    outcome(lambda: token_and_count(sync_token=NEVER_ISSUED, disable_fallback=True)),
)
result(
    SERVER,
    "unknown token, DAVClient.report()",
    outcome(lambda: caldav_sync_report(client, CALENDAR_URL, NEVER_ISSUED).status),
)
result(
    SERVER,
    "unknown token, default fallback",
    outcome(lambda: token_and_count(sync_token=NEVER_ISSUED)),
)

token = sync_objects(calendar, disable_fallback=True).sync_token
event = save_event(calendar, ics("client-removed", "removed"))
token = sync_objects(calendar, sync_token=token, disable_fallback=True).sync_token
event.delete()
removed = [
    (str(o.url).rsplit("/", 1)[-1], o.props.get("{DAV:}getetag"), o.data is None)
    for o in sync_objects(calendar, sync_token=token, disable_fallback=True)
]
result(SERVER, "a removed member as returned: (href, getetag, data is None)", removed)

for n in range(3):
    save_event(calendar, ics(f"client-{n}", "x"))
page = client.report(CALENDAR_URL, limited(sync_body(client)).decode(), depth=0)
has_507 = any("507" in (s.text or "") for s in tree(page).iter("{DAV:}status"))
result(
    SERVER,
    "DAV:limit 1 through DAVClient.report()",
    f"status {page.status}, 507 present: {has_507}, token {page.sync_token}",
)
if has_507:
    original = client._build_sync_collection_body
    client._build_sync_collection_body = lambda *a, **k: limited(original(*a, **k))
    result(
        SERVER,
        "real 507 page, get_objects_by_sync_token(disable_fallback=True)",
        outcome(lambda: token_and_count(disable_fallback=True)),
    )
    result(
        SERVER,
        "real 507 page, default fallback",
        outcome(token_and_count),
    )
    client._build_sync_collection_body = original
    result(
        SERVER,
        "real 507 page, DAVResponse.parse_sync_collection()",
        outcome(page.parse_sync_collection),
    )

hrefs = [str(o.url) for o in sync_objects(calendar, disable_fallback=True)][:2]
multiget = client.report(CALENDAR_URL, multiget_body(hrefs), depth=None)
entries = len(tree(multiget).findall("{DAV:}response"))
result(
    SERVER,
    "calendar-multiget through DAVClient.report(depth=None), no Depth header",
    f"status {multiget.status}, entries {entries} for {len(hrefs)} hrefs",
)

finish()
