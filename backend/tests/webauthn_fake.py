"""A WebAuthn authenticator in forty lines: a real P-256 key, real signatures.

Registration produces a "none" attestation; authentication signs
authenticatorData || SHA-256(clientDataJSON) exactly as a device would. This is
what lets the passkey tests run the unpatched library rather than a mock of
it, so a wrong origin string or a mis-encoded key fails here, not in
production.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

_UP = 0x01
_AT = 0x40


class FakeAuthenticator:
    def __init__(self, rp_id: str, origin: str) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)

    # --- pieces -----------------------------------------------------------

    def _cose_key(self) -> bytes:
        n = self._key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        )

    def _auth_data(self, flags: int, count: int, *, attested: bool) -> bytes:
        out = (
            hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", count)
        )
        if attested:
            out += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id
            out += self._cose_key()
        return out

    def _client_data(self, kind: str, challenge_b64: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge_b64, "origin": self.origin, "crossOrigin": False}
        ).encode()

    # --- ceremonies --------------------------------------------------------

    def register(self, options: dict, *, transports: tuple[str, ...] = ("internal",)) -> dict:
        """What ``navigator.credentials.create`` hands back, as JSON."""
        att = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": self._auth_data(_UP | _AT, 0, attested=True)}
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self._client_data("webauthn.create", options["challenge"])
                ),
                "attestationObject": bytes_to_base64url(att),
                "transports": list(transports),
            },
            "clientExtensionResults": {},
        }

    def assert_(self, options: dict, *, count: int) -> dict:
        """What ``navigator.credentials.get`` hands back, as JSON."""
        auth_data = self._auth_data(_UP, count, attested=False)
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = self._key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }


def challenge_of(options: dict) -> bytes:
    return base64url_to_bytes(options["challenge"])


__all__ = ["FakeAuthenticator", "challenge_of"]
