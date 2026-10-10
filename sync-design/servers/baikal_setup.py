# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Drive Baikal's install wizard and admin UI: config, SQLite database, user 'test' / 'test'."""

import re

import requests

B = "http://127.0.0.1:8811"
s = requests.Session()


def submit(url, fill):
    page = s.get(url).text
    form = dict(
        re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page)
    )
    form.update(fill)
    r = s.post(url, files={k: (None, v) for k, v in form.items()})
    err = re.search(
        r"Validation error.{0,200}", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
    )
    print(url, r.status_code, "ERROR: " + err.group(0) if err else "ok")


submit(
    f"{B}/admin/install/",
    {
        "data[timezone]": "UTC",
        "data[card_enabled]": "1",
        "data[cal_enabled]": "1",
        "data[invite_from]": "noreply@example.org",
        "data[dav_auth_type]": "Basic",
        "data[admin_passwordhash]": "adminpass",
        "data[admin_passwordhash_confirm]": "adminpass",
    },
)
submit(
    f"{B}/admin/install/",
    {"data[backend]": "sqlite", "data[sqlite_file]": "/var/www/baikal/Specific/db/db.sqlite"},
)
s.post(f"{B}/admin/", data={"auth": "1", "login": "admin", "password": "adminpass"})
submit(
    f"{B}/admin/?/users/new/1/",
    {
        "data[username]": "test",
        "data[displayname]": "test",
        "data[email]": "test@example.org",
        "data[password]": "test",
        "data[passwordconfirm]": "test",
    },
)
r = requests.request(
    "PROPFIND", f"{B}/dav.php/calendars/test/", auth=("test", "test"), headers={"Depth": "1"}
)
print("calendar home:", r.status_code)
