type Point = { x: number; y: number };

export function projectStationPoint(
  x: number,
  y: number,
  height: number,
  center: Point,
  yaw: number,
  pitch = 0.22,
): Point {
  const dx = x - center.x;
  const dy = y - center.y;
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const rotatedX = dx * cos - dy * sin;
  const rotatedY = dx * sin + dy * cos;
  return { x: 720 + rotatedX * 0.58, y: 275 + rotatedY * pitch - height * 1.15 };
}
