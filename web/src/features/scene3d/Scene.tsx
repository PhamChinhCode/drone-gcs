// Scene.tsx — khung cảnh 3D dựng HOÀN TOÀN từ telemetry (quyết định số 3, mục 9).
// Dùng chung cho vận hành trực tiếp và phát lại (9.6): chỉ khác bộ đệm + đồng hồ truyền vào.
import { Suspense, useEffect, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import type { Area, Tag, Waypoint } from "../../lib/types";
import { CameraRig, type ViewMode } from "./CameraRig";
import { AssumedDrone, DroneGroup, FlownTrail, LandingCone, PlannedPath, RthHome, WaypointMarkers, type FrameRef } from "./objects/DroneObjects";
import { GroundPlane, NoFlyZone, OperatingArea, TagField } from "./objects/SiteObjects";
import type { InterpolatedSample, TelemetryBuffer } from "./useTelemetryBuffer";

export interface SceneProps {
  buffer: TelemetryBuffer;
  clock: () => number;
  delayMs: number;
  tags: Tag[];
  areas: Area[];
  plan?: Waypoint[] | null;
  planHighlight?: boolean;
  wpIndex?: number;              // ưu tiên shadow (live); mặc định lấy từ mẫu
  expectedTag?: number | null;
  areaAlert?: boolean;
  failsafe?: boolean;
  highlightTags?: Set<number>;
  onTagClick?: (t: Tag) => void;
}

/** Tính mẫu nội suy MỘT lần mỗi khung hình (ưu tiên −1, chạy trước mọi đối tượng). Không qua React state. */
function SampleDriver({ buffer, clock, delayMs, frame, onWp }: { buffer: TelemetryBuffer; clock: () => number; delayMs: number; frame: FrameRef; onWp: (i: number) => void }) {
  const lastWp = useRef(-1);
  useFrame(() => {
    frame.current = buffer.sampleAt(clock(), delayMs, frame.current ?? undefined);
    const wp = frame.current?.wp ?? -1;
    if (wp !== lastWp.current) { lastWp.current = wp; onWp(wp); } // đổi hiếm → setState chấp nhận được
  }, -1);
  return null;
}

export function Scene(p: SceneProps) {
  const frame = useRef<InterpolatedSample | null>(null);
  const [view, setView] = useState<ViewMode>(1);
  const [sampleWp, setSampleWp] = useState(-1);
  const [hidden, setHidden] = useState(document.hidden);

  useEffect(() => {
    // dừng vòng render khi tab ẩn (9.5) — telemetry vẫn ghi ở backend
    const onVis = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const wpIndex = p.wpIndex ?? sampleWp;
  const expected = p.expectedTag ?? (p.plan && wpIndex >= 0 ? p.plan[wpIndex]?.tag_id : null) ?? null;
  const targetTag = p.tags.find((t) => t.tag_id === expected) ?? null;

  return (
    <div className={`scene-wrap${p.failsafe ? " failsafe-border" : ""}`}>
      <Canvas flat shadows="percentage" dpr={[1, 2]} frameloop={hidden ? "never" : "always"}
        camera={{ position: [0, 50, 30], fov: 50, near: 0.2, far: 2000 }} gl={{ antialias: true }}>
        {/* bầu trời xanh nhạt; sương mù cùng màu trời làm mặt đất xa hòa vào đường chân trời */}
        <color attach="background" args={["#cfe3f6"]} />
        <fog attach="fog" args={["#cfe3f6", 250, 1500]} />
        <hemisphereLight args={["#ffffff", "#7d8f62", 0.9]} />
        <directionalLight position={[30, 60, 20]} intensity={1.3} castShadow shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-60} shadow-camera-right={60} shadow-camera-top={60} shadow-camera-bottom={-60} />
        <SampleDriver buffer={p.buffer} clock={p.clock} delayMs={p.delayMs} frame={frame} onWp={setSampleWp} />
        <Suspense fallback={null}>
          <group name="worldRoot">
            <GroundPlane />
            <TagField tags={p.tags} highlight={p.highlightTags} onTagClick={p.onTagClick} />
            {p.plan && <PlannedPath wps={p.plan} wpIndex={wpIndex} highlight={p.planHighlight} />}
            {p.plan && <WaypointMarkers wps={p.plan} wpIndex={wpIndex} />}
            <FlownTrail buffer={p.buffer} frame={frame} />
            <DroneGroup frame={frame} />
            <LandingCone tag={targetTag} frame={frame} />
            <RthHome />
            <AssumedDrone />
            {p.areas.filter((a) => a.enabled && a.vertices.length >= 3).map((a, i) =>
              a.kind === "operating" ? <OperatingArea key={i} area={a} alert={!!p.areaAlert} /> : <NoFlyZone key={i} area={a} />)}
          </group>
        </Suspense>
        <CameraRig frame={frame} view={view} setView={setView} areas={p.areas} tags={p.tags} targetTag={targetTag} />
      </Canvas>
      <div className="view-bar">
        {([[1, "Trên xuống"], [2, "Bám drone"], [3, "Từ tag đích"], [4, "Ngang"], [0, "Tự do"]] as [ViewMode, string][]).map(([v, label]) => (
          <button key={v} className={view === v ? "active" : ""} onClick={() => setView(v)} title={`Phím ${v}`}>
            <kbd>{v}</kbd> {label}
          </button>
        ))}
      </div>
    </div>
  );
}
