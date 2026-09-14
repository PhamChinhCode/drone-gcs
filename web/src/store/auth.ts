import { create } from "zustand";
import type { User } from "../lib/types";

interface AuthState {
  token: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  logout: () => void;
}

const KEY = "gcs.session";

function load(): { token: string | null; user: User | null } {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* bộ nhớ trình duyệt không khả dụng */ }
  return { token: null, user: null };
}

export const useAuth = create<AuthState>((set) => ({
  ...load(),
  setSession: (token, user) => {
    try { localStorage.setItem(KEY, JSON.stringify({ token, user })); } catch { /* bỏ qua */ }
    set({ token, user });
  },
  logout: () => {
    try { localStorage.removeItem(KEY); } catch { /* bỏ qua */ }
    set({ token: null, user: null });
  },
}));

export const useIsAdmin = () => useAuth((s) => s.user?.role === "admin");
