# SPDX-FileCopyrightText: 2026 calendaring-sync contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the calendaring_sync package.

Rule: zero network calls, zero Docker dependency, all tests are fast.
"""

import calendaring_sync


class TestPackage:
    def test_version_is_set(self):
        assert isinstance(calendaring_sync.__version__, str)
        assert calendaring_sync.__version__
