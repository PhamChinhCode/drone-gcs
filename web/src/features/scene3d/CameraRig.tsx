// Góc nhìn đặt sẵn (mục 9.3): 1 trên xuống, 2 bám sau drone 8 m, 3 từ tag đích lên, 4 nhìn ngang khóa độ cao, 0 tự do.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { OrbitControls as OrbitImpl } from "three-stdlib";
import type { Area, Tag } from "../../lib/types";
import { nedToThree } from "./coords";
import type { FrameRef } from "./objects/DroneObjects";

export type ViewMode = 0 | 1 | 2 | 3 | 4;

interface Props {
  frame: FrameRef;
  view: ViewMode;
  setView: (v: ViewMode) => void;
  areas: Area[];
  tags: Tag[];
  targetTag: Tag | null;
}

export function CameraRig({ frame, view, setView, areas, tags, targetTag }: Props) {
  const controls = useRef<OrbitImpl>(null);
  const { camera } = useThree();
  const tmp = useRef(new THREE.Vector3());
  const tgt = useRef(new THREE.Vector3());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (["0", "1", "2", "3", "4"].includes(e.key)) setView(Number(e.key) as ViewMode);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setView]);

  // góc nhìn tĩnh: đặt một lần khi đổi chế độ
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    if (view === 1) {
      const pts = [...areas.flatMap((a) => a.vertices), ...tags.map((t) => [t.pos_n_m, t.pos_e_m] as [number, number])];
      const ns = pts.map((p) => p[0]), es = pts.map((p) => p[1]);
      const cn = pts.length ? (Math.min(...ns) + Math.max(...ns)) / 2 : 0;
      const ce = pts.length ? (Math.min(...es) + Math.max(...es)) / 2 : 0;
      const span = pts.length ? Math.max(Math.max(...ns) - Math.min(...ns), Math.max(...es) - Math.min(...es), 20) : 40;
      const center = nedToThree(cn, ce, 0);
      c.target.copy(center);
      camera.position.set(center.x, span * 1.1, center.z + 0.01); // +0,01 tránh suy biến "up"
    } else if (view === 4) {
      const s = frame.current;
      const p = s ? s.pos : new THREE.Vector3();
      c.target.set(p.x, p.y, p.z);
      camera.position.set(p.x + 25, p.y, p.z);
    }
    c.update();
  }, [view, areas, tags, camera, frame]);

  useFrame(() => {
    const c = controls.current;
    const s = frame.current;
    if (!c || !s) return;
    if (view === 2) {
      // phía sau drone theo hướng yaw, cao hơn 3 m
      tmp.current.set(0, 0, 1).applyAxisAngle(new THREE.Vector3(0, 1, 0), -THREE.MathUtils.degToRad(s.att[2]));
      tgt.current.copy(s.pos).addScaledVector(tmp.current, 8).add(new THREE.Vector3(0, 3, 0));
      camera.position.lerp(tgt.current, 0.1);
      c.target.lerp(s.pos, 0.3);
      c.update();
    } else if (view === 3 && targetTag) {
      const p = nedToThree(targetTag.pos_n_m, targetTag.pos_e_m, targetTag.pos_d_m);
      camera.position.lerp(tgt.current.set(p.x + 0.8, p.y + 0.3, p.z + 0.8), 0.2);
      c.target.lerp(s.pos, 0.3);
      c.update();
    } else if (view === 4) {
      c.target.y = s.pos.y;
      camera.position.y = s.pos.y;
      c.update();
    }
  });

  return <OrbitControls ref={controls as never} makeDefault enableDamping dampingFactor={0.12} maxPolarAngle={Math.PI * 0.495}
    onStart={() => { if (view === 2 || view === 3) setView(0); }} />;
}
