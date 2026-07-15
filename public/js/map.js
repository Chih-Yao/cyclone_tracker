import { formatWind } from "./units.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const VIEW = Object.freeze({
  minLon: 95,
  maxLon: 200,
  minLat: -5,
  maxLat: 55,
  width: 1050,
  height: 600,
});

function normalizePacificLongitude(lon) {
  return lon < 80 ? lon + 360 : lon;
}

function compactNumber(value) {
  return Number(value.toFixed(3)).toString();
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function appendTitle(element, text) {
  const title = svgElement("title");
  title.textContent = text;
  element.append(title);
}

export function projectPoint(lon, lat, width = VIEW.width, height = VIEW.height) {
  if (![lon, lat, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
    return null;
  }
  const normalizedLon = normalizePacificLongitude(lon);
  return [
    ((normalizedLon - VIEW.minLon) / (VIEW.maxLon - VIEW.minLon)) * width,
    ((VIEW.maxLat - lat) / (VIEW.maxLat - VIEW.minLat)) * height,
  ];
}

function validRingCoordinates(ring) {
  if (!Array.isArray(ring)) {
    return [];
  }
  const coordinates = ring
    .filter(
      (coordinate) =>
        Array.isArray(coordinate) &&
        coordinate.length >= 2 &&
        Number.isFinite(coordinate[0]) &&
        Number.isFinite(coordinate[1]),
    )
    .map(([lon, lat]) => [lon, lat]);
  if (
    coordinates.length > 1 &&
    coordinates[0][0] === coordinates.at(-1)[0] &&
    coordinates[0][1] === coordinates.at(-1)[1]
  ) {
    coordinates.pop();
  }
  return coordinates;
}

function unwrapRing(ring) {
  const coordinates = validRingCoordinates(ring);
  if (coordinates.length === 0) {
    return [];
  }
  const unwrapped = [coordinates[0]];
  for (const [rawLon, lat] of coordinates.slice(1)) {
    let lon = rawLon;
    const previousLon = unwrapped.at(-1)[0];
    while (lon - previousLon > 180) {
      lon -= 360;
    }
    while (lon - previousLon < -180) {
      lon += 360;
    }
    unwrapped.push([lon, lat]);
  }
  return unwrapped;
}

function clipBoundary(points, inside, intersection) {
  if (points.length === 0) {
    return [];
  }
  const clipped = [];
  let previous = points.at(-1);
  let previousInside = inside(previous);
  for (const current of points) {
    const currentInside = inside(current);
    if (currentInside !== previousInside) {
      clipped.push(intersection(previous, current));
    }
    if (currentInside) {
      clipped.push(current);
    }
    previous = current;
    previousInside = currentInside;
  }
  return clipped;
}

function intersectLongitude(start, end, longitude) {
  const fraction = (longitude - start[0]) / (end[0] - start[0]);
  return [longitude, start[1] + fraction * (end[1] - start[1])];
}

function intersectLatitude(start, end, latitude) {
  const fraction = (latitude - start[1]) / (end[1] - start[1]);
  return [start[0] + fraction * (end[0] - start[0]), latitude];
}

function clipRingToView(points) {
  let clipped = clipBoundary(
    points,
    ([lon]) => lon >= VIEW.minLon,
    (start, end) => intersectLongitude(start, end, VIEW.minLon),
  );
  clipped = clipBoundary(
    clipped,
    ([lon]) => lon <= VIEW.maxLon,
    (start, end) => intersectLongitude(start, end, VIEW.maxLon),
  );
  clipped = clipBoundary(
    clipped,
    ([, lat]) => lat >= VIEW.minLat,
    (start, end) => intersectLatitude(start, end, VIEW.minLat),
  );
  return clipBoundary(
    clipped,
    ([, lat]) => lat <= VIEW.maxLat,
    (start, end) => intersectLatitude(start, end, VIEW.maxLat),
  );
}

function projectUnwrappedPoint(lon, lat, width, height) {
  return [
    ((lon - VIEW.minLon) / (VIEW.maxLon - VIEW.minLon)) * width,
    ((VIEW.maxLat - lat) / (VIEW.maxLat - VIEW.minLat)) * height,
  ];
}

function clippedRingPath(points, width, height) {
  if (points.length < 3) {
    return "";
  }
  const commands = points.map(([lon, lat], index) => {
    const [x, y] = projectUnwrappedPoint(lon, lat, width, height);
    return `${index === 0 ? "M" : "L"} ${compactNumber(x)} ${compactNumber(y)}`;
  });
  return `${commands.join(" ")} Z`;
}

function ringToPath(ring, width, height) {
  const unwrapped = unwrapRing(ring);
  if (unwrapped.length < 3) {
    return "";
  }
  const longitudes = unwrapped.map(([lon]) => lon);
  const minimum = Math.min(...longitudes);
  const maximum = Math.max(...longitudes);
  const firstShift = Math.ceil((VIEW.minLon - maximum) / 360);
  const lastShift = Math.floor((VIEW.maxLon - minimum) / 360);
  const paths = new Set();
  for (let shift = firstShift; shift <= lastShift; shift += 1) {
    const shifted = unwrapped.map(([lon, lat]) => [lon + shift * 360, lat]);
    const path = clippedRingPath(clipRingToView(shifted), width, height);
    if (path) {
      paths.add(path);
    }
  }
  return [...paths].join(" ");
}

function geometryToPath(geometry, width, height) {
  if (!geometry || typeof geometry !== "object") {
    return "";
  }
  if (geometry.type === "Polygon") {
    return (geometry.coordinates ?? [])
      .map((ring) => ringToPath(ring, width, height))
      .filter(Boolean)
      .join(" ");
  }
  if (geometry.type === "MultiPolygon") {
    return (geometry.coordinates ?? [])
      .flatMap((polygon) =>
        Array.isArray(polygon)
          ? polygon.map((ring) => ringToPath(ring, width, height))
          : [],
      )
      .filter(Boolean)
      .join(" ");
  }
  if (geometry.type === "GeometryCollection") {
    return (geometry.geometries ?? [])
      .map((child) => geometryToPath(child, width, height))
      .filter(Boolean)
      .join(" ");
  }
  return "";
}

export function geoJsonToPath(feature, width = VIEW.width, height = VIEW.height) {
  if (!feature || typeof feature !== "object") {
    return "";
  }
  if (feature.type === "FeatureCollection") {
    return (feature.features ?? [])
      .map((child) => geoJsonToPath(child, width, height))
      .filter(Boolean)
      .join(" ");
  }
  return geometryToPath(feature.type === "Feature" ? feature.geometry : feature, width, height);
}

function linePath(points, width, height) {
  let segmentOpen = false;
  const commands = [];
  for (const point of points ?? []) {
    const projected = projectPoint(point?.lon, point?.lat, width, height);
    if (projected === null) {
      segmentOpen = false;
      continue;
    }
    const [x, y] = projected;
    commands.push(
      `${segmentOpen ? "L" : "M"} ${compactNumber(x)} ${compactNumber(y)}`,
    );
    segmentOpen = true;
  }
  return commands.join(" ");
}

function graticulePath(width, height) {
  const commands = [];
  for (let lon = 100; lon <= 200; lon += 10) {
    const start = projectPoint(lon, VIEW.minLat, width, height);
    const end = projectPoint(lon, VIEW.maxLat, width, height);
    commands.push(
      `M ${compactNumber(start[0])} ${compactNumber(start[1])}`,
      `L ${compactNumber(end[0])} ${compactNumber(end[1])}`,
    );
  }
  for (let lat = 0; lat <= 50; lat += 10) {
    const start = projectPoint(VIEW.minLon, lat, width, height);
    const end = projectPoint(VIEW.maxLon, lat, width, height);
    commands.push(
      `M ${compactNumber(start[0])} ${compactNumber(start[1])}`,
      `L ${compactNumber(end[0])} ${compactNumber(end[1])}`,
    );
  }
  return commands.join(" ");
}

function formatCoordinate(value, positive, negative) {
  return `${Math.abs(value).toFixed(1)}°${value >= 0 ? positive : negative}`;
}

function meanPointLabel(point, unit) {
  const latitude = formatCoordinate(point.lat, "N", "S");
  const longitude = formatCoordinate(point.lon, "E", "W");
  const wind = Number.isFinite(point.wind_kt) ? formatWind(point.wind_kt, unit) : "風速無資料";
  const pressure = Number.isFinite(point.pressure_hpa)
    ? `${point.pressure_hpa.toFixed(1)} hPa`
    : "氣壓無資料";
  const members = Number.isFinite(point.member_count)
    ? `${point.member_count} 位成員`
    : "成員數無資料";
  return `預報 ${point.tau_h} 小時｜${latitude}、${longitude}｜${wind}｜${pressure}｜${members}`;
}

function createTooltip() {
  const tooltip = svgElement("g", {
    class: "map-tooltip",
    visibility: "hidden",
    "aria-hidden": "true",
  });
  tooltip.append(svgElement("rect", { width: 570, height: 36, rx: 2 }));
  const text = svgElement("text", { x: 10, y: 23 });
  tooltip.append(text);
  return tooltip;
}

function showTooltip(tooltip, label, x, y, width) {
  const candidateX = x > width - 590 ? x - 580 : x + 10;
  const tooltipX = Math.min(Math.max(candidateX, 0), width - 570);
  const tooltipY = y < 48 ? y + 12 : y - 46;
  tooltip.setAttribute("transform", `translate(${compactNumber(tooltipX)} ${compactNumber(tooltipY)})`);
  tooltip.setAttribute("visibility", "visible");
  tooltip.setAttribute("aria-hidden", "false");
  tooltip.querySelector("text").textContent = label;
}

function hideTooltip(tooltip) {
  tooltip.setAttribute("visibility", "hidden");
  tooltip.setAttribute("aria-hidden", "true");
}

function landFeatures(landGeoJson) {
  if (landGeoJson?.type === "FeatureCollection" && Array.isArray(landGeoJson.features)) {
    return landGeoJson.features;
  }
  return landGeoJson ? [landGeoJson] : [];
}

export function renderMap(svg, landGeoJson, storm, { unit = "kt" } = {}) {
  if (!svg || svg.namespaceURI !== SVG_NS) {
    throw new TypeError("renderMap 需要 SVG 元素");
  }
  formatWind(0, unit);
  const { width, height } = VIEW;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.querySelectorAll(":scope > [data-renderer='map']").forEach((node) => node.remove());

  const clipId = `${svg.id || "forecast-map"}-plot-clip`;
  const definitions = svgElement("defs", { "data-renderer": "map" });
  const clipPath = svgElement("clipPath", { id: clipId });
  clipPath.append(svgElement("rect", { width, height }));
  definitions.append(clipPath);
  svg.append(definitions);

  const plot = svgElement("g", {
    "data-renderer": "map",
    "clip-path": `url(#${clipId})`,
  });
  plot.append(svgElement("rect", { class: "map-ocean", width, height }));
  plot.append(svgElement("path", { class: "graticule", d: graticulePath(width, height) }));

  for (const feature of landFeatures(landGeoJson)) {
    const pathData = geoJsonToPath(feature, width, height);
    if (pathData) {
      const land = svgElement("path", { class: "land", d: pathData });
      appendTitle(land, "Natural Earth 陸地");
      plot.append(land);
    }
  }

  for (const member of storm?.members ?? []) {
    const pathData = linePath(member.points, width, height);
    if (pathData) {
      const path = svgElement("path", { class: "track-member", d: pathData });
      appendTitle(path, `集合成員 ${member.id}`);
      plot.append(path);
    }
  }

  const meanPoints = storm?.mean?.points ?? [];
  const meanPathData = linePath(meanPoints, width, height);
  if (meanPathData) {
    const meanPath = svgElement("path", { class: "track-mean", d: meanPathData });
    appendTitle(meanPath, "集合平均預報路徑");
    plot.append(meanPath);
  }

  const tooltip = createTooltip();
  for (const point of meanPoints) {
    const projected = projectPoint(point?.lon, point?.lat, width, height);
    if (projected === null) {
      continue;
    }
    const [x, y] = projected;
    const label = meanPointLabel(point, unit);
    const circle = svgElement("circle", {
      class: `mean-point${point.tau_h % 24 === 0 ? " major-hour" : ""}`,
      cx: compactNumber(x),
      cy: compactNumber(y),
      r: point.tau_h % 24 === 0 ? 5 : 4,
      tabindex: 0,
      role: "img",
      "aria-label": label,
    });
    appendTitle(circle, label);
    circle.addEventListener("pointerenter", () => showTooltip(tooltip, label, x, y, width));
    circle.addEventListener("pointerleave", () => hideTooltip(tooltip));
    circle.addEventListener("focus", () => showTooltip(tooltip, label, x, y, width));
    circle.addEventListener("blur", () => hideTooltip(tooltip));
    plot.append(circle);
  }
  plot.append(tooltip);
  svg.append(plot);
}
