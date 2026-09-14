import { useAuth } from "../store/auth";

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
  }
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const token = useAuth.getState().token;
  const res = await fetch(path, {
    method,
    headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && token) useAuth.getState().logout();
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = json?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, msg, json?.data);
  }
  return json as T;
}

export const get = <T>(p: string) => api<T>("GET", p);
export const post = <T>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T>(p: string, b?: unknown) => api<T>("PUT", p, b ?? {});
export const del = <T>(p: string) => api<T>("DELETE", p);

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
