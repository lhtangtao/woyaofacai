"""Network access classification and physical LAN boundary verification.

Classifies incoming requests into:
- LOCAL_LOOPBACK: Connection from Mac localhost (127.0.0.1 / ::1).
- DIRECT_PHYSICAL_LAN: Direct TCP connection from a device in the Mac's physical LAN subnet.
- REMOTE_TAILSCALE: Connection over Tailscale virtual mesh (100.64.0.0/10).
- REMOTE_PUBLIC: Public WAN, 4G/5G, or external networks.
- UNKNOWN: Ambiguous, virtual, or unverified network origins (safe default: requires login).
"""

from functools import lru_cache
import ipaddress
import logging
import time
from typing import Dict, List, Optional, Set

import psutil

logger = logging.getLogger(__name__)

# Virtual/Tunnel interface prefix blocklist (never physical LANs)
VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "utun",
    "tun",
    "tap",
    "tailscale",
    "docker",
    "bridge",
    "vboxnet",
    "vmnet",
    "clash",
    "ppp",
    "gif",
    "stf",
    "awdl",
    "llw",
)

# Explicit Tailscale CGNAT subnet (RFC 6598)
TAILSCALE_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")

# Clash fake-ip benchmark subnet
CLASH_FAKE_NETWORK = ipaddress.IPv4Network("198.18.0.0/15")

# Cache physical LAN networks for 10 seconds to avoid repeating syscalls on every request
_cached_networks: List[ipaddress.IPv4Network] = []
_last_cache_time: float = 0.0


def get_active_physical_lan_networks(force_refresh: bool = False) -> List[ipaddress.IPv4Network]:
    """Retrieve active IPv4 subnets attached to real physical network interfaces on this Mac."""
    global _cached_networks, _last_cache_time
    now = time.time()
    if not force_refresh and _cached_networks and (now - _last_cache_time < 10.0):
        return _cached_networks

    networks: List[ipaddress.IPv4Network] = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        for iface_name, addr_list in addrs.items():
            lower_name = iface_name.lower()
            # Exclude virtual / tunnel / loopback / container interfaces
            if any(lower_name.startswith(p) for p in VIRTUAL_IFACE_PREFIXES):
                continue

            # Must be operational (UP)
            st = stats.get(iface_name)
            if not st or not st.isup:
                continue

            for addr in addr_list:
                # AF_INET is IPv4
                if getattr(addr.family, "name", "") == "AF_INET" or addr.family == 2:
                    ip_str = addr.address
                    mask_str = addr.netmask
                    if not ip_str or not mask_str:
                        continue
                    try:
                        ip_obj = ipaddress.IPv4Address(ip_str)
                        if ip_obj.is_loopback or ip_obj.is_link_local:
                            continue
                        if ip_obj in TAILSCALE_NETWORK or ip_obj in CLASH_FAKE_NETWORK:
                            continue

                        net = ipaddress.IPv4Network(f"{ip_str}/{mask_str}", strict=False)
                        if net not in networks:
                            networks.append(net)
                    except Exception as e:
                        logger.debug(f"Error parsing interface {iface_name} subnet: {e}")
    except Exception as e:
        logger.warning(f"Failed to inspect physical network interfaces: {e}")

    _cached_networks = networks
    _last_cache_time = now
    return networks


def classify_network_access(
    remote_addr: Optional[str],
    headers: Optional[Dict[str, str]] = None,
    trusted_proxies: Optional[Set[str]] = None,
) -> str:
    """Classify the incoming connection's network environment.

    Args:
        remote_addr: The actual TCP socket peer IP (request.remote_addr).
        headers: Request HTTP headers (to detect proxy forwarded traffic).
        trusted_proxies: Optional set of trusted reverse proxy IPs.

    Returns:
        One of: 'LOCAL_LOOPBACK', 'DIRECT_PHYSICAL_LAN', 'REMOTE_TAILSCALE',
        'REMOTE_PUBLIC', or 'UNKNOWN'.
    """
    if not remote_addr:
        return "UNKNOWN"

    raw_ip = remote_addr.strip().lower()
    headers = headers or {}
    trusted_proxies = trusted_proxies or {"127.0.0.1", "::1"}

    # 1. Check if this is a reverse proxy connection on localhost
    is_socket_loopback = raw_ip in ("127.0.0.1", "::1", "localhost")

    if is_socket_loopback:
        # Check if an external client passed through a local proxy (Nginx, Caddy, Cloudflare tunnel)
        forwarded_for = headers.get("X-Forwarded-For") or headers.get("X-Real-IP") or headers.get("CF-Connecting-IP")
        if forwarded_for:
            # Client IP is the first IP in X-Forwarded-For chain
            client_ip_candidate = forwarded_for.split(",")[0].strip()
            if client_ip_candidate and client_ip_candidate not in ("127.0.0.1", "::1"):
                # Connection came from a proxy representing an external client
                return _classify_ip_address(client_ip_candidate, is_proxy_forwarded=True)

        # Pure direct connection from Mac localhost
        return "LOCAL_LOOPBACK"

    # 2. Check if a non-loopback connection claims proxy headers
    # Never trust X-Forwarded-For from untrusted remote IPs (anti-spoofing)
    return _classify_ip_address(raw_ip, is_proxy_forwarded=False)


def _classify_ip_address(ip_str: str, is_proxy_forwarded: bool = False) -> str:
    """Helper to classify an IPv4/IPv6 address against physical subnets and Tailscale."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return "UNKNOWN"

    # Loopback
    if ip.is_loopback:
        return "LOCAL_LOOPBACK"

    # IPv4 Classification
    if isinstance(ip, ipaddress.IPv4Address):
        # 1. Tailscale CGNAT range (100.64.0.0/10) - ALWAYS remote, never LAN
        if ip in TAILSCALE_NETWORK:
            return "REMOTE_TAILSCALE"

        # 2. Virtual/fake-ip subnets (Clash utun) - Always remote
        if ip in CLASH_FAKE_NETWORK:
            return "REMOTE_PUBLIC"

        # 3. Direct physical LAN subnets
        # If forwarded by an external proxy, cannot be directly trusted as physical LAN without verification
        if not is_proxy_forwarded:
            physical_nets = get_active_physical_lan_networks()
            for net in physical_nets:
                if ip in net:
                    return "DIRECT_PHYSICAL_LAN"

        # 4. Any other non-physical address (public, alien private LAN, foreign Wi-Fi, 4G/5G)
        return "REMOTE_PUBLIC"

    # IPv6 Classification
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.is_loopback:
            return "LOCAL_LOOPBACK"
        # Tailscale IPv6 prefix fd7a:115c:a1e0::/48
        try:
            ts_v6 = ipaddress.IPv6Network("fd7a:115c:a1e0::/48")
            if ip in ts_v6:
                return "REMOTE_TAILSCALE"
        except Exception:
            pass

        # PRD 5.1 & AC-31: ULA (fc00::/7) or link-local must NOT be blindly accepted as physical LAN
        return "REMOTE_PUBLIC"

    return "UNKNOWN"
