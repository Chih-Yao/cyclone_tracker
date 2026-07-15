const KNOTS_TO_METRES_PER_SECOND = 1852 / 3600;

export function knotsToMetresPerSecond(knots) {
  return knots * KNOTS_TO_METRES_PER_SECOND;
}

export function formatWind(knots, unit = "kt") {
  const value = unit === "m/s" ? knotsToMetresPerSecond(knots) : knots;
  return `${value.toFixed(1)} ${unit}`;
}
