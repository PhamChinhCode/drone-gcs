// coords.ts — mục 3.2. Đổi cơ sở NED/FRD → Three.js (thuận tay phải, Y hướng lên).
//   x_three =  pos_e   (Đông)
//   y_three = −pos_d   (Lên)
//   z_three = −pos_n   (ngược hướng Bắc)
// Dùng z_three = +pos_n sẽ LẬT GƯƠNG cảnh — lỗi rất khó thấy bằng mắt.
import * as THREE from "three";

// M biến vector NED thành vector Three, và cũng biến trục thân FRD thành trục mô hình
// (phải, lên, lùi): mũi drone = −Z, cánh phải = +X, nóc = +Y.
const M = new THREE.Matrix4().set(
  0, 1, 0, 0,
  0, 0, -1, 0,
  -1, 0, 0, 0,
  0, 0, 0, 1,
);
const Mt = M.clone().transpose();

export function nedToThree(n: number, e: number, d: number, out = new THREE.Vector3()): THREE.Vector3 {
  return out.set(e, -d, -n);
}

export function threeToNed(v: THREE.Vector3): [number, number, number] {
  return [-v.z, v.x, -v.y];
}

/** roll/pitch/yaw (radian, quy ước hàng không ZYX, NED/FRD) → quaternion Three */
export function attitudeToThree(roll: number, pitch: number, yaw: number, out = new THREE.Quaternion()): THREE.Quaternion {
  const cr = Math.cos(roll), sr = Math.sin(roll);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  // R_ned_from_body, ZYX
  const R = new THREE.Matrix4().set(
    cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy, 0,
    cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy, 0,
    -sp, sr * cp, cr * cp, 0,
    0, 0, 0, 1,
  );
  const Rthree = new THREE.Matrix4().multiplyMatrices(M, R).multiply(Mt);
  return out.setFromRotationMatrix(Rthree);
}

export const deg = (d: number) => (d * Math.PI) / 180;

/** Vector trong hệ thân FRD → vector trong hệ Three, theo tư thế đã quy đổi. */
export function bodyFrdToThree(v: [number, number, number], q: THREE.Quaternion, out = new THREE.Vector3()): THREE.Vector3 {
  // trục mô hình: F → −Z, R → +X, D → −Y
  return out.set(v[1], -v[2], -v[0]).applyQuaternion(q);
}

/** Sơ đồ 2D (8.4.1): Bắc hướng lên màn hình — chú ý dấu trừ ở trục tung. */
export function nedToPx(n: number, e: number, n0: number, e0: number, scale: number): [number, number] {
  return [(e - e0) * scale, -(n - n0) * scale];
}

export function pxToNed(px: number, py: number, n0: number, e0: number, scale: number): [number, number] {
  return [n0 - py / scale, e0 + px / scale];
}
