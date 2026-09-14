export const fmt = (v: number | null | undefined, digits = 1, unit = "") =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}${unit ? " " + unit : ""}`;

export const hex8 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toString(16).toUpperCase().padStart(8, "0"));

export const timeStr = (epochS: number) => new Date(epochS * 1000).toLocaleTimeString("vi-VN");

export const dateTimeStr = (iso: string | null) => (iso ? new Date(iso).toLocaleString("vi-VN") : "—");

export const SEVERITY = ["info", "warn", "error", "critical"] as const;
