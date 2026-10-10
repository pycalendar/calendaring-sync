# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Truncate Stalwart's change log for testuser (account id from x:Account), as Stalwart's own
tests/src/system/purge.rs does: DataRetention.maxChangesHistory = 1, reload settings, then an
AccountMaintenance purge task. Usage: stalwart_expire.py setup | purge

The fixture's default config holds SpamDnsblServer entries whose expressions Stalwart's own
reload rejects ("bit_and"); setup removes each one the reload names, in this throwaway
container only, so the reload can apply the new history limit.
"""

import base64
import json
import sys
import time
import urllib.request

AUTH = "Basic " + base64.b64encode(b"admin:adminpass").decode()


def call(name, args):
    body = {
        "using": ["urn:ietf:params:jmap:core"],
        "methodCalls": [[name, {"accountId": "d333333", **args}, "0"]],
    }
    req = urllib.request.Request(
        "http://localhost:8809/jmap",
        data=json.dumps(body).encode(),
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
    )
    out = json.loads(urllib.request.urlopen(req).read())["methodResponses"][0]
    print(f">>> {name} {json.dumps(args)[:200]}\n<<< {out[0]} {json.dumps(out[1])[:400]}")
    return out[1]


def testuser_id():
    accounts = call(
        "x:Account/get", {"ids": call("x:Account/query", {})["ids"], "properties": ["name"]}
    )["list"]
    return next(a["id"] for a in accounts if a["name"] == "testuser")


if sys.argv[1] == "setup":
    call("x:DataRetention/set", {"update": {"singleton": {"maxChangesHistory": 1}}})
    for _ in range(40):
        r = call("x:Action/set", {"create": {"r": {"@type": "ReloadSettings"}}})
        if r.get("created"):
            print("SUMMARY [Stalwart] reload with maxChangesHistory 1: ok")
            break
        bad = r["notCreated"]["r"]["objectId"]
        call(f"x:{bad['object']}/set", {"destroy": [bad["id"]]})
else:
    call(
        "x:Task/set",
        {
            "create": {
                "t": {
                    "@type": "AccountMaintenance",
                    "accountId": testuser_id(),
                    "maintenanceType": "purge",
                }
            }
        },
    )
    time.sleep(10)
