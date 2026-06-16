"""Account management schemas."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class DeleteAccountRequest(BaseModel):
    # User retypes their email to confirm the irreversible deletion.
    confirm_email: EmailStr
