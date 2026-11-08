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

addEventListener("popstate", () => {
  Object.assign(route, read());
});
