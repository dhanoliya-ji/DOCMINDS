"""
roles.py
========
WHAT THIS FILE DOES
-------------------
Defines the three roles a member of an organization can hold, and -- the
important part -- the ORDER between them.

    Admin     (admin)   everything an Editor can do, plus: manage members and
                        invites, API keys, and read the audit log.
    Manager   (editor)  everything a Viewer can do, plus: upload, delete and
                        re-index documents, and create and manage projects.
    Employee  (viewer)  read, search and chat. Nothing that changes content.

WHY THE STORED NAMES ARE "Admin / Manager / Employee"
-----------------------------------------------------
Those are the names already sitting in every existing `users.role` column, in
the frontend, and in the JWTs of everyone currently signed in. Renaming them
would need a data migration, a coordinated frontend release and a forced
sign-out, all to change three words. So the stored values stay, and the
generic names -- admin, editor, viewer -- are accepted as ALIASES wherever a
role is checked. `require_role("editor")` and `require_role("Manager")` mean
exactly the same thing.

WHY A RANKING RATHER THAN LISTS
-------------------------------
The older `RoleChecker(["Admin", "Manager"])` spells out every allowed role at
every endpoint. That works until a fourth role is added, at which point every
list has to be found and edited, and the one that is missed becomes a hole.

A ranking says it once: an endpoint names the LOWEST role allowed, and anything
ranked above it passes automatically. "Editors and up" can never accidentally
exclude admins.
"""

from typing import Dict, Optional

# The canonical, stored role names, lowest first.
VIEWER = "Employee"
EDITOR = "Manager"
ADMIN = "Admin"

ROLES = (VIEWER, EDITOR, ADMIN)

# Higher number = more power. Gaps are left so a role could be slotted in
# between later without renumbering.
ROLE_RANK: Dict[str, int] = {VIEWER: 10, EDITOR: 20, ADMIN: 30}

# The generic names the spec and the API docs use, mapped onto stored ones.
ALIASES: Dict[str, str] = {
    "viewer": VIEWER,
    "editor": EDITOR,
    "admin": ADMIN,
    # Accept the stored names in any case too, so "manager" is not a typo trap.
    "employee": VIEWER,
    "manager": EDITOR,
}


def normalize_role(role: str) -> str:
    """Turn any accepted spelling of a role into its stored name.

    Raises ValueError for something that is not a role at all, so a typo in an
    endpoint declaration fails at import time rather than silently locking
    everyone out (or, worse, letting everyone in).
    """
    if role in ROLE_RANK:
        return role
    canonical = ALIASES.get(role.lower())
    if canonical is None:
        raise ValueError(f"Unknown role: {role!r}")
    return canonical


def rank(role: Optional[str]) -> int:
    """The numeric rank of a stored role. Unknown or missing ranks as 0 --
    below every real role, so a corrupted value fails closed."""
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
    """The less powerful of two roles. Used to cap an API key at the role of
    the person who owns it, should they be demoted after creating it."""
    if a is None:
        return b
    if b is None:
        return a
    return a if rank(a) <= rank(b) else b
