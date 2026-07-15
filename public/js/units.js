const KNOTS_TO_METRES_PER_SECOND = 1852 / 3600;

export function knotsToMetresPerSecond(knots) {
  return knots * KNOTS_TO_METRES_PER_SECOND;
}

export function formatWind(knots, unit = "kt") {
  if (unit !== "kt" && unit !== "m/s") {
    throw new Error("最大風速單位只支援 kt 或 m/s");
  }
  const value = unit === "m/s" ? knotsToMetresPerSecond(knots) : knots;
  return `${value.toFixed(1)} ${unit}`;
}
