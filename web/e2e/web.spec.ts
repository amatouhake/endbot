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
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

function signedIn(page: Page) {
  return page.locator(".account summary");
}

async function link(page: Page): Promise<void> {
  const { code } = await ask("code");
  await page.getByLabel(/Link code|リンクコード/).fill(code);
  await page.getByRole("button", { name: /Continue|次へ/ }).click();
  await expect(signedIn(page)).toBeVisible();
}

async function start(page: Page, context: BrowserContext): Promise<void> {
  await withAuthenticator(context, page);
  await page.goto("/");
  await link(page);
}

async function tab(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation").getByRole("link", { name, exact: true }).click();
}

async function devices(page: Page): Promise<void> {
  await signedIn(page).click();
  await page.getByRole("link", { name: "Devices" }).click();
  await expect(page.getByRole("heading", { name: "Passkeys" })).toBeVisible();
}

async function fitsTheScreen(page: Page): Promise<void> {
  expect(await page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth <= window.innerWidth && root.scrollHeight <= window.innerHeight;
  })).toBe(true);
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
  await expect(signedIn(page)).toContainText("Alice");
  await expect(page.getByRole("link", { name: /Miner_01/ })).toBeVisible();
  await shot(page, "02-bots");

  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === "__Host-endbot_session")!;
  expect(session.httpOnly).toBe(true);
  expect(session.secure).toBe(true);
  expect(session.sameSite).toBe("Strict");
  expect(await page.evaluate(() => document.cookie)).not.toContain("endbot_session");

  await page.reload();
  await expect(signedIn(page)).toContainText("Alice");

  await ask("log");
  await tab(page, "History");
  await expect(page.getByText("layer 3 done, moving down")).toBeVisible();
  await shot(page, "03-history");

  await devices(page);
  await expect(page.getByText("Type /botweb link in the game")).toBeVisible();
  await shot(page, "04-devices");

  await signedIn(page).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible();
  expect((await context.cookies()).find((cookie) => cookie.name === "__Host-endbot_session")).toBeUndefined();

  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(signedIn(page)).toContainText("Alice");
});

test("a second device joins with a code from the game, and the first page hears about it", async ({ browser, page, context }) => {
  await start(page, context);
  const { code } = await ask("code");

  const other = await browser.newContext({ baseURL: ORIGIN, locale: "en-US" });
  const second = await other.newPage();
  await withAuthenticator(other, second);
  await second.goto("/");
  await second.getByLabel("Link code").fill(code);
  await second.getByRole("button", { name: "Continue" }).click();
  await expect(signedIn(second)).toContainText("Alice");
  await expect(page.getByRole("alert")).toContainText("passkey was just added");
  await other.close();
});

test("a session ended elsewhere leaves nothing of the account on the page", async ({ browser, page, context }) => {
  await start(page, context);
  await devices(page);

  const other = await browser.newContext({ baseURL: ORIGIN, locale: "en-US" });
  const second = await other.newPage();
  await start(second, other);
  await devices(second);
  const row = second.getByRole("listitem").filter({ has: second.getByRole("button", { name: "Remove" }) }).last();
  await row.getByRole("button", { name: "Remove" }).click();
  // Asked on the page itself, and the page's own dialog is what carries it out.
  await second.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible({ timeout: 15_000 });
  await other.close();

  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(signedIn(page)).toContainText("Alice");
  // Back on the first tab, with the earlier account's device list gone until asked for again.
  await expect(page).toHaveURL(/\/bots$/);
  await expect(page.getByRole("heading", { name: "Passkeys" })).toHaveCount(0);
});

test("a code needs no hyphen, and editing one keeps the cursor where it was", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  const field = page.getByLabel("Link code");
  const continueButton = page.getByRole("button", { name: "Continue" });
  await field.pressSequentially("k7q");
  // The hyphen is there as soon as the third symbol is.
  await expect(field).toHaveValue("K7Q-");
  await page.keyboard.press("Backspace");
  await expect(field).toHaveValue("K7Q");
  await expect(continueButton).toBeDisabled();
  await field.pressSequentially("4m2");
  await expect(field).toHaveValue("K7Q-4M2");
  await expect(continueButton).toBeEnabled();
  await field.evaluate((input: HTMLInputElement) => input.setSelectionRange(1, 1));
  await page.keyboard.press("Delete");
  await expect(field).toHaveValue("KQ4-M2");
  expect(await field.evaluate((input: HTMLInputElement) => input.selectionStart)).toBe(1);
  await page.keyboard.type("7");
  await expect(field).toHaveValue("K7Q-4M2");
  expect(await field.evaluate((input: HTMLInputElement) => input.selectionStart)).toBe(2);
  await field.fill(" k7q 4m2 extra ");
  await expect(field).toHaveValue("K7Q-4M2");
  const { code } = await ask("code");
  await field.fill(code.replace("-", ""));
  await continueButton.click();
  await expect(signedIn(page)).toBeVisible();
});

test("the page has its icon, choosing a tab moves nothing, and the page itself never scrolls", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  const icon = await page.request.get(await page.locator('link[rel="icon"]').evaluate((link: HTMLLinkElement) => link.href));
  expect(icon.headers()["content-type"]).toContain("image/png");
  await link(page);
  // The chosen tab differs by colour and underline only: the weight of its letters stays, so nothing looks shifted.
  const positions = async () => page.getByRole("navigation").getByRole("link").evaluateAll(
    (links) => links.map((link) => { const box = link.getBoundingClientRect(); return [box.left, box.width, getComputedStyle(link).fontWeight]; }));
  const first = await positions();
  for (const name of ["Scripts", "Groups", "History", "Bots"]) {
    await tab(page, name);
    expect(await positions()).toEqual(first);
    await fitsTheScreen(page);
  }
});

test("the address keeps what was chosen, through a reload and the browser's Back", async ({ page, context }) => {
  await start(page, context);
  await page.getByRole("link", { name: /Miner_01/ }).click();
  await expect(page).toHaveURL(/\/bots\/Miner_01$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Miner_01" })).toBeVisible();
  await tab(page, "Groups");
  await expect(page).toHaveURL(/\/groups$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/bots\/Miner_01$/);
  await expect(page.getByRole("heading", { name: "Miner_01" })).toBeVisible();
  // A tab comes back to what it showed.
  await tab(page, "History");
  await tab(page, "Bots");
  await expect(page).toHaveURL(/\/bots\/Miner_01$/);
  await page.goto("/history");
  await expect(page.getByRole("navigation").getByRole("link", { name: "History" })).toHaveAttribute("aria-current", "page");
});

test("a Bot's state is laid out, and every operation is two clicks away", async ({ page, context }) => {
  await start(page, context);
  await page.getByRole("link", { name: /Miner_01/ }).click();
  await expect(page.getByText("12.5, 64, -3")).toBeVisible();
  await expect(page.getByText("18 / 20")).toBeVisible();
  const category = (name: string) => page.getByRole("group", { name: "Categories" }).getByRole("button", { name, exact: true });
  const operations = page.locator(".choices.operations");
  const panel = page.locator(".op-panel");
  await page.getByRole("button", { name: "forward" }).click();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect.poll(async () => JSON.stringify(await ask("calls"))).toContain("forward");
  await shot(page, "11-bot");

  // Modes are buttons: one with nothing to ask runs at once, as on the in-game menu.
  await category("Act").click();
  await operations.getByRole("button", { name: "attack" }).click();
  await panel.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.locator(".results")).not.toBeEmpty();
  // A mode with clauses asks only for its own: `once` has no duration, and `interval` must have its ticks.
  await panel.getByRole("button", { name: "At intervals" }).click();
  await expect(panel.getByLabel(/^interval/)).toHaveAttribute("required");
  await panel.getByLabel(/^interval/).fill("40");
  await panel.getByLabel(/^ticks/).fill("80");
  await shot(page, "12-bot-form");
  // Run stays on screen with the fields filled in.
  await expect(panel.getByRole("button", { name: "Run attack" })).toBeInViewport({ ratio: 1 });
  await panel.getByRole("button", { name: "Run attack" }).click();
  await expect(page.locator(".results li").first()).not.toHaveClass(/failed/);
  await tab(page, "History");
  await expect(page.getByText("attack interval 40 for 80").locator("visible=true").first()).toBeVisible();
  await tab(page, "Bots");

  // What sent nothing but a form with a free-text box before.
  await category("Move").click();
  await operations.getByRole("button", { name: "sprint" }).click();
  await panel.getByRole("button", { name: "On", exact: true }).click();
  await tab(page, "History");
  await expect(page.getByText("sprint on").locator("visible=true").first()).toBeVisible();
  await tab(page, "Bots");

  await category("Teleport").click();
  await expect(panel.getByRole("button", { name: "To my position" })).toBeVisible();
  await panel.getByRole("button", { name: "x y z" }).click();
  await expect(panel.getByLabel(/^dimension/).locator("option")).toHaveText(["—", "overworld", "nether", "end"]);
  // The in-game forms leave facing to the typed command.
  await expect(panel.getByLabel(/^facing/)).toHaveCount(0);
  await panel.getByRole("button", { name: "To my position" }).click();
  await tab(page, "History");
  await expect(page.getByText("tp Alice").locator("visible=true").first()).toBeVisible();
  await tab(page, "Bots");

  // Forget is the one that asks first: declining sends nothing.
  await category("Bot").click();
  await operations.getByRole("button", { name: "forget" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  expect(JSON.stringify(await ask("calls"))).not.toContain('"forget"');
});

test("what was used comes back under Recent, the category is remembered, and an offline Bot offers what it can do", async ({ page, context }) => {
  await start(page, context);
  await page.getByRole("link", { name: /Miner_01/ }).click();
  const recent = page.getByRole("group", { name: "Recent" });
  await expect(recent).toHaveCount(0);
  await page.getByRole("group", { name: "Categories" }).getByRole("button", { name: "Info", exact: true }).click();
  await page.locator(".choices.operations").getByRole("button", { name: "status" }).click();
  await expect(recent.getByRole("button", { name: "status" })).toBeVisible();
  await shot(page, "19-recent");
  await page.reload();
  await expect(recent.getByRole("button", { name: "status" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Categories" }).getByRole("button", { name: "Info", exact: true }))
    .toHaveAttribute("aria-pressed", "true");
  await page.evaluate(() => localStorage.setItem("endbot.opCategory", "not a category"));
  await page.evaluate(() => localStorage.setItem("endbot.recentOps", "{broken"));
  await page.reload();
  await expect(page.getByRole("group", { name: "Categories" }).getByRole("button", { name: "Move", exact: true }))
    .toHaveAttribute("aria-pressed", "true");
  await expect(recent).toHaveCount(0);
  // Offline, only the Bot section has anything to offer, as in the in-game menu.
  await page.getByRole("link", { name: /Farmer/ }).click();
  await expect(page.getByRole("group", { name: "Categories" }).getByRole("button")).toHaveText(["Bot"]);
  await expect(page.locator(".choices.operations").getByRole("button", { name: "reconnect" })).toHaveCount(0);
  await shot(page, "20-offline");
});

test("the list can be searched and filtered, and follows what happens elsewhere by itself", async ({ page, context }) => {
  await start(page, context);
  const list = page.locator(".list-scroll");
  await expect(list.getByRole("link")).toHaveText([/Miner_01/, /Farmer/]);
  await page.getByLabel("Sort").selectOption("name");
  await expect(list.getByRole("link")).toHaveText([/Farmer/, /Miner_01/]);
  await page.getByLabel("Search").fill("min");
  await expect(list.getByRole("link")).toHaveText([/Miner_01/]);
  await page.getByLabel("Search").fill("");
  await page.getByLabel("State").selectOption("online");
  await expect(list.getByRole("link")).toHaveText([/Miner_01/]);
  // Nobody touches this page: the Bot simply comes online.
  await ask("online Farmer");
  await expect(list.getByRole("link")).toHaveText([/Farmer/, /Miner_01/], { timeout: 10_000 });
});

test("Groups follow the Bots in the list and offer only what a Group can do", async ({ page, context }) => {
  await start(page, context);
  await tab(page, "Groups");
  await page.locator(".list-tools").getByRole("button", { name: "New", exact: true }).locator("visible=true").click();
  await page.getByRole("dialog").getByLabel("New Group name").fill("crew");
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "#crew" })).toBeVisible();
  await tab(page, "Bots");
  const list = page.locator(".list-scroll").locator("visible=true");
  // Bots first, then Groups, whatever the earlier tests left online.
  await expect(list.getByRole("link")).toHaveText([/Farmer/, /Miner_01/, /#crew/]);
  await page.getByLabel("State").selectOption("groups");
  await expect(list.getByRole("link")).toHaveText([/#crew/]);
  await page.getByLabel("State").selectOption("online");
  await expect(list.getByRole("link")).toHaveText([/Farmer/, /Miner_01/]);
  await page.getByLabel("State").selectOption("all");
  await page.getByLabel("Search").locator("visible=true").fill("#cr");
  await expect(list.getByRole("link")).toHaveText([/#crew/]);
  await list.getByRole("link").click();
  await page.getByRole("group", { name: "Categories" }).getByRole("button", { name: "Bot", exact: true }).click();
  const offered = page.locator(".choices.operations");
  await expect(offered.getByRole("button", { name: "visibility" })).toBeVisible();
  await expect(offered.getByRole("button", { name: "forget" })).toHaveCount(0);
  await expect(offered.getByRole("button", { name: "rename" })).toHaveCount(0);
  await page.getByRole("link", { name: "Manage this Group" }).click();
  await page.getByRole("button", { name: "Delete Group" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await tab(page, "Bots");
  await expect(page.getByText("This Group no longer exists")).toBeVisible();
});

test("New is in one place on all three lists, and a refused name stays in its dialog", async ({ page, context }) => {
  await start(page, context);
  const search = page.getByLabel("Search").locator("visible=true");
  const button = page.locator(".list-tools").getByRole("button", { name: "New", exact: true }).locator("visible=true");
  const place = async () => [(await search.boundingBox())!.y, (await button.boundingBox())!.y, (await button.boundingBox())!.x];
  const bots = await place();
  await tab(page, "Groups");
  expect(await place()).toEqual(bots);
  await tab(page, "Scripts");
  expect(await place()).toEqual(bots);

  await tab(page, "Groups");
  await button.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("New Group name").fill("1 bad");
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Use up to 16");
  await expect(dialog.getByLabel("New Group name")).toHaveValue("1 bad");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(await page.locator(".list-scroll").locator("visible=true").getByRole("link").count()).toBe(0);

  // A script is not created until it is saved, and the list and the button both say so.
  await tab(page, "Scripts");
  await button.click();
  await expect(page.getByText("New script (not saved)")).toBeVisible();
  await expect(page.locator(".script-bar").getByRole("button", { name: "Create" })).toBeVisible();
  await expect(page).toHaveURL(/\/scripts$/);
  await shot(page, "17-script-draft");
  await page.locator(".script-bar").getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("New script (not saved)")).toHaveCount(0);
});

test("a Group is created, filled, operated as a whole and deleted, and all of it is in the history", async ({ page, context }) => {
  await start(page, context);
  await tab(page, "Groups");
  await page.locator(".list-tools").getByRole("button", { name: "New", exact: true }).locator("visible=true").click();
  await page.getByRole("dialog").getByLabel("New Group name").fill("miners");
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "#miners" })).toBeVisible();
  await expect(page).toHaveURL(/\/groups\/miners$/);
  await page.getByLabel("Bot to add").selectOption("Miner_01");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("button", { name: "Take out" })).toBeVisible();
  await expect(page.getByRole("link", { name: /#miners/ })).toContainText("Members: 1");
  await shot(page, "16-group");

  // A Group is operated where Bots are, as in the game; managing it stays on its own tab.
  await page.getByRole("link", { name: "Operate this Group" }).click();
  await expect(page).toHaveURL(/\/bots\/%23miners$/);
  await expect(page.getByRole("heading", { name: "#miners" })).toBeVisible();
  await expect(page.locator(".members").locator("visible=true")).toContainText("Miner_01");
  await expect(page.getByRole("link", { name: /#miners/ })).toHaveAttribute("aria-current", "true");
  await shot(page, "18-group-target");
  await page.reload();
  await expect(page.getByRole("heading", { name: "#miners" })).toBeVisible();
  await page.locator(".choices.operations").getByRole("button", { name: "jump" }).click();
  await page.locator(".op-panel").getByRole("button", { name: "Once" }).click();
  await expect.poll(async () => JSON.stringify(await ask("calls"))).toContain("jump");
  await page.getByRole("link", { name: "Manage this Group" }).click();
  await expect(page).toHaveURL(/\/groups\/miners$/);
  await page.getByRole("button", { name: "Take out" }).click();
  await expect(page.getByText("No Bots in this Group yet.").locator("visible=true")).toBeVisible();
  await page.getByRole("button", { name: "Delete Group" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No Groups")).toBeVisible();
  await tab(page, "History");
  await expect(page.getByText("/botgroup create #miners")).toBeVisible();
  await page.getByLabel(/^Show/).selectOption("scripts");
  await expect(page.getByText("/botgroup create #miners")).toHaveCount(0);
});

test("a script is written, checked, saved, compared and run on one screen", async ({ page, context }) => {
  test.skip(!process.env.E2E_RUNNER, "needs a built Luau runner in E2E_RUNNER");
  await start(page, context);
  await tab(page, "Scripts");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByPlaceholder("my_script").fill("e2e_script");
  const editor = page.getByRole("textbox", { name: "Script source" });
  const place = async () => page.locator(".editor").boundingBox();
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type('params {\n\n"not valid"');
  const before = await place();
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.locator(".output").getByRole("alert")).toBeVisible();
  // A result has its own place: the editor stays where it was.
  expect(await place()).toEqual(before);
  await shot(page, "13-script-error");
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.insertText('local a = params { param.number("count", 2) }\nlog("count " .. a.count)\n');
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByRole("status")).toContainText("No problems");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("status")).toContainText("Saved");
  await expect(page.getByRole("link", { name: /e2e_script/ })).toBeVisible();
  await expect(page).toHaveURL(/\/scripts\/e2e_script$/);

  // However long the script, only the editor scrolls.
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(Array.from({ length: 300 }, (_, line) => `-- line ${line}`).join("\n"));
  await page.keyboard.insertText('\nlog("second")\n');
  await expect(page.getByText("unsaved changes")).toBeVisible();
  await fitsTheScreen(page);
  await expect(page.getByRole("navigation")).toBeInViewport();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status")).toContainText("Saved");

  // What is typed survives looking elsewhere.
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText("-- draft\n");
  await tab(page, "Bots");
  await tab(page, "Scripts");
  await expect(page).toHaveURL(/\/scripts\/e2e_script$/);
  await expect(page.getByText("unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("unsaved changes")).toHaveCount(0);

  await page.getByText("More ▾").click();
  await page.getByRole("button", { name: "Earlier revisions..." }).click();
  await page.getByRole("button", { name: "Compare" }).first().click();
  await expect(page.locator(".diff .added").last()).toContainText("-- draft");
  await shot(page, "14-script-history");
  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Run..." }).click();
  await expect(page.getByRole("dialog").getByLabel("count")).toHaveValue("2");
  await page.getByRole("dialog").getByLabel("count").fill("7");
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.locator(".output")).toContainText("count 7", { timeout: 15_000 });
  await shot(page, "15-script-output");

  // Renaming asks on the page, as everything else does.
  await page.getByText("More ▾").click();
  await page.getByRole("button", { name: "Rename..." }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("e2e_renamed");
  await page.getByRole("dialog").getByRole("button", { name: "OK" }).click();
  await expect(page).toHaveURL(/\/scripts\/e2e_renamed$/);
  await page.getByText("More ▾").click();
  await page.getByRole("button", { name: "Remove" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/scripts$/);
});

test("a wrong code says so", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/");
  await page.getByLabel("Link code").fill("AAAAAA");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("not right");
  await shot(page, "05-wrong-code");
});

test("on a phone a list and its detail are two screens, with a way back", async ({ page, context }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  await withAuthenticator(context, page);
  await page.goto("/");
  await shot(page, "06-phone-sign-in");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await link(page);
  const item = page.getByRole("link", { name: /Miner_01/ });
  await expect(item).toBeVisible();
  await shot(page, "07-phone-list");
  await fitsTheScreen(page);
  await item.click();
  await expect(page.getByRole("heading", { name: "Miner_01" })).toBeVisible();
  await expect(item).toBeHidden();
  await shot(page, "08-phone-detail");
  await fitsTheScreen(page);
  await page.getByRole("button", { name: /Back$/ }).click();
  await expect(item).toBeVisible();
  await devices(page);
  await fitsTheScreen(page);
});

test("Japanese follows the browser", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: ORIGIN, locale: "ja-JP" });
  const page = await context.newPage();
  await withAuthenticator(context, page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "パスキーでサインイン" })).toBeVisible();
  await shot(page, "09-ja-sign-in");
  await link(page);
  await expect(page.getByRole("navigation").getByRole("link", { name: "スクリプト" })).toBeVisible();
  await expect(page.getByText("オンライン", { exact: true }).first()).toBeVisible();
  await shot(page, "10-ja-bots");
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
  await page.getByRole("link", { name: /Miner_01/ }).click();
  await expect(page.getByRole("heading", { name: "Miner_01" })).toBeVisible();
  for (const name of ["Groups", "History", "Bots"]) await tab(page, name);
  await devices(page);
  await tab(page, "Scripts");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Script source" })).toBeVisible();
  expect(violations).toEqual([]);
});

test("a broken address still opens the page", async ({ page, context }) => {
  await withAuthenticator(context, page);
  await page.goto("/bots/%E0%A4%A");
  await expect(page.getByRole("heading", { name: "E2E server" })).toBeVisible();
});

test("revoking the grant ends an open page at once, and takes an open question with it", async ({ page, context }) => {
  await start(page, context);
  await tab(page, "Groups");
  await page.locator(".list-tools").getByRole("button", { name: "New", exact: true }).locator("visible=true").click();
  await page.getByRole("dialog").getByLabel("New Group name").fill("secret_group");
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await page.getByRole("button", { name: "Delete Group" }).click();
  await expect(page.getByRole("dialog")).toContainText("secret_group");
  await ask("revoke");
  await expect(page.getByRole("button", { name: "Sign in with a passkey" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("secret_group")).toHaveCount(0);
  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});
