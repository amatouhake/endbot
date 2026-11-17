// Where the page is, in the address: a reload, a link and the browser's Back all land on the same thing.
export type Section = "bots" | "scripts" | "triggers" | "groups" | "history" | "devices";
const SECTIONS: Section[] = ["bots", "scripts", "triggers", "groups", "history", "devices"];

function read(): { section: Section; item: string | null } {
  const [first, second] = location.pathname.split("/").filter(Boolean);
  const section = SECTIONS.includes(first as Section) ? (first as Section) : "bots";
  return { section, item: second ? decoded(second) : null };
}

function decoded(text: string): string | null {
  try {
    return decodeURIComponent(text);
  } catch {
    return null;
  }
}

export const route = $state(read());

// A tab reopens on what was chosen there: App keeps this in step with the address while a section is in front.
export const chosen = $state<Partial<Record<Section, string | null>>>({});

export function forgetChosen(): void {
  for (const section of SECTIONS) delete chosen[section];
}

export function href(section: Section, item: string | null = null): string {
  return item ? `/${section}/${encodeURIComponent(item)}` : `/${section}`;
}

export function navigate(section: Section, item: string | null = null, replace = false): void {
  const target = href(section, item);
  if (location.pathname !== target) {
    if (replace) history.replaceState(null, "", target);
    else history.pushState(null, "", target);
  }
  route.section = section;
  route.item = item;
}

/**
 * Move from `from` to `item` once a request has answered, but only where `from` is still what the section shows: the
 * person may have chosen something else meanwhile, and that choice stands (#285). A section in the background just
 * reopens on `item`.
 */
export function leave(section: Section, from: string | null, item: string | null = null, replace = true): void {
  if (route.section === section) {
    if (route.item === from) navigate(section, item, replace);
  } else if ((chosen[section] ?? null) === from) {
    chosen[section] = item;
  }
}

addEventListener("popstate", () => {
  Object.assign(route, read());
});
