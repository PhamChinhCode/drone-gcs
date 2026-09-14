// DroneGroup, FlownTrail, PlannedPath, WaypointMarkers, LandingCone, TagDetectRay — mục 9.1, 9.2
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import type { Tag, TagDetect, Waypoint } from "../../../lib/types";
import { FSM, TF } from "../../../lib/types";
import { bodyFrdToThree, nedToThree } from "../coords";
import type { InterpolatedSample, TelemetryBuffer } from "../useTelemetryBuffer";
import { Label } from "./SiteObjects";
import { labelTexture } from "./labels";

export type FrameRef = React.MutableRefObject<InterpolatedSample | null>;

const ARM = 0.32;
const CAM_FOV_DEG = 62;

/** Mô hình drone dựng thủ tục (≈ 1k tam giác). Mũi = −Z, cánh phải = +X, nóc = +Y. */
export function DroneGroup({ frame }: { frame: FrameRef }) {
  const group = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const props = useRef<THREE.Group[]>([]);
  const payload = useRef<THREE.Mesh>(null);
  const altLine = useRef<THREE.BufferGeometry>(null);
  const shadow = useRef<THREE.Mesh>(null);
  const staleLabel = useRef<THREE.Sprite>(null);
  const materials = useRef<THREE.Material[]>([]);
  const payloadOpacity = useRef(0);
  const lastStaleSec = useRef(-1);

  const frustum = useMemo(() => {
    // nón tầm nhìn camera nhìn thẳng xuống, cao 2 m
    const h = 2, r = Math.tan(((CAM_FOV_DEG / 2) * Math.PI) / 180) * h;
    const corners = [[r, -h, r], [-r, -h, r], [-r, -h, -r], [r, -h, -r]];
    const pts: number[] = [];
    corners.forEach((c, i) => {
      const n = corners[(i + 1) % 4];
      pts.push(0, 0, 0, ...c, ...c, ...n);
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, []);

  const collect = (m: THREE.Material | null) => { if (m && !materials.current.includes(m)) materials.current.push(m); };

  useFrame((_, dt) => {
    const s = frame.current;
    const g = group.current;
    if (!g) return;
    g.visible = !!s;
    if (!s) return;
    g.position.copy(s.pos);
    body.current!.quaternion.copy(s.quat);
    const armed = (s.flags & TF.ARMED) !== 0;
    props.current.forEach((p, i) => { if (armed && !s.stale) p.rotation.y += (i % 2 ? -1 : 1) * 30 * Math.PI * 2 * Math.min(dt, 0.05); });
    // payloadBox hiện/ẩn mờ dần 300 ms
    const want = (s.flags & TF.CARRYING) !== 0 ? 1 : 0;
    payloadOpacity.current += Math.sign(want - payloadOpacity.current) * Math.min(Math.abs(want - payloadOpacity.current), dt / 0.3);
    if (payload.current) {
      payload.current.visible = payloadOpacity.current > 0.01;
      (payload.current.material as THREE.MeshStandardMaterial).opacity = payloadOpacity.current * (s.stale ? 0.4 : 1);
    }
    // tuổi dữ liệu > 3 s: opacity 0,4 + nhấp nháy + nhãn "số liệu cũ N s" (9.2, 5.8)
    const blink = s.stale ? 0.25 + 0.15 * Math.sin(performance.now() / 180) : 1;
    materials.current.forEach((m) => { m.transparent = true; m.opacity = blink; });
    if (staleLabel.current) {
      staleLabel.current.visible = s.stale;
      const sec = Math.floor(s.ageMs / 1000);
      if (s.stale && sec !== lastStaleSec.current) {
        lastStaleSec.current = sec;
        const tex = labelTexture(`số liệu cũ ${sec} s`, "#fbbf24");
        (staleLabel.current.material as THREE.SpriteMaterial).map = tex;
        staleLabel.current.scale.set((tex.userData.aspect as number) * 0.04, 0.04, 1);
        (staleLabel.current.material as THREE.SpriteMaterial).needsUpdate = true;
      }
    }
    const h = s.pos.y;
    if (altLine.current) {
      const a = altLine.current.getAttribute("position") as THREE.BufferAttribute;
      a.setXYZ(1, 0, -h, 0);
      a.needsUpdate = true;
    }
    if (shadow.current) {
      shadow.current.position.y = -h + 0.02;
      const k = Math.max(0.4, 1 - h / 30);
      shadow.current.scale.setScalar(k);
    }
  });

  return (
    <group ref={group}>
      <group ref={body}>
        <mesh castShadow>
          <boxGeometry args={[0.22, 0.08, 0.3]} />
          <meshStandardMaterial ref={collect} color="#e2e8f0" metalness={0.2} roughness={0.6} />
        </mesh>
        <mesh position={[0, 0.02, -0.19]} rotation-x={-Math.PI / 2} castShadow>
          <coneGeometry args={[0.05, 0.12, 12]} />
          <meshStandardMaterial ref={collect} color="#ef4444" />
        </mesh>
        {[[1, -1], [-1, -1], [-1, 1], [1, 1]].map(([sx, sz], i) => (
          <group key={i}>
            <mesh position={[sx * ARM * 0.5, 0, sz * ARM * 0.5]} rotation-y={Math.atan2(sx, sz)} castShadow>
              <boxGeometry args={[0.03, 0.025, ARM * 1.414]} />
              <meshStandardMaterial ref={collect} color="#475569" />
            </mesh>
            <group position={[sx * ARM, 0.05, sz * ARM]} ref={(el) => { if (el) props.current[i] = el; }}>
              <mesh>
                <cylinderGeometry args={[0.02, 0.02, 0.05, 8]} />
                <meshStandardMaterial ref={collect} color="#111827" />
              </mesh>
              <mesh position-y={0.03}>
                <boxGeometry args={[0.26, 0.005, 0.03]} />
                <meshStandardMaterial ref={collect} color={i < 2 ? "#f97316" : "#94a3b8"} />
              </mesh>
            </group>
          </group>
        ))}
        <lineSegments geometry={frustum}>
          <lineBasicMaterial ref={collect} color="#22d3ee" transparent opacity={0.6} />
        </lineSegments>
        <mesh ref={payload} position={[0, -0.17, 0]} castShadow visible={false}>
          <boxGeometry args={[0.18, 0.14, 0.18]} />
          <meshStandardMaterial color="#b45309" transparent opacity={0} />
        </mesh>
      </group>
      {/* đường thẳng xuống mặt đất + bóng đổ giả */}
      <line>
        <bufferGeometry ref={altLine}>
          <bufferAttribute attach="attributes-position" args={[new Float32Array([0, 0, 0, 0, -1, 0]), 3]} />
        </bufferGeometry>
        <lineDashedMaterial color="#94a3b8" transparent opacity={0.6} />
      </line>
      <mesh ref={shadow} rotation-x={-Math.PI / 2}>
        <circleGeometry args={[0.35, 24]} />
        <meshBasicMaterial color="#000" transparent opacity={0.35} depthWrite={false} />
      </mesh>
      {/* chấm định vị không phụ thuộc khoảng cách để thấy drone từ xa */}
      <sprite scale={[0.025, 0.025, 1]}>
        <spriteMaterial color="#f8fafc" sizeAttenuation={false} depthTest={false} />
      </sprite>
      <sprite ref={staleLabel} position={[0, 0.9, 0]} visible={false} renderOrder={11}>
        <spriteMaterial depthTest={false} transparent sizeAttenuation={false} />
      </sprite>
    </group>
  );
}

const TRAIL_MAX = 3000;

/** Vệt quỹ đạo: Float32Array cấp phát sẵn 3000 điểm, setDrawRange, gradient theo độ cao (9.5). */
export function FlownTrail({ buffer, frame }: { buffer: TelemetryBuffer; frame: FrameRef }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TRAIL_MAX * 3), 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(TRAIL_MAX * 3), 3).setUsage(THREE.DynamicDrawUsage));
    g.setDrawRange(0, 0);
    return g;
  }, []);
  const state = useRef({ version: -1, end: -1 });
  const col = useMemo(() => new THREE.Color(), []);

  useFrame(() => {
    const s = frame.current;
    if (!s) return;
    const end = buffer.indexAt(s.t);
    if (state.current.version === buffer.version && state.current.end === end) return;
    state.current = { version: buffer.version, end };
    const start = Math.max(0, end - TRAIL_MAX + 1);
    const pos = geom.getAttribute("position") as THREE.BufferAttribute;
    const clr = geom.getAttribute("color") as THREE.BufferAttribute;
    let k = 0;
    for (let i = start; i <= end; i++, k++) {
      const p = buffer.samples[i].pos;
      pos.setXYZ(k, p.x, p.y, p.z);
      col.setHSL(0.6 - Math.min(p.y / 15, 1) * 0.6, 0.9, 0.55);
      clr.setXYZ(k, col.r, col.g, col.b);
    }
    geom.setDrawRange(0, k);
    pos.needsUpdate = clr.needsUpdate = true;
    geom.computeBoundingSphere();
  });

  const line = useMemo(() => new THREE.Line(geom, new THREE.LineBasicMaterial({ vertexColors: true })), [geom]);
  useEffect(() => () => { geom.dispose(); (line.material as THREE.Material).dispose(); }, [geom, line]);
  return <primitive object={line} />;
}

export function PlannedPath({ wps, wpIndex, highlight }: { wps: Waypoint[]; wpIndex: number; highlight?: boolean }) {
  const pts = wps.map((w) => nedToThree(w.pos_n_m, w.pos_e_m, w.pos_d_m));
  if (pts.length < 2) return null;
  const passed = pts.slice(0, Math.min(wpIndex, pts.length));
  const cur = wpIndex > 0 && wpIndex < pts.length ? [pts[wpIndex - 1], pts[wpIndex]] : null;
  const rest = pts.slice(Math.max(0, wpIndex));
  return (
    <group>
      {passed.length >= 2 && <Line points={passed} color="#64748b" lineWidth={2} dashed dashSize={0.5} gapSize={0.35} />}
      {rest.length >= 2 && <Line points={rest} color={highlight ? "#f472b6" : "#e2e8f0"} lineWidth={highlight ? 3.5 : 2.5} dashed dashSize={0.6} gapSize={0.4} />}
      {cur && <Line points={cur} color="#facc15" lineWidth={4} />}
    </group>
  );
}

export function WaypointMarkers({ wps, wpIndex }: { wps: Waypoint[]; wpIndex: number }) {
  const active = useRef<THREE.Mesh>(null);
  useFrame(() => {
    if (active.current) {
      const k = 1 + 0.35 * Math.sin(performance.now() / 150);
      active.current.scale.set(k, 1, k);
    }
  });
  return (
    <group>
      {wps.map((w, i) => {
        const p = nedToThree(w.pos_n_m, w.pos_e_m, w.pos_d_m);
        const isCur = i === wpIndex;
        const color = i < wpIndex ? "#64748b" : isCur ? "#facc15" : w.precision_land ? "#f472b6" : "#e2e8f0";
        return (
          <group key={i} position={p}>
            <mesh ref={isCur ? active : undefined}>
              <cylinderGeometry args={[0.12, 0.12, 0.3, 12]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <Label text={`${i}${w.action !== "none" ? " " + w.action : ""}`} position={[0.45, 0.35, 0]} scale={0.6} color={color} />
          </group>
        );
      })}
    </group>
  );
}

/** Hành lang tiếp cận tại tag đích — chỉ hiện ở pha MARKER_SEARCH / PRECISION_LAND. */
export function LandingCone({ tag, frame }: { tag: Tag | null; frame: FrameRef }) {
  const ref = useRef<THREE.Group>(null);
  useFrame(() => {
    const s = frame.current;
    if (ref.current) ref.current.visible = !!tag && !!s && (s.fsm === FSM.PRECISION_LAND || s.fsm === FSM.MARKER_SEARCH);
  });
  if (!tag) return null;
  const p = nedToThree(tag.pos_n_m, tag.pos_e_m, tag.pos_d_m);
  const h = 6;
  return (
    <group ref={ref} position={p} visible={false}>
      <mesh position-y={h / 2}>
        {/* đỉnh ở tag (bán kính dung sai), mở rộng theo độ cao */}
        <cylinderGeometry args={[h * 0.35, Math.max(tag.landing_tol_m, 0.2), h, 32, 1, true]} />
        <meshBasicMaterial color="#f472b6" transparent opacity={0.12} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position-y={0.03}>
        <ringGeometry args={[Math.max(tag.landing_tol_m, 0.2) - 0.03, Math.max(tag.landing_tol_m, 0.2), 48]} />
        <meshBasicMaterial color="#f472b6" side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

/** Tia camera → tag đang khóa. Xanh lá khi khóa, vàng khi thấy chưa khóa; màu theo quality đỏ→xanh. */
export function TagDetectRay({ detect, frame }: { detect: TagDetect | null; frame: FrameRef }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    return g;
  }, []);
  const line = useMemo(() => new THREE.Line(geom, new THREE.LineBasicMaterial({ color: "#22c55e" })), [geom]);
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    const s = frame.current;
    // s.t và t_gcs cùng miền thời gian backend (mẫu dựng đã trừ RENDER_DELAY)
    const show = !!s && !!detect && detect.t_gcs > s.t - 800 && !s.stale;
    line.visible = show;
    if (!show || !s || !detect) return;
    bodyFrdToThree(detect.rel, s.quat, v);
    const a = geom.getAttribute("position") as THREE.BufferAttribute;
    a.setXYZ(0, s.pos.x, s.pos.y, s.pos.z);
    a.setXYZ(1, s.pos.x + v.x, s.pos.y + v.y, s.pos.z + v.z);
    a.needsUpdate = true;
    geom.computeBoundingSphere();
    const locked = (s.flags & TF.TAG_LOCK) !== 0;
    (line.material as THREE.LineBasicMaterial).color.set(locked ? new THREE.Color().setHSL((detect.quality / 100) * 0.33, 0.9, 0.5) : "#facc15");
  });
  return <primitive object={line} />;
}
