// A line diff for comparing revisions: longest common subsequence, with shared ends trimmed first.
export interface DiffLine {
  kind: "same" | "added" | "removed";
  text: string;
}

const LIMIT = 4000;

export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const head = a.slice(0, start).map((text): DiffLine => ({ kind: "same", text }));
  const tail = a.slice(endA).map((text): DiffLine => ({ kind: "same", text }));
  const x = a.slice(start, endA);
  const y = b.slice(start, endB);
  if (x.length * y.length > LIMIT * LIMIT) {
    return [...head, ...x.map((text): DiffLine => ({ kind: "removed", text })),
      ...y.map((text): DiffLine => ({ kind: "added", text })), ...tail];
  }
  const lengths = Array.from({ length: x.length + 1 }, () => new Uint32Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i -= 1) {
    for (let j = y.length - 1; j >= 0; j -= 1) {
      lengths[i]![j] = x[i] === y[j] ? lengths[i + 1]![j + 1]! + 1 : Math.max(lengths[i + 1]![j]!, lengths[i]![j + 1]!);
    }
  }
  const middle: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) {
      middle.push({ kind: "same", text: x[i]! });
      i += 1;
      j += 1;
    } else if (j >= y.length || (i < x.length && lengths[i + 1]![j]! >= lengths[i]![j + 1]!)) {
      middle.push({ kind: "removed", text: x[i]! });
      i += 1;
    } else {
      middle.push({ kind: "added", text: y[j]! });
      j += 1;
    }
  }
  return [...head, ...middle, ...tail];
}
