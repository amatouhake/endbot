# Endstone patch series

`series` is the ordered, reviewable Endbot delta from the exact upstream commit in `endstone.lock`.

- `0001-feat-auth-add-local-ownerbot-login-trust.patch` adds disabled-by-default configuration, the ES384 local identity
  verifier, the narrow login hook, and required crypto build dependency.
- `0002-test-auth-cover-local-ownerbot-verification.patch` adds verifier unit coverage for acceptance, replay, disabled and
  malformed inputs, owner signature, issuer/expiry, client-key/name binding, and safe observation of a missing raw
  request token.

The unrelated changes from the former validation branch are intentionally absent. Apply patches only through
`scripts/prepare_endstone.py`; do not edit a cached upstream checkout or make a fork a build prerequisite.
