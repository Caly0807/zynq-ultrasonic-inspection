import base64
import binascii
import hmac
import ipaddress
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


def private_address(value):
    address = ipaddress.IPv4Address(value)
    if not address.is_private or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError("Use a specific private IPv4 address, not a wildcard or link-local address")
    return str(address)


def load_token(path):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".ut-credential-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    if destination.is_symlink():
        raise ValueError("The credential must be a regular non-symlink file")
    descriptor = os.open(destination, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "r", encoding="ascii") as stream:
        information = os.fstat(stream.fileno())
        if not stat.S_ISREG(information.st_mode):
            raise ValueError("The credential must be a regular file")
        if os.name != "nt" and ((stat.S_IMODE(information.st_mode) & 0o077) or information.st_uid != os.geteuid()):
            raise ValueError("Credential must be owned by this service user with permissions 0600")
        token = stream.read(256).strip()
    if not 32 <= len(token) <= 128 or any(not character.isascii() or not (character.isalnum() or character in "-_") for character in token):
        raise ValueError("Credential must contain 32..128 ASCII letters, digits, hyphens or underscores")
    return token


@dataclass(frozen=True)
class NetworkAccess:
    address: str
    peer: str
    token: str

    def __post_init__(self):
        private_address(self.address)
        private_address(self.peer)
        if not 32 <= len(self.token) <= 128 or any(not character.isascii() or not (character.isalnum() or character in "-_") for character in self.token):
            raise ValueError("Use a strong per-device credential")

    def allowed(self, handler):
        if handler.client_address[0] != self.peer:
            return False
        authority = f"{self.address}:{handler.server.server_address[1]}"
        if handler.headers.get_all("Host", []) != [authority]:
            return False
        origins = handler.headers.get_all("Origin", [])
        if origins and origins != ["http://" + authority]:
            return False
        return handler.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def authenticated(self, handler):
        authorizations = handler.headers.get_all("Authorization", [])
        if len(authorizations) != 1:
            return False
        scheme, separator, credentials = authorizations[0].partition(" ")
        if not separator or scheme.lower() != "basic":
            return False
        try:
            supplied = base64.b64decode(credentials, validate=True)
        except (ValueError, binascii.Error):
            return False
        return hmac.compare_digest(supplied, ("operator:" + self.token).encode("ascii"))
