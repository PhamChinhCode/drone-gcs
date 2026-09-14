import { useState } from "react";
import { errMsg, post } from "../lib/api";
import type { User } from "../lib/types";
import { useAuth } from "../store/auth";

export function Login() {
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const setSession = useAuth((s) => s.setSession);
  return (
    <div className="login">
      <form className="panel" onSubmit={async (e) => {
        e.preventDefault();
        try {
          const r = await post<{ token: string; user: User }>("/api/auth/login", { username, password });
          setSession(r.token, r.user);
        } catch (ex) { setErr(errMsg(ex)); }
      }}>
        <h2>Trạm điều khiển mặt đất</h2>
        <p className="muted small">ESP-NOW · giám sát 3D</p>
        <label>Tên đăng nhập<input autoFocus value={username} onChange={(e) => setU(e.target.value)} /></label>
        <label>Mật khẩu<input type="password" value={password} onChange={(e) => setP(e.target.value)} /></label>
        <button className="primary" type="submit">Đăng nhập</button>
        {err && <div className="error">{err}</div>}
      </form>
    </div>
  );
}
