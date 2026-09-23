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

/** Tải một tệp cần xác thực.
 *
 * KHÔNG dùng <a href="/api/..."> cho những đường này: trình duyệt tự đi lấy thì không gửi header
 * Authorization nào cả, và backend trả 401 "cần đăng nhập" — người dùng thấy nút chết mà không hiểu
 * vì sao. Phải fetch kèm token rồi dựng Blob để lưu. */
export async function download(path: string, filename: string): Promise<void> {
  const token = useAuth.getState().token;
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (res.status === 401 && token) useAuth.getState().logout();
  if (!res.ok) {
    const text = await res.text();
    let msg = res.statusText;
    try { msg = (JSON.parse(text) as { detail?: string }).detail ?? msg; } catch { /* không phải JSON */ }
    throw new ApiError(res.status, msg);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
