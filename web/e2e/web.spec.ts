// Browser → real Worker and Durable Object in workerd → real endbotd, with a virtual authenticator (docs/WEB.md step 5).
import { type ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import { createHash, createPrivateKey, createPublicKey } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";

const PORT = 8787;
const ORIGIN = `http://localhost:${PORT}`;
const SHOTS = process.env.E2E_SCREENSHOTS;
const PYTHON = process.env.E2E_PYTHON ?? "python";

let worker: ChildProcessWithoutNullStreams;
let endbotd: ChildProcessWithoutNullStreams;
let answers: AsyncIterableIterator<string>;

async function ask(command: string): Promise<any> {
  endbotd.stdin.write(`${command}\n`);
  const { value } = await answers.next();
  return JSON.parse(value);
}

function publicKeySecret(): string {
  // The server script's fixed test key: raw public key and the same id endbotd derives.
  const der = Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from([...Array(32).keys()])]);
  const key = createPublicKey(createPrivateKey({ key: der, format: "der", type: "pkcs8" }));
  const raw = key.export({ format: "der", type: "spki" }).subarray(-32);
  return JSON.stringify({ key: raw.toString("base64url"), keyId: createHash("sha256").update(raw).digest("hex").slice(0, 16) });
}

async function waitFor(url: string): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      if ((await fetch(url)).ok) return;
    } catch { /* not up yet */ }
    await new Promise((done) => setTimeout(done, 250));
  }
  throw new Error(`${url} did not come up`);
}

test.beforeAll(async () => {
  test.setTimeout(120_000);
  const state = mkdtempSync(join(tmpdir(), "endbot-e2e-"));
  const devVars = join(state, ".dev.vars");
  writeFileSync(devVars, `ENDBOT_ORIGIN=${ORIGIN}\nENDBOTD_LINK_KEY='${publicKeySecret()}'\nAUTH_BUCKET_PER_MINUTE=1000\n`);
  worker = spawn("npx", ["wrangler", "dev", "--config", "wrangler.e2e.jsonc", "--port", String(PORT), "--env-file", devVars,
    "--persist-to", join(state, "wrangler"), "--show-interactive-dev-session=false"],
  { cwd: resolve("."), shell: true, env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "true" } });
  worker.stderr.on("data", (data) => process.stderr.write(data));
  await waitFor(`${ORIGIN}/api/display`);
  endbotd = spawn(PYTHON, ["e2e/server.py", ORIGIN, join(state, "web-link.key")], { cwd: resolve(".") });
  endbotd.stderr.on("data", (data) => process.stderr.write(data));
  answers = createInterface({ input: endbotd.stdout })[Symbol.asyncIterator]();
  expect(JSON.parse((await answers.next()).value).ready).toBe(true);
  // The display cache arrives with the link.
  await expect.poll(async () => (await (await fetch(`${ORIGIN}/api/display`)).json()).displayName, { timeout: 30_000 })
    .toBe("E2E server");
});

test.afterAll(() => {
  endbotd?.stdin.end();
  endbotd?.kill();
  if (worker?.pid) {
    // A leftover Worker or endbotd would answer the next run, so wait for them to go.
    if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(worker.pid), "/T", "/F"]);
    else worker.kill();
  }
});

async function withAuthenticator(context: BrowserContext, page: Page): Promise<string> {
  const session = await context.newCDPSession(page);
  await session.send("WebAuthn.enable");
  const { authenticatorId } = await session.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true,
      isUserVerified: true, automaticPresenceSimulation: true },
  });
  return authenticatorId;
}

async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

async function link(page: Page): Promise<void> {
  const { code } = await ask("code");
  await page.getByLabel(/Link code|リンクコード/).fill(code);
  await page.getByRole("button", { name: /Continue|次へ/ }).click();
  await expect(page.getByRole("button", { name: /Sign out|サインアウト/ })).toBeVisible();
}

test.describe.configure({ mode: "serial" });

// One player for the whole file: without this they would reach the passkey limit.
test.beforeEach(async () => {
  await ask("reset");
});

test("a code links a passkey, the session survives a reload, and a passkey signs in again", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "E2E server" })).toBeVisible();
  await shot(page, "01-sign-in");
  await link(page);
  await expect(page.getByText("Signed in as Alice")).toBeVisible();
  await expect(page.getByText("Miner_01")).toBeVisible();
  await shot(page, "02-status");

  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === "__Host-endbot_session")!;
  expect(session.httpOnly).toBe(true);
  expect(session.secure).toBe(true);
  expect(session.sameSite).toBe("Strict");
  expect(await page.evaluate(() => document.cookie)).not.toContain("endbot_session");

  await page.reload();
  await expect(page.getByText("Signed in as Alice")).toBeVisible();

  await ask("log");
  await page.getByRole("button", { name: "Log" }).click();
  await expect(page.getByText("layer 3 done, moving down")).toBeVisible();
  await shot(page, "03-log");

  await page.getByRole("navigation").getByRole("button", { name: "Devices" }).click();
  await expect(page.getByRole("heading", { name: "Passkeys" })).toBeVisible();
  await expect(page.getByText("Type /botweb link in the game")).toBeVisible();
  await shot(page, "04-devices");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible();
  expect((await context.cookies()).find((cookie) => cookie.name === "__Host-endbot_session")).toBeUndefined();

  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(page.getByText("Signed in as Alice")).toBeVisible();
});

test("a second device joins with a code from the game, and the first page hears about it", async ({ browser, page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
  await page.getByRole("navigation").getByRole("button", { name: "Devices" }).click();
  const { code } = await ask("code");

  const other = await browser.newContext({ baseURL: ORIGIN, locale: "en-US" });
  const second = await other.newPage();
  await withAuthenticator(other, second);
  await second.goto("/");
  await second.getByLabel("Link code").fill(code);
  await second.getByRole("button", { name: "Continue" }).click();
  await expect(second.getByText("Signed in as Alice")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("passkey was just added");
  await other.close();
});

test("a session ended elsewhere leaves nothing of the account on the page", async ({ browser, page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
  await page.getByRole("navigation").getByRole("button", { name: "Devices" }).click();
  await expect(page.getByRole("heading", { name: "Passkeys" })).toBeVisible();

  const other = await browser.newContext({ baseURL: ORIGIN, locale: "en-US" });
  const second = await other.newPage();
  await withAuthenticator(other, second);
  await second.goto("/");
  await link(second);
  await second.getByRole("navigation").getByRole("button", { name: "Devices" }).click();
  const row = second.getByRole("listitem").filter({ has: second.getByRole("button", { name: "Remove" }) }).last();
  await row.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible({ timeout: 15_000 });
  await other.close();

  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(page.getByText("Signed in as Alice")).toBeVisible();
  // Back on the first tab, with the earlier account's device list gone until asked for again.
  await expect(page.getByRole("heading", { name: "Bots" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Passkeys" })).toHaveCount(0);
});

test("a code needs no hyphen, however it is typed or pasted", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  const field = page.getByLabel("Link code");
  const continueButton = page.getByRole("button", { name: "Continue" });
  await field.pressSequentially("k7q");
  await expect(field).toHaveValue("K7Q");
  await expect(continueButton).toBeDisabled();
  await field.pressSequentially("4m2");
  await expect(field).toHaveValue("K7Q-4M2");
  await expect(continueButton).toBeEnabled();
  await field.fill(" k7q 4m2 extra ");
  await expect(field).toHaveValue("K7Q-4M2");
  const { code } = await ask("code");
  await field.fill(code.replace("-", ""));
  await continueButton.click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("the page has its icon, and choosing a tab moves nothing", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  const icon = await page.request.get(await page.locator('link[rel="icon"]').evaluate((link: HTMLLinkElement) => link.href));
  expect(icon.headers()["content-type"]).toContain("image/png");
  await link(page);
  const positions = async () => page.getByRole("navigation").getByRole("button").evaluateAll(
    (buttons) => buttons.map((button) => { const box = button.getBoundingClientRect(); return [box.left, box.width]; }));
  const first = await positions();
  for (const name of ["Bots", "Scripts", "Log", "Devices", "Status"]) {
    await page.getByRole("navigation").getByRole("button", { name, exact: true }).click();
    expect(await positions()).toEqual(first);
  }
});

test("a Bot is controlled from forms built from the registry", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
  await page.getByRole("navigation").getByRole("button", { name: "Bots" }).click();
  await page.getByRole("button", { name: /Miner_01/ }).click();
  await shot(page, "11-bots");
  await page.getByLabel("direction").selectOption("forward");
  await page.getByLabel(/ticks/).fill("20");
  await page.getByRole("button", { name: "Run move" }).click();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect.poll(async () => JSON.stringify(await ask("calls"))).toContain("forward");
  await shot(page, "12-bots-result");
  // Forget is the one that asks first: declining sends nothing.
  await page.getByRole("button", { name: "Bot", exact: true }).click();
  await page.getByRole("button", { name: "forget", exact: true }).click();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Run forget" }).click();
  expect(JSON.stringify(await ask("calls"))).not.toContain('"forget"');
});

test("a script is written, checked, saved, compared and run", async ({ page, context }) => {
  test.skip(!process.env.E2E_RUNNER, "needs a built Luau runner in E2E_RUNNER");
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
  await page.getByRole("navigation").getByRole("button", { name: "Scripts" }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByPlaceholder("my_script").fill("e2e_script");
  const editor = page.getByRole("textbox", { name: "Script source" });
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type('params {\n\n"not valid"');
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await shot(page, "13-script-error");
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.insertText('local a = params { param.number("count", 2) }\nlog("count " .. a.count)\n');
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByRole("status")).toContainText("No problems");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status")).toContainText("Saved");
  await expect(page.getByRole("button", { name: /e2e_script/ })).toBeVisible();

  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText('log("second")\n');
  await expect(page.getByText("unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Earlier revisions" })).toBeVisible();
  await page.getByRole("button", { name: "Compare" }).click();
  await expect(page.locator(".diff .added")).toContainText('log("second")');
  await shot(page, "14-script-history");

  await page.getByRole("button", { name: "Run..." }).click();
  await expect(page.getByLabel("count")).toHaveValue("2");
  await page.getByLabel("count").fill("7");
  await page.getByRole("button", { name: "Start" }).click();
  await page.getByRole("navigation").getByRole("button", { name: "Log" }).click();
  await expect(page.getByText("count 7")).toBeVisible({ timeout: 15_000 });
  await shot(page, "15-script-log");
});

test("a wrong code says so", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await page.getByLabel("Link code").fill("AAAAAA");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("not right");
  await shot(page, "05-wrong-code");
});

test("the layout fits a phone with no sideways scrolling", async ({ page, context }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  await withAuthenticator(context, page);
  await page.goto("/");
  await shot(page, "06-phone-sign-in");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await link(page);
  await expect(page.getByText("Miner_01")).toBeVisible();
  await shot(page, "07-phone-status");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("navigation").getByRole("button", { name: "Devices" }).click();
  await expect(page.getByRole("heading", { name: "Passkeys" })).toBeVisible();
  await shot(page, "08-phone-devices");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("Japanese follows the browser", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: ORIGIN, locale: "ja-JP" });
  const page = await context.newPage();
  await withAuthenticator(context, page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "パスキーでサインイン" })).toBeVisible();
  await shot(page, "09-ja-sign-in");
  await link(page);
  await expect(page.getByText("Alice としてサインイン中")).toBeVisible();
  await shot(page, "10-ja-status");
  await context.close();
});

test("the page keeps to its Content-Security-Policy", async ({ page, context }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) violations.push(message.text());
  });
  await withAuthenticator(context, page);
  const response = await page.goto("/");
  expect(response!.headers()["content-security-policy"]).toContain("default-src 'self'");
  await expect(page.getByRole("heading", { name: "E2E server" })).toBeVisible();
  await link(page);
  for (const name of ["Bots", "Scripts", "Log", "Devices", "Status"]) {
    await page.getByRole("navigation").getByRole("button", { name }).click();
  }
  await page.getByRole("navigation").getByRole("button", { name: "Scripts" }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Script source" })).toBeVisible();
  expect(violations).toEqual([]);
});

test("revoking the grant ends an open page at once", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
  await ask("revoke");
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});
