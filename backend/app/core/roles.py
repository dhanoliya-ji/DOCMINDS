"""Defines the three roles a member of an organization can hold, and -- the important part -- the ORDER..."""
from typing import Dict, Optional

VIEWER = "Employee"
EDITOR = "Manager"
ADMIN = "Admin"

ROLES = (VIEWER, EDITOR, ADMIN)

ROLE_RANK: Dict[str, int] = {VIEWER: 10, EDITOR: 20, ADMIN: 30}

ALIASES: Dict[str, str] = {
    "viewer": VIEWER,
    "editor": EDITOR,
    "admin": ADMIN,
    "employee": VIEWER,
    "manager": EDITOR,
}


def normalize_role(role: str) -> str:
    """Turn any accepted spelling of a role into its stored name."""
    if role in ROLE_RANK:
        return role
    canonical = ALIASES.get(role.lower())
    if canonical is None:
        raise ValueError(f"Unknown role: {role!r}")
    return canonical


def rank(role: Optional[str]) -> int:
    """The numeric rank of a stored role. Unknown or missing ranks as 0 --"""
    if not role:
        return 0
    try:
        return ROLE_RANK[normalize_role(role)]
    except ValueError:
        return 0


def at_least(role: Optional[str], minimum: str) -> bool:
    """Does `role` meet or exceed `minimum`?"""
    return rank(role) >= rank(minimum) > 0


def lower_of(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The less powerful of two roles. Used to cap an API key at the role of"""
    if a is None:
        return b
    if b is None:
        return a
    return a if rank(a) <= rank(b) else b
