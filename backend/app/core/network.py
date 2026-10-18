"""The laptop's LAN address, so a phone on the same Wi-Fi can open the app. Nothing here leaves the machine."""
import ipaddress
import socket

from backend.app.core.config import Settings, get_settings

DEFAULT_FRONTEND_PORT = 5173


def _usable(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def detect_lan_ip(settings: Settings | None = None) -> str | None:
    """The IPv4 of the interface that carries the default route.

    ``LAN_IP`` (set by scripts/start-demo.ps1, which skips virtual adapters by name) wins. Otherwise a UDP socket is
    "connected" to a private address: the OS picks the outgoing interface and no packet is sent.
    """
    configured = (settings or get_settings()).lan_ip.strip()
    if configured:
        return configured if _usable(configured) else None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.254.254.254", 9))
            address = probe.getsockname()[0]
    except OSError:
        return None
    return address if _usable(address) else None


def phone_url(scheme: str | None = None, port: int | str | None = None, settings: Settings | None = None) -> str | None:
    """Where a phone opens the app, e.g. https://192.168.1.20:5173/#/capture; None when no LAN address is known.

    ``PHONE_URL`` overrides everything. Otherwise the scheme and port are those the laptop's browser used to reach the
    dev server (the proxy forwards them), falling back to https on 5173.
    """
    active = settings or get_settings()
    if active.phone_url.strip():
        return active.phone_url.strip()
    address = detect_lan_ip(active)
    if address is None:
        return None
    return f"{scheme if scheme in ('http', 'https') else 'https'}://{address}:{port or DEFAULT_FRONTEND_PORT}/#/capture"
