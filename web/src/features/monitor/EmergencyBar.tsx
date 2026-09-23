// Thanh khẩn cấp: luôn hiển thị, không nằm trong tab/menu.
//
// Bốn lệnh của giao ước 4.2, phát lại mỗi 0,5 s KHÔNG giới hạn tới khi có ACK hoặc người vận hành
// huỷ — một lệnh LAND dừng thử sau 3 giây là bỏ drone lại giữa không trung. Vì thế nút "huỷ phát
// lại" phải luôn bấm được, và ba giai đoạn (gửi → ACK → có hiệu lực) đọc từ TRẠNG THÁI drone chứ
// không từ ACK: ACCEPTED chỉ nghĩa là FSM đã nhận.
//
// Không có ARM ở đây, và sẽ không bao giờ có: arm từ xa nghĩa là động cơ quay khi người ra lệnh
// không nhìn thấy drone (9.3).
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
          {cmd?.ack && <span className="muted"> · {cmd.ack.attempts} lần gửi</span>}
          {cmd && stage === "sending" && (
            <button className="link" onClick={() => post(`/api/commands/${cmd.id}/cancel`)}>hủy phát lại</button>
          )}
        </div>
      )}
    </div>
  );
}

export function EmergencyBar() {
  const admin = useIsAdmin();
  return (
    <div className="emergency-bar">
      <HoldButton label="VỀ NHÀ (RTH)" action="RTH" cls="em-rth" />
      <HoldButton label="HẠ CÁNH NGAY" action="LAND" cls="em-land" />
      <HoldButton label="HỦY NHIỆM VỤ" action="ABORT_MISSION" cls="em-abort" />
      {/* DISARM giữa không trung = cắt động cơ, drone RƠI. param2 = 21196 ở backend (4.1). */}
      {admin && <HoldButton label="CẮT ĐỘNG CƠ" action="DISARM" cls="em-abort danger" />}
    </div>
  );
}
