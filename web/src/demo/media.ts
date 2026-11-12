export const sample = (i: number) => i % 24000 < 1200 ? (i % 200 < 100 ? 0.25 : -0.25) : 0;
export function pcm(start: number, count: number): Uint8Array {
  const bytes = new Uint8Array(count * 4); const view = new DataView(bytes.buffer);
  for (let i = 0; i < count; i++) view.setFloat32(i * 4, sample(start + i), true);
  return bytes;
}
export function peak(start: number, end: number): [number, number] {
  let min = Infinity, max = -Infinity;
  for (let i = start; i < end; i++) { const v = sample(i); min = Math.min(min, v); max = Math.max(max, v); }
  return [min, max];
}
