// dist/bundle: the prebuilt Worker, UI assets and manifest that `endbot web deploy` uploads. Operators need no Node.
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const run = (command) => execFileSync(command, { cwd: root, stdio: "inherit", shell: true });

run("npx vite build");
run("npx esbuild src/worker/index.ts --bundle --format=esm --target=es2022 --external:cloudflare:workers "
  + "--outfile=dist/worker/index.js");
mkdirSync(resolve(root, "dist/bundle/worker"), { recursive: true });
copyFileSync(resolve(root, "dist/worker/index.js"), resolve(root, "dist/bundle/worker/index.js"));
const wrangler = readFileSync(resolve(root, "wrangler.jsonc"), "utf8");
const schema = JSON.parse(readFileSync(resolve(root, "../core/src/endbotd/web_protocol.schema.json"), "utf8"));
writeFileSync(resolve(root, "dist/bundle/manifest.json"), `${JSON.stringify({
  protocol: schema.version,
  compatibilityDate: /"compatibility_date":\s*"([^"]+)"/.exec(wrangler)[1],
}, null, 2)}\n`);
