// The page's own dialogs, so that everything asking a question looks and reads the same.
export interface Request {
  title: string;
  message?: string;
  input?: { label: string; value: string; maxlength?: number };
  /** Runs on the answer with the dialog still open; a returned message stays on show, so nothing typed is lost. */
  attempt?: (text: string) => Promise<string | null>;
  confirm: string;
  danger?: boolean;
  resolve: (answer: string | boolean | null) => void;
}

export const dialogs = $state<{ current: Request | null }>({ current: null });

/** The person asked is gone: what was on show must go too. */
export function cancelDialog(): void {
  const request = dialogs.current;
  dialogs.current = null;
  request?.resolve(null);
}

export function confirmDialog(title: string, confirm: string, options: { message?: string; danger?: boolean } = {}):
Promise<boolean> {
  return new Promise((resolve) => {
    dialogs.current = { title, confirm, ...options, resolve: (answer) => resolve(answer === true) };
  });
}

export function promptDialog(title: string, label: string, value: string, confirm: string): Promise<string | null> {
  return new Promise((resolve) => {
    dialogs.current = {
      title, confirm, input: { label, value },
      resolve: (answer) => resolve(typeof answer === "string" ? answer : null),
    };
  });
}

/** Asks for a name and creates the thing before closing: a name refused is corrected right there. */
export function createDialog(title: string, label: string, confirm: string, maxlength: number,
  attempt: (text: string) => Promise<string | null>): Promise<string | null> {
  return new Promise((resolve) => {
    dialogs.current = {
      title, confirm, input: { label, value: "", maxlength }, attempt,
      resolve: (answer) => resolve(typeof answer === "string" ? answer : null),
    };
  });
}
