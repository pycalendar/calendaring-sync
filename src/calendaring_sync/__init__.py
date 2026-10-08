# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Calendar sync state.

Tracks sync tokens and per-object versions across sync cycles, keeps a local
copy of synced calendar objects, and detects conflicts between local and remote
changes.
"""

from calendaring_sync._version import __version__

__all__ = ["__version__"]
