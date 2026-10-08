<!--- SPDX-FileCopyrightText: 2026 calendaring-sync contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# calendaring-sync

Calendar sync state: sync tokens, object versions, change tracking, and conflict detection.

Part of the [Python Calendaring Ecosystem](https://pycal.org).

[![Documentation](https://readthedocs.org/projects/calendaring-sync/badge/?version=latest)](https://calendaring-sync.readthedocs.io/en/latest/)
[![REUSE status](https://api.reuse.software/badge/github.com/pycalendar/calendaring-sync)](https://api.reuse.software/info/github.com/pycalendar/calendaring-sync)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL%203.0--or--later-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

calendaring-sync keeps the state a calendar sync needs between runs: the sync token the server last issued, a version for each object, and a local copy of what was last synced. It tells you what changed on each side and where local and remote changes conflict; your code decides what to do with each conflict. It doesn't talk to servers or run a sync loop itself: adapters connect it to a protocol's client library. It's in early development and has no release yet.

## Documentation

Full documentation: https://calendaring-sync.readthedocs.io/en/latest/

## Contributing

See the [contributing guide](https://calendaring-sync.readthedocs.io/en/latest/contribute.html).

## Funding

This project is funded through [NGI0 Commons Fund](https://nlnet.nl/commonsfund), a fund established by [NLnet](https://nlnet.nl) with financial support from the European Commission's [Next Generation Internet](https://ngi.eu) program. Learn more at the [NLnet project page](https://nlnet.nl/project/Python-Webcalendaring).

[<img src="https://nlnet.nl/logo/banner.png" alt="NLnet foundation logo" width="20%" />](https://nlnet.nl)
[<img src="https://nlnet.nl/image/logos/NGI0_tag.svg" alt="NGI Zero Logo" width="20%" />](https://nlnet.nl/commonsfund)

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
