<!--
SPDX-FileCopyrightText: 2026 calendaring-sync contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Source references

The server and library code behind behaviors the logs show, linked at the version that was tested rather than copied here.

## Stalwart v0.16.21

- CalDAV sync reads the change log without checking whether it was truncated: [`crates/dav/src/common/propfind.rs` lines 1223 to 1226](https://github.com/stalwartlabs/stalwart/blob/v0.16.21/crates/dav/src/common/propfind.rs#L1223-L1226). The file contains no `is_truncated` check.
- JMAP `/changes` answers `cannotCalculateChanges` for a truncated log: [`crates/jmap/src/changes/get.rs` lines 283 to 290](https://github.com/stalwartlabs/stalwart/blob/v0.16.21/crates/jmap/src/changes/get.rs#L283-L290).
- Stalwart's own test truncates the log the way `servers/stalwart_expire.py` does: [`tests/src/system/purge.rs` lines 38 to 48 and 145 to 152](https://github.com/stalwartlabs/stalwart/blob/v0.16.21/tests/src/system/purge.rs#L38-L48).

## Radicale 3.8.3

- An empty token reports deletion history as removed members: [`radicale/storage/multifilesystem/sync.py` lines 56 to 63 and 113 to 121](https://github.com/Kozea/Radicale/blob/v3.8.3/radicale/storage/multifilesystem/sync.py#L56-L63).

## Xandikos 0.4.8

- `DAV:limit` cuts the list with `itertools.islice` and the response still carries the newest token: [`xandikos/sync.py` lines 108 to 144](https://github.com/jelmer/xandikos/blob/v0.4.8/xandikos/sync.py#L108-L144).

## python-caldav 3.4.0 (unchanged on master at fa5ead6)

- A 403 becomes `AuthorizationError` with only the URL and reason: [`caldav/davclient.py` lines 864 to 866](https://github.com/python-caldav/caldav/blob/v3.4.0/caldav/davclient.py#L864-L866) and [`caldav/base_client.py` lines 342 to 357](https://github.com/python-caldav/caldav/blob/v3.4.0/caldav/base_client.py#L342-L357).
- A multistatus entry with any status other than 200, 201, 207 or 404 raises, so a 507 page fails: [`caldav/response.py` lines 92 to 100](https://github.com/python-caldav/caldav/blob/v3.4.0/caldav/response.py#L92-L100).
- `save()` increments `SEQUENCE` by default: [`caldav/calendarobjectresource.py` lines 1523 to 1527](https://github.com/python-caldav/caldav/blob/v3.4.0/caldav/calendarobjectresource.py#L1523-L1527).
- `delete()` sends no precondition: [`caldav/davobject.py` line 647](https://github.com/python-caldav/caldav/blob/v3.4.0/caldav/davobject.py#L647).
