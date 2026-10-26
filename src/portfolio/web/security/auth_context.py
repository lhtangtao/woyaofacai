"""Unified authorization context and capability matrix."""

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Optional, Tuple

# Capability constants
CAP_VIEW_ASSETS = "assets:view"
CAP_VIEW_MARKET = "market:view"
CAP_MANAGE_RULES = "rules:manage"
CAP_MANAGE_BARK = "bark:manage"
CAP_AI_CHAT = "ai:chat"
CAP_AI_STOCK = "ai:stock"
CAP_MANAGE_USERS = "users:manage"
CAP_AUDIT_LOGS = "audit:view"

ADMIN_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    CAP_MANAGE_RULES,
    CAP_MANAGE_BARK,
    CAP_AI_CHAT,
    CAP_AI_STOCK,
    CAP_MANAGE_USERS,
    CAP_AUDIT_LOGS,
})

USER_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    CAP_AI_CHAT,
    CAP_AI_STOCK,
})

# LAN_VIEWER has read-only access to assets & market monitoring; NO write, NO bark, NO custom personal AI chat
LAN_VIEWER_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    CAP_AI_STOCK,
})

UNAUTH_CAPABILITIES: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class AuthContext:
    """Security authorization context computed strictly on server-side for each request."""

    access_mode: str  # LOCAL_LOOPBACK, DIRECT_PHYSICAL_LAN, REMOTE_TAILSCALE, REMOTE_PUBLIC, UNKNOWN
    principal_type: str  # LAN_VIEWER, ADMIN, USER, UNAUTHENTICATED
    user_id: Optional[int]
    username: Optional[str]
    allowed_person_ids: Optional[Tuple[int, ...]]  # None = full portfolio scope; (5,) = isolated to person 5
    person_name: Optional[str]
    capabilities: FrozenSet[str]
    is_authenticated: bool
    session_id: Optional[str] = None

    def has_capability(self, cap: str) -> bool:
        """Check if current context has the requested capability."""
        return cap in self.capabilities

    def is_admin(self) -> bool:
        """Check if current context is an authenticated administrator."""
        return self.is_authenticated and self.principal_type == "ADMIN"

    def is_lan_viewer(self) -> bool:
        """Check if current context is an anonymous LAN read-only visitor."""
        return self.principal_type == "LAN_VIEWER"

    def is_unauthenticated(self) -> bool:
        """Check if current context is an unauthenticated remote visitor."""
        return self.principal_type == "UNAUTHENTICATED"

    def can_access_person(self, target_person_id: Optional[int]) -> bool:
        """Check if user is permitted to query the specific target_person_id."""
        if self.principal_type in ("ADMIN", "LAN_VIEWER"):
            return True
        if self.principal_type == "USER":
            if self.allowed_person_ids is None or not self.allowed_person_ids:
                return False
            if target_person_id is None:
                return False
            return target_person_id in self.allowed_person_ids
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to safe JSON-serializable dictionary (no secrets)."""
        if self.is_admin():
            role_name = "admin"
        elif self.principal_type == "USER":
            role_name = "user"
        elif self.principal_type == "LAN_VIEWER":
            role_name = "lan_viewer"
        else:
            role_name = "unauthenticated"

        return {
            "access_mode": self.access_mode,
            "principal_type": self.principal_type,
            "user_id": self.user_id,
            "username": self.username,
            "role": role_name,
            "person_id": self.allowed_person_ids[0] if (self.allowed_person_ids and len(self.allowed_person_ids) == 1) else None,
            "person_name": self.person_name,
            "is_authenticated": self.is_authenticated,
            "is_lan": self.access_mode in ("LOCAL_LOOPBACK", "DIRECT_PHYSICAL_LAN"),
            "login_required": self.is_unauthenticated(),
            "can_admin": self.has_capability("manage_users"),
            "can_write": self.has_capability("modify_rules"),
            "can_chat": self.has_capability("execute_ai_chat"),
            "capabilities": list(self.capabilities),
        }


def build_auth_context(
    access_mode: str,
    session_user: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> AuthContext:
    """Build the authoritative AuthContext based on network classification and active session."""
    # 1. Check if authenticated user session is active
    if session_user and session_user.get("user_id"):
        role = session_user.get("role", "user")
        user_id = session_user["user_id"]
        username = session_user.get("username", "")
        person_id = session_user.get("person_id")
        person_name = session_user.get("person_name")

        if role == "admin":
            return AuthContext(
                access_mode=access_mode,
                principal_type="ADMIN",
                user_id=user_id,
                username=username,
                allowed_person_ids=None,  # Admin has full scope
                person_name=person_name or "管理员",
                capabilities=ADMIN_CAPABILITIES,
                is_authenticated=True,
                session_id=session_id,
            )
        else:
            allowed_ids = (person_id,) if person_id is not None else ()
            return AuthContext(
                access_mode=access_mode,
                principal_type="USER",
                user_id=user_id,
                username=username,
                allowed_person_ids=allowed_ids,
                person_name=person_name or username,
                capabilities=USER_CAPABILITIES,
                is_authenticated=True,
                session_id=session_id,
            )

    # 2. No session: check network access mode
    if access_mode in ("LOCAL_LOOPBACK", "DIRECT_PHYSICAL_LAN"):
        # PRD 1.3: LAN anonymous visitor gets LAN_VIEWER read-only role with full portfolio view
        return AuthContext(
            access_mode=access_mode,
            principal_type="LAN_VIEWER",
            user_id=None,
            username="局域网访客",
            allowed_person_ids=None,
            person_name="局域网访客",
            capabilities=LAN_VIEWER_CAPABILITIES,
            is_authenticated=False,
            session_id=None,
        )

    # 3. Remote access without session (Tailscale, 4G/5G, WAN, Unknown):
    return AuthContext(
        access_mode=access_mode,
        principal_type="UNAUTHENTICATED",
        user_id=None,
        username=None,
        allowed_person_ids=(),
        person_name=None,
        capabilities=UNAUTH_CAPABILITIES,
        is_authenticated=False,
        session_id=None,
    )
