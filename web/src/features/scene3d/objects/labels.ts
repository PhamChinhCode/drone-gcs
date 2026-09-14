// Nhãn dạng Sprite vẽ bằng canvas (không tải font từ mạng — GCS phải chạy offline).
import * as THREE from "three";

const cache = new Map<string, THREE.CanvasTexture>();

export function labelTexture(text: string, color = "#ffffff", bg = "rgba(15,23,42,0.78)"): THREE.CanvasTexture {
  const key = `${text}|${color}|${bg}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d")!;
  const font = "600 44px system-ui, sans-serif";
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 28;
  c.width = w; c.height = 64;
  ctx.font = font;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(0, 0, w, 64, 14);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, 14, 34);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.aspect = w / 64;
  cache.set(key, tex);
  return tex;
}

export function stripeTexture(color = "#ef4444"): THREE.CanvasTexture {
  const key = `stripe|${color}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const ctx = c.getContext("2d")!;
  ctx.strokeStyle = color;
  ctx.lineWidth = 10;
  for (let i = -64; i < 128; i += 24) {
    ctx.beginPath(); ctx.moveTo(i, 64); ctx.lineTo(i + 64, 0); ctx.stroke();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  cache.set(key, tex);
  return tex;
}

export const KIND_COLOR: Record<string, string> = {
  home: "#22c55e", pickup: "#3b82f6", dropoff: "#f59e0b", waypoint: "#a78bfa",
};
