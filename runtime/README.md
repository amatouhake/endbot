# Endbot runtime

This package is the boundary for the external headless Bedrock client. M1 contains only the proven local-identity
producer: an explicitly persisted hidden UUID, a separate user-visible bot name, short-lived ES384 identity tokens,
and client-public-key binding. The UUID is never derived from the name, so a later rename can retain persistent
relationships. It intentionally does not yet contain transport, reconnect, movement, actions, macros, or tasks.

Private keys are generated at runtime, stored outside source control, and forced to mode `0600` on POSIX systems.

```bash
npm test --prefix runtime
```
