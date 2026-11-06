"""What each person may do. `admin` is a preset that holds every permission; `member` starts with
none and an admin grants specific ones. Permissions are stored as comma-separated names
(users.permissions).

Rules, enforced in service.py and checked per request by the gate in web.py:
  * a protected route names the one permission it needs;
  * you can only grant or remove permissions you hold yourself, and never your own;
  * admins always hold everything, so their stored list is ignored.
"""

# In display order. Add new ones at the end and give the route table in web.py a row for them.
ALL = ("upload", "collector", "maintenance", "status", "settings", "users")
LABELS = {
    "upload": "Upload music",
    "collector": "Use the collector",
    "maintenance": "Run and approve maintenance",
    "status": "See the status tab",
    "settings": "Change site settings",
    "users": "Manage users",
}
# Anyone holding one of these may open the Admin page (each tab still checks its own permission).
ADMIN_PAGE_TABS = ("status", "maintenance", "collector")


def clean(raw) -> tuple:
    """A list of permission names in display order, without duplicates. Raises ValueError for
    unknown names."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [p for p in raw.split(",") if p.strip()]
    names = {str(p).strip() for p in raw}
    unknown = sorted(names - set(ALL))
    if unknown:
        raise ValueError("Unknown permission: " + ", ".join(n[:30] for n in unknown))
    return tuple(p for p in ALL if p in names)


def to_text(perms) -> str:
    return ",".join(clean(perms))


def from_text(text) -> frozenset:
    """Reads the stored value. Unknown names (from a hand edit) are dropped rather than trusted."""
    return frozenset(p.strip() for p in str(text or "").split(",")) & frozenset(ALL)


def effective(role: str, text) -> frozenset:
    return frozenset(ALL) if role == "admin" else from_text(text)


def can_open_admin_page(user: dict) -> bool:
    return user.get("role") == "admin" or bool(
        set(ADMIN_PAGE_TABS) & set(user.get("permissions", ()))
    )


def allows(user: dict, need: str) -> bool:
    """`need` is a permission name, "admin_page" (any admin tab), or "admin" (the admin role itself)."""
    if need == "admin":
        return user.get("role") == "admin"
    if need == "admin_page":
        return can_open_admin_page(user)
    return need in user.get("permissions", ())
