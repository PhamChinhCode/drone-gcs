// useTelemetryBuffer.ts — đệm + nội suy (mục 9.4).
// Quy tắc: (1) KHÔNG BAO GIỜ ngoại suy — thiếu mẫu thì giữ mẫu cuối và làm mờ;
// (2) mốc nội suy là thời điểm NHẬN TẠI GCS (t_gcs), không phải t_ms của drone;
// (3) RENDER_DELAY_MS hiển thị được ở màn chẩn đoán.
import * as THREE from "three";
import { attitudeToThree, deg, nedToThree } from "./coords";

export const RENDER_DELAY_MS = 150; // > 1 chu kỳ telemetry, đủ để luôn có 2 mẫu bao quanh
export const MAX_SAMPLES = 3000;
export const STALE_MS = 3000;

export interface Sample {
  t: number;                        // ms — t_gcs (live) hoặc t_utc×1000 (phát lại)
  pos: THREE.Vector3;               // hệ Three
  quat: THREE.Quaternion;
  ned: [number, number, number];
  att: [number, number, number];    // độ
  fsm: number;
  /** mẫu này là một bước nhảy do odom neo lại — KHÔNG nội suy từ mẫu trước sang (5.2b ý 3) */
  jump: boolean;
  wp: number;
}

export interface InterpolatedSample extends Sample {
  stale: boolean;
  ageMs: number;
}

export function makeSample(t: number, ned: [number, number, number], att: [number, number, number], fsm: number, jump: boolean, wp: number): Sample {
  return {
    t, ned, att, fsm, jump, wp,
    pos: nedToThree(ned[0], ned[1], ned[2]),
    quat: attitudeToThree(deg(att[0]), deg(att[1]), deg(att[2])),
  };
}

type Listener = (s: Sample) => void;

export class TelemetryBuffer {
  samples: Sample[] = [];
  private listeners = new Set<Listener>();
  version = 0; // tăng mỗi khi dữ liệu đổi — để vệt bay biết khi nào cần ghi lại

  constructor(public maxSamples = MAX_SAMPLES) {}

  push(s: Sample) {
    const last = this.samples.at(-1);
    if (last && s.t <= last.t) return; // mẫu cũ/đảo thứ tự → bỏ
    this.samples.push(s);
    if (this.samples.length > this.maxSamples) this.samples.splice(0, this.samples.length - this.maxSamples);
    this.version++;
    this.listeners.forEach((l) => l(s));
  }

  load(all: Sample[]) {
    this.samples = all;
    this.version++;
  }

  clear() {
    this.samples = [];
    this.version++;
  }

  subscribe(l: Listener) {
    this.listeners.add(l);
    return () => { this.listeners.delete(l); };
  }

  /** Chỉ số mẫu cuối cùng có t ≤ tt (tìm nhị phân). */
  indexAt(tt: number): number {
    const b = this.samples;
    let lo = 0, hi = b.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (b[mid].t <= tt) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  /** Mẫu tại thời điểm tNow − delay. Thiếu mẫu phía sau → giữ mẫu cuối (không ngoại suy). */
  sampleAt(tNow: number, delayMs = RENDER_DELAY_MS, out?: InterpolatedSample): InterpolatedSample | null {
    const b = this.samples;
    if (!b.length) return null;
    const t = tNow - delayMs;
    const last = b[b.length - 1];
    const ageMs = tNow - last.t;
    const i = this.indexAt(t);
    const o = out ?? ({ pos: new THREE.Vector3(), quat: new THREE.Quaternion() } as InterpolatedSample);
    let a: Sample, k = 0, bb: Sample;
    if (i < 0) { a = bb = b[0]; }
    else if (i >= b.length - 1) { a = bb = last; }
    else {
      a = b[i]; bb = b[i + 1];
      k = (t - a.t) / (bb.t - a.t);
      // khoảng trống lớn (mất gói dài), hoặc mẫu sau là bước nhảy khi odom neo: giữ nguyên mẫu trước
      // rồi nhảy thẳng, đừng vẽ drone trượt qua quãng nó chưa từng bay
      if (bb.t - a.t > 1000 || bb.jump) k = 0;
    }
    o.t = t;
    o.pos.copy(a.pos).lerp(bb.pos, k);
    o.quat.copy(a.quat).slerp(bb.quat, k);
    o.ned = a.ned; o.att = a.att; o.fsm = a.fsm; o.jump = a.jump; o.wp = a.wp;
    o.stale = ageMs > STALE_MS;
    o.ageMs = ageMs;
    return o;
  }
}
