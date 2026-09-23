// A8 — không ngoại suy: ngắt telemetry giữa chừng → drone đứng yên và bị đánh dấu cũ.
import { describe, expect, it } from "vitest";
import { makeSample, TelemetryBuffer } from "./useTelemetryBuffer";

const s = (t: number, n: number) => makeSample(t, [n, 0, -5], [0, 0, 0], 2, false, 0);

describe("TelemetryBuffer", () => {
  it("nội suy giữa hai mẫu với trễ dựng hình", () => {
    const b = new TelemetryBuffer();
    b.push(s(1000, 0));
    b.push(s(1100, 1));
    const o = b.sampleAt(1200, 150)!; // t = 1050 → giữa hai mẫu
    expect(o.pos.z).toBeCloseTo(-0.5); // z_three = −n
  });

  it("A8: mất dữ liệu → giữ mẫu cuối, không bay tiếp, đánh dấu stale sau 3 s", () => {
    const b = new TelemetryBuffer();
    for (let i = 0; i <= 10; i++) b.push(s(1000 + i * 100, i));
    const o1 = b.sampleAt(2000 + 150 + 500, 150)!;
    const o2 = b.sampleAt(2000 + 150 + 5000, 150)!;
    expect(o1.pos.z).toBeCloseTo(-10);
    expect(o2.pos.z).toBeCloseTo(-10);
    expect(o1.stale).toBe(false);
    expect(o2.stale).toBe(true);
  });

  it("bỏ mẫu đảo thứ tự và giới hạn kích thước", () => {
    const b = new TelemetryBuffer(5);
    for (let i = 0; i < 10; i++) b.push(s(i * 100, i));
    b.push(s(50, 99));
    expect(b.samples.length).toBe(5);
    expect(b.samples[0].ned[0]).toBe(5);
  });
});
