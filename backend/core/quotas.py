"""Per-user ceilings that hold on every plan (abuse and disk protection, not product tiers).

They live next to ``core.tiers`` (which is about what a *plan* may do) but are deliberately
not plan-dependent: no tier lifts them. Read them as ``quotas.NAME`` -- never copy the value
-- so a test (or a future per-plan override) can change one place.
"""
from __future__ import annotations

# Receipt photos (fin-02): what one user may keep in total.
MAX_USER_ATTACHMENT_BYTES = 500 * 1024 * 1024  # 500 MB of pictures
MAX_USER_ATTACHMENTS = 1000

# Finance projects (fin-01/02): expenses one user may hold across all their projects.
MAX_USER_EXPENSES = 20_000
