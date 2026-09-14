// Bảng kiểm chứng BẮT BUỘC mục 3.2 — nghiệm thu A6.
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { attitudeToThree, bodyFrdToThree, deg, nedToPx, nedToThree, pxToNed, threeToNed } from "./coords";

const NOSE = new THREE.Vector3(0, 0, -1);
const TOP = new THREE.Vector3(0, 1, 0);

function axes(r: number, p: number, y: number) {
  const q = attitudeToThree(deg(r), deg(p), deg(y));
  return { nose: NOSE.clone().applyQuaternion(q), top: TOP.clone().applyQuaternion(q) };
}

function close(v: THREE.Vector3, x: number, y: number, z: number, eps = 0.01) {
  expect(v.x).toBeCloseTo(x, 2);
  expect(v.y).toBeCloseTo(y, 2);
  expect(v.z).toBeCloseTo(z, 2);
  expect(Math.abs(v.length() - 1)).toBeLessThan(eps);
}

describe("attitudeToThree — bảng 5 trường hợp (A6)", () => {
  it("0/0/0 (Bắc): mũi (0,0,−1), nóc (0,1,0)", () => {
    const { nose, top } = axes(0, 0, 0);
    close(nose, 0, 0, -1);
    close(top, 0, 1, 0);
  });
  it("yaw 90° (Đông): mũi (1,0,0)", () => {
    const { nose, top } = axes(0, 0, 90);
    close(nose, 1, 0, 0);
    close(top, 0, 1, 0);
  });
  it("yaw 180° (Nam): mũi (0,0,1)", () => {
    const { nose, top } = axes(0, 0, 180);
    close(nose, 0, 0, 1);
    close(top, 0, 1, 0);
  });
  it("pitch +15° (ngóc lên): mũi (0,+0.26,−0.97), nóc nghiêng về sau", () => {
    const { nose, top } = axes(0, 15, 0);
    close(nose, 0, 0.26, -0.97);
    expect(top.z).toBeGreaterThan(0.2); // về sau = +Z
  });
  it("roll +20° (nghiêng phải): mũi (0,0,−1), nóc (0.34,0.94,0)", () => {
    const { nose, top } = axes(20, 0, 0);
    close(nose, 0, 0, -1);
    close(top, 0.34, 0.94, 0);
  });
});

describe("nedToThree", () => {
  it("không lật gương: Bắc → −Z, Đông → +X, lên (d âm) → +Y", () => {
    expect(nedToThree(1, 0, 0).toArray()).toEqual([0, -0, -1]);
    expect(nedToThree(0, 1, 0).toArray()).toEqual([1, -0, -0]);
    expect(nedToThree(0, 0, -5).toArray()).toEqual([0, 5, -0]);
  });
  it("thuận tay phải: Bắc × Đông = Xuống", () => {
    const n = nedToThree(1, 0, 0), e = nedToThree(0, 1, 0), d = nedToThree(0, 0, 1);
    const c = new THREE.Vector3().crossVectors(n, e);
    expect(c.distanceTo(d)).toBeLessThan(1e-9);
  });
  it("quy đổi hai chiều", () => {
    const v = nedToThree(3, -4, -5);
    expect(threeToNed(v)).toEqual([3, -4, -5]);
  });
  it("vector thân FRD theo yaw 90°: phía trước = Đông", () => {
    const q = attitudeToThree(0, 0, deg(90));
    const f = bodyFrdToThree([1, 0, 0], q);
    const east = nedToThree(0, 1, 0);
    expect(f.distanceTo(east)).toBeLessThan(1e-6);
  });
});

describe("sơ đồ 2D (8.4.1)", () => {
  it("Bắc hướng lên màn hình (py âm), quy đổi hai chiều", () => {
    const [px, py] = nedToPx(10, 5, 0, 0, 20);
    expect(px).toBe(100);
    expect(py).toBe(-200);
    expect(pxToNed(px, py, 0, 0, 20)).toEqual([10, 5]);
  });
});
