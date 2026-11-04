// The page's own dialogs, so that everything asking a question looks and reads the same.
export interface Request {
  title: string;
  message?: string;
  input?: { label: string; value: string };
  confirm: string;
  danger?: boolean;
  resolve: (answer: string | boolean | null) => void;
}

export const dialogs = $state<{ current: Request | null }>({ current: null });

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
