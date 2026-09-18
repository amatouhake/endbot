# Endbot runtime

This package is the boundary for the external headless Bedrock client. M1 contains only the proven local-identity
producer: an explicitly persisted hidden UUID, a separate user-visible bot name, short-lived ES384 identity tokens,
and client-public-key binding. The UUID is never derived from the name, so a later rename can retain persistent
relationships. It intentionally does not yet contain transport, reconnect, movement, actions, macros, or tasks.

Private keys and bot UUIDs are generated at runtime and stored outside source control. Their shared load-or-create path
publishes exactly one candidate with Node's cross-platform exclusive-copy operation; concurrent losers discard their
candidates and reload the persisted winner. Existing corrupt files and symlinks fail closed. Files are forced to mode
`0600` on POSIX systems; on Windows, access control is provided by the native filesystem ACLs instead of POSIX modes.
Exclusive creation depends on the filesystem honoring Node's create-if-absent contract, so local disks are recommended
over network filesystems with weak or inconsistent exclusive-create semantics.

```bash
npm test --prefix runtime
```
