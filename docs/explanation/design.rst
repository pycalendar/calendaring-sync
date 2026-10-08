.. SPDX-FileCopyrightText: 2026 calendaring-sync contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

======
Design
======

.. code-block:: text

    Your application
           |
           v
    calendaring-sync
      sync state, local copy, conflict detection
           |
           v
    Adapters

Why calendaring-sync doesn't talk to servers
============================================

calendaring-sync only keeps sync state: it doesn't send requests, schedule syncs, or run a sync loop. Your application decides when to sync, and an adapter fetches what changed. Keeping network access out of the core means the same state handling works for any server and any protocol an adapter supports.

Why each protocol is an adapter
===============================

The core describes sync in its own terms: a sync token for "what changed since last time," a version for "did this object change," and an identity for each object. An adapter maps one protocol's equivalents onto those terms and translates its change reports. Supporting a new protocol means writing a new adapter, not changing the core.

Why conflicts are reported, not resolved
========================================

When the same object changed both locally and on the server, or was deleted on one side and edited on the other, calendaring-sync reports the conflict and leaves the decision to your code. Only the application knows whether the local edit, the server's version, or a merge of the two is right, so the library never picks a winner for you.
