"""A software WebAuthn authenticator for tests: ES256, attestation none, resident credentials.

It signs what a real authenticator would, so a test can vary exactly one field an
attacker controls (origin, RP ID, challenge, flags, counter) and see it refused.
"""

import base64
import hashlib
import json
import secrets

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, AT = 0x01, 0x04, 0x40


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class SoftAuthenticator:
    def __init__(self, origin: str, rp_id: str) -> None:
        self.origin, self.rp_id = origin, rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.count = 0

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    def register(self, options: dict, *, origin=None, rp_id=None, kind="webauthn.create", flags=UP | UV | AT,
                 challenge=None) -> dict:
        client_data = self._client_data(kind, challenge or options["challenge"], origin)
        data = (hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([flags])
                + self.count.to_bytes(4, "big") + bytes(16) + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id + self._cose_key())
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "attestationObject": b64(attestation),
                             "transports": ["internal"]}}

    def assert_(self, options: dict, *, origin=None, rp_id=None, kind="webauthn.get", flags=UP | UV, challenge=None,
                count=None, key=None) -> dict:
        self.count = self.count + 1 if count is None else count
        client_data = self._client_data(kind, challenge or options["challenge"], origin)
        data = (hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([flags])
                + self.count.to_bytes(4, "big"))
        signature = (key or self.key).sign(data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(data),
                             "signature": b64(signature), "userHandle": None}}
