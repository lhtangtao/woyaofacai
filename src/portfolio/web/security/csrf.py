"""CSRF (Cross-Site Request Forgery) protection with token verification and Origin/Referer check."""

import hmac
import logging
import secrets
from typing import Optional, Tuple
from urllib.parse import urlparse

from flask import Request

logger = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_COOKIE_NAME = "money_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request, allow_login_exemption: bool = True) -> Tuple[bool, str]:
    """Validate CSRF token and Origin/Referer header for mutating HTTP requests.

    Returns:
        (is_valid, error_message)
    """
    if request.method in SAFE_METHODS:
        return True, ""

    # 1. Check Origin / Referer (Anti-cross-site origin check)
    host = request.host.lower()
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")

    if origin:
        parsed_origin = urlparse(origin).netloc.lower()
        if parsed_origin != host:
            logger.warning(f"CSRF Origin mismatch: {parsed_origin} != {host}")
            return False, "跨站请求已被阻止 (Origin 不匹配)"
    elif referer:
        parsed_referer = urlparse(referer).netloc.lower()
        if parsed_referer != host:
            logger.warning(f"CSRF Referer mismatch: {parsed_referer} != {host}")
            return False, "跨站请求已被阻止 (Referer 不匹配)"

    # 2. Login endpoint exemption: Login creates the first session, so it only checks Origin
    if allow_login_exemption and request.path == "/api/auth/login":
        return True, ""

    # 3. Check CSRF token for all other mutating requests
    expected_token = request.cookies.get(CSRF_COOKIE_NAME)
    submitted_token = (
        request.headers.get(CSRF_HEADER_NAME)
        or (request.get_json(silent=True) or {}).get("csrf_token")
        or request.form.get("csrf_token")
    )

    if not expected_token:
        logger.warning(f"CSRF cookie missing for {request.path}")
        return False, "缺少 CSRF 安全 Cookie，请刷新页面后重试"

    if not submitted_token:
        logger.warning(f"CSRF token missing in request headers for {request.path}")
        return False, "请求中缺少 X-CSRF-Token 请求头"

    if not hmac.compare_digest(expected_token, submitted_token):
        logger.warning(f"CSRF token mismatch for {request.path}")
        return False, "CSRF 令牌无效或已过期"

    return True, ""
