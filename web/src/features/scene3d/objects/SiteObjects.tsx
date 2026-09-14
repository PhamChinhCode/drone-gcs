// GroundPlane, TagField (InstancedMesh), HomePad, OperatingArea, NoFlyZones — mục 9.1
import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Grid } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import type { Area, Tag } from "../../../lib/types";
import { deg, nedToThree } from "../coords";
import { KIND_COLOR, labelTexture, stripeTexture } from "./labels";

export function GroundPlane({ size = 400 }: { size?: number }) {
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} receiveShadow position-y={-0.01}>
        <planeGeometry args={[size, size]} />
        <meshStandardMaterial color="#1e293b" />
      </mesh>
      <Grid args={[size, size]} cellSize={1} sectionSize={10} cellColor="#334155" sectionColor="#475569"
        fadeDistance={160} fadeStrength={1.5} infiniteGrid position-y={0.001} />
      {/* mũi tên Bắc tại gốc Home */}
      <mesh position={[0, 0.02, -3]} rotation-x={-Math.PI / 2}>
        <coneGeometry args={[0.35, 1, 3]} />
        <meshBasicMaterial color="#ef4444" />
      </mesh>
      <Label text="N" position={nedToThree(4.2, 0, -0.1)} scale={0.9} color="#fca5a5" />
    </group>
  );
}

export function Label({ text, position, scale = 1, color = "#fff" }: { text: string; position: THREE.Vector3 | [number, number, number]; scale?: number; color?: string }) {
  const tex = useMemo(() => labelTexture(text, color), [text, color]);
  const aspect = (tex.userData.aspect as number) ?? 2;
  const h = 0.032 * scale; // kích thước cố định trên màn hình — đọc được cả ở góc nhìn toàn khu vực
  return (
    <sprite position={position as never} scale={[h * aspect, h, 1]} renderOrder={10}>
      <spriteMaterial map={tex} depthTest={false} transparent sizeAttenuation={false} />
    </sprite>
  );
}

const _m = new THREE.Matrix4();
const _q = new THREE.Quaternion();
const _s = new THREE.Vector3();
const _p = new THREE.Vector3();
const _c = new THREE.Color();

export function TagField({ tags, highlight, onTagClick }: { tags: Tag[]; highlight?: Set<number>; onTagClick?: (t: Tag) => void }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const visible = tags.filter((t) => t.enabled);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    visible.forEach((t, i) => {
      nedToThree(t.pos_n_m, t.pos_e_m, t.pos_d_m, _p);
      _p.y += 0.02;
      _q.setFromEuler(new THREE.Euler(-Math.PI / 2, 0, -deg(t.yaw_deg), "YXZ"));
      // tấm tag phóng to tối thiểu 0,6 m để nhìn thấy từ xa; kích thước thật vẽ bằng khung trắng
      const s = Math.max(t.tag_size_m, 0.6);
      _s.set(s, s, 1);
      mesh.setMatrixAt(i, _m.compose(_p, _q, _s));
      mesh.setColorAt(i, _c.set(highlight?.has(t.tag_id) ? "#f472b6" : KIND_COLOR[t.kind]));
    });
    mesh.count = visible.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [visible, highlight]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId !== undefined && onTagClick) onTagClick(visible[e.instanceId]);
  };

  return (
    <group>
      {/* 50 tag = 1 draw call (9.5) */}
      <instancedMesh ref={ref} args={[undefined, undefined, Math.max(visible.length, 1)]} onClick={click}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial side={THREE.DoubleSide} />
      </instancedMesh>
      {visible.map((t) => (
        <group key={t.tag_id}>
          <Label text={`${t.label} #${t.tag_id}`} position={nedToThree(t.pos_n_m, t.pos_e_m, t.pos_d_m - 1.1)} color={KIND_COLOR[t.kind]} />
          {t.kind === "home" && <HomePad tag={t} />}
        </group>
      ))}
    </group>
  );
}

export function HomePad({ tag }: { tag: Tag }) {
  const p = nedToThree(tag.pos_n_m, tag.pos_e_m, tag.pos_d_m);
  return (
    <mesh position={[p.x, p.y + 0.015, p.z]} rotation-x={-Math.PI / 2}>
      <ringGeometry args={[Math.max(tag.landing_tol_m, 0.3), Math.max(tag.landing_tol_m, 0.3) + 0.12, 48]} />
      <meshBasicMaterial color="#22c55e" transparent opacity={0.85} side={THREE.DoubleSide} />
    </mesh>
  );
}

function prismEdges(verts: [number, number][], minAlt: number, maxAlt: number): Float32Array {
  const pts: number[] = [];
  const n = verts.length;
  for (let i = 0; i < n; i++) {
    const [an, ae] = verts[i];
    const [bn, be] = verts[(i + 1) % n];
    for (const alt of [minAlt, maxAlt]) pts.push(ae, alt, -an, be, alt, -bn);
    pts.push(ae, minAlt, -an, ae, maxAlt, -an);
  }
  return new Float32Array(pts);
}

export function OperatingArea({ area, alert }: { area: Area; alert: boolean }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(prismEdges(area.vertices, 0.02, area.max_alt_m), 3));
    return g;
  }, [area]);
  const c = area.vertices.reduce((acc, v) => [acc[0] + v[0] / area.vertices.length, acc[1] + v[1] / area.vertices.length], [0, 0]);
  const top = Math.max(...area.vertices.map((v) => v[0]));
  return (
    <group>
      <lineSegments geometry={geom}>
        <lineBasicMaterial color={alert ? "#ef4444" : "#38bdf8"} transparent opacity={alert ? 0.95 : 0.45} />
      </lineSegments>
      <Label text="Vùng bay — CẢNH BÁO, không cưỡng chế" position={nedToThree(top, c[1], -area.max_alt_m - 0.8)} scale={1.4} color={alert ? "#fca5a5" : "#7dd3fc"} />
    </group>
  );
}

export function NoFlyZone({ area }: { area: Area }) {
  const { mesh, edges } = useMemo(() => {
    // Shape trên mặt (x=e, y=n); ép đùn theo +Z rồi xoay −90° quanh X → cao theo +Y, n → −Z
    const shape = new THREE.Shape(area.vertices.map(([n, e]) => new THREE.Vector2(e, n)));
    const g = new THREE.ExtrudeGeometry(shape, { depth: Math.max(area.max_alt_m - area.min_alt_m, 0.1), bevelEnabled: false });
    g.rotateX(-Math.PI / 2);
    g.translate(0, area.min_alt_m, 0);
    return { mesh: g, edges: new THREE.EdgesGeometry(g) };
  }, [area]);
  const tex = useMemo(() => { const t = stripeTexture().clone(); t.repeat.set(0.25, 0.25); t.needsUpdate = true; return t; }, []);
  const c = area.vertices.reduce((acc, v) => [acc[0] + v[0] / area.vertices.length, acc[1] + v[1] / area.vertices.length], [0, 0]);
  return (
    <group>
      <mesh geometry={mesh}>
        <meshBasicMaterial map={tex} color="#ef4444" transparent opacity={0.28} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color="#ef4444" />
      </lineSegments>
      <Label text={`Cấm bay: ${area.name}`} position={nedToThree(c[0], c[1], -area.max_alt_m - 0.8)} scale={1.2} color="#fca5a5" />
    </group>
  );
}
