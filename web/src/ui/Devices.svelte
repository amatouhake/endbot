<script lang="ts">
  import { ApiError, post, signIn } from "./api";
  import { confirmDialog } from "./dialogs.svelte";
  import { errorText, formatDate, t } from "./i18n";

  let { revision, signedIn }: { revision: number; signedIn: () => Promise<void> } = $props();

  let devices = $state<any>(null);
  let busy = $state(false);
  let error = $state("");
  let reauth = $state(false);

  async function load(): Promise<void> {
    devices = await post("/auth/devices");
  }

  async function act(action: () => Promise<void>): Promise<void> {
    busy = true;
    error = "";
    try {
      await action();
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "reauthenticate") reauth = true;
      else if (failure instanceof Error && failure.name === "NotAllowedError") error = errorText("cancelled");
      else error = errorText(failure instanceof ApiError ? failure.code : "generic");
    } finally {
      busy = false;
    }
  }

  // Again whenever the account says something changed.
  $effect(() => {
    void revision;
    void act(load);
  });

  async function removePasskey(id: string): Promise<void> {
    if (!(await confirmDialog(t("removePasskey"), t("remove"), { danger: true }))) return;
    await act(async () => {
      await post("/auth/passkey/remove", { id });
      await load();
    });
  }

  async function removeSession(id: string): Promise<void> {
    if (!(await confirmDialog(t("removeSession"), t("remove"), { danger: true }))) return;
    await act(async () => {
      await post("/auth/session/remove", { id });
      await load();
    });
  }

  function again(): Promise<void> {
    return act(async () => {
      await signIn();
      reauth = false;
      await signedIn();
      await load();
    });
  }
</script>

<div class="scroll">
  <div class="column">
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if reauth}
      <section class="card">
        <p>{t("reauth")}</p>
        <button class="primary" disabled={busy} onclick={again}>{t("signIn")}</button>
      </section>
    {/if}
    <section class="card">
      <h2>{t("addDevice")}</h2>
      <p class="quiet">{t("addDeviceHelp")}</p>
    </section>
    {#if devices}
      <section class="card">
        <h2>{t("passkeys")}</h2>
        <ul>
          {#each devices.passkeys as passkey (passkey.id)}
            <li>
              <span>
                {passkey.label || t("unnamed")}
                <small>
                  {t("created", { date: formatDate(passkey.created) })} ·
                  {passkey.lastUsed ? t("lastUsed", { date: formatDate(passkey.lastUsed) }) : t("neverUsed")}
                </small>
              </span>
              <button class="danger" disabled={busy} onclick={() => removePasskey(passkey.id)}>{t("remove")}</button>
            </li>
          {/each}
        </ul>
      </section>
      <section class="card">
        <h2>{t("sessions")}</h2>
        <ul>
          {#each devices.sessions as session (session.id)}
            <li>
              <span>
                {session.label || t("unnamed")}
                {#if session.current}<span class="tag online">{t("thisDevice")}</span>{/if}
                {#if session.via === "site"}<span class="tag">{t("viaSite")}</span>{/if}
                <small>{t("lastSeen", { date: formatDate(session.lastSeen) })}</small>
              </span>
              {#if !session.current}
                <button class="danger" disabled={busy} onclick={() => removeSession(session.id)}>{t("remove")}</button>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {:else if !error}
      <div class="center"><div class="spinner"></div>{t("loading")}</div>
    {/if}
  </div>
</div>
