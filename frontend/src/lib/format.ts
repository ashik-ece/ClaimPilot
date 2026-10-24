export function money(value: unknown): string {
  const n = Number(value);
  if (value === null || value === undefined || value === "" || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}
