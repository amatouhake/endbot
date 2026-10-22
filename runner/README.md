# Standalone Luau runner spike

Build/test tooling only; nothing starts from the Endbot plugin or Bedrock runtime.
Requires Node >=24, CMake >=3.15, a C/C++17 toolchain and gzip-capable `tar`.

```sh
cd runner
node build.mjs
node --test
node demo.mjs
# Or supply a source file and a JSON args object:
node demo.mjs examples/interleaved.luau '{"first":"One","second":"Two","ticks":3}'
```

No npm dependencies or Python required. Downloads and binaries stay in ignored `.deps/` and `.build/`.
See [the research, protocol, limits and exact validation record](../docs/LUAU_RUNNER_SPIKE.md)
for Windows generator instructions and unverified production gates.
