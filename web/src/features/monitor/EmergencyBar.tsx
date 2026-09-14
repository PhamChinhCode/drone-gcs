// Thanh khẩn cấp (mục 8.3): luôn hiển thị, không nằm trong tab/menu.
// Giữ 800 ms để xác nhận (nhả sớm = hủy); trạng thái ba giai đoạn: đang gửi → đã nhận (ACK) → đã có hiệu lực.
import { useRef, useState } from "react";
import { errMsg, post } from "../../lib/api";
import type { Command } from "../../lib/types";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";

const HOLD_MS = 800;

const STAGE_LABEL: Record<string, string> = {
  sending: "đang gửi…", acked: "đã nhận (ACK)", effective: "đã có hiệu lực ✓", rejected: "bị từ chối",
  failed: "không có ACK", not_effective: "ACK nhưng KHÔNG hiệu lực", cancelled: "đã hủy",
};

function HoldButton({ label, action, cls }: { label: string; action: string; cls: string }) {
  const [progress, setProgress] = useState(0);
  const [cmdId, setCmdId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const start = useRef(0);
  const cmd: Command | undefined = useLive((s) => (cmdId ? s.commands[cmdId] : undefined));

  const tick = () => {
    const p = Math.min(1, (performance.now() - start.current) / HOLD_MS);
    setProgress(p);
    if (p >= 1) { stop(); void fire(); return; }
    timer.current = requestAnimationFrame(tick);
  };
  const begin = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    start.current = performance.now();
    setErr(null);
    timer.current = requestAnimationFrame(tick);
  };
  const stop = () => {
    if (timer.current) cancelAnimationFrame(timer.current);
    timer.current = null;
    setProgress(0);
  };
  const fire = async () => {
    try {
      const c = await post<Command>("/api/commands/emergency", { action });
      useLive.setState((s) => ({ commands: { ...s.commands, [c.id]: c } }));
      setCmdId(c.id);
    } catch (e) { setErr(errMsg(e)); }
  };

  const stage = cmd?.stage;
  const bad = stage === "failed" || stage === "rejected" || stage === "not_effective";
  return (
    <div className="em-cell">
      <button className={`em-btn ${cls}`} onPointerDown={begin} onPointerUp={stop} onPointerLeave={stop} onPointerCancel={stop}
        style={{ "--p": progress } as React.CSSProperties}>
        <span className="em-ring" />
        {label}
        <small>{progress > 0 ? "giữ để xác nhận…" : "giữ 0,8 s"}</small>
      </button>
      {(cmd || err) && (
        <div className={`em-stage ${bad || err ? "bad" : stage === "effective" ? "good" : ""}`}>
          {err ?? STAGE_LABEL[stage!]}
          {cmd?.ack && <span className="muted"> · {cmd.ack.rtt_ms} ms</span>}
          {cmd && stage === "sending" && (
            <button className="link" onClick={() => post(`/api/commands/${cmd.id}/cancel`)}>hủy phát lại</button>
          )}
        </div>
      )}
    </div>
  );
}

function KillMenu() {
  const [open, setOpen] = useState(false);
  const [word, setWord] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="kill">
      <button className="ghost danger" onClick={() => setOpen(!open)}>KILL ▾</button>
      {open && (
        <div className="kill-pop">
          <p><b>Ngắt động cơ trên không</b> — drone sẽ RƠI. Chỉ dùng khi mất kiểm soát hoàn toàn ở nơi có người.</p>
          <input placeholder='Gõ "KILL" để xác nhận' value={word} onChange={(e) => setWord(e.target.value)} />
          <button className="danger" disabled={word !== "KILL"} onClick={async () => {
            try { await post("/api/commands/emergency", { action: "KILL", confirm_token: word }); setMsg("đã gửi KILL"); }
            catch (e) { setMsg(errMsg(e)); }
            setWord("");
          }}>Gửi KILL</button>
          {msg && <div className="muted">{msg}</div>}
        </div>
      )}
    </div>
  );
}

export function EmergencyBar() {
  const admin = useIsAdmin();
  return (
    <div className="emergency-bar">
      <HoldButton label="RTH" action="RTH" cls="em-rth" />
      <HoldButton label="HẠ CÁNH NGAY" action="LAND_NOW" cls="em-land" />
      <HoldButton label="HỦY NHIỆM VỤ" action="ABORT_MISSION" cls="em-abort" />
      {admin && <KillMenu />}
    </div>
  );
}
