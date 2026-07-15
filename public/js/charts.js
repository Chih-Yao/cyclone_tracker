import { formatWind, knotsToMetresPerSecond } from "./units.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const CHART = Object.freeze({
  width: 640,
  height: 320,
  margin: { top: 28, right: 22, bottom: 50, left: 58 },
});

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function appendText(parent, className, text, attributes = {}) {
  const element = svgElement("text", { class: className, ...attributes });
  element.textContent = text;
  parent.append(element);
  return element;
}

function appendTitle(element, text) {
  const title = svgElement("title");
  title.textContent = text;
  element.append(title);
}

function linearScale(domainMin, domainMax, rangeMin, rangeMax) {
  const span = domainMax - domainMin;
  return (value) => rangeMin + ((value - domainMin) / span) * (rangeMax - rangeMin);
}

function ticks(minimum, maximum, count = 5) {
  return Array.from(
    { length: count },
    (_, index) => minimum + ((maximum - minimum) * index) / (count - 1),
  );
}

function compactNumber(value) {
  return Number(value.toFixed(3)).toString();
}

function tickLabel(value) {
  if (Math.abs(value) >= 100 || Number.isInteger(value)) {
    return value.toFixed(0);
  }
  return value.toFixed(1);
}

function finiteExtent(values) {
  const finiteValues = values.filter(Number.isFinite);
  if (finiteValues.length === 0) {
    return null;
  }
  return [Math.min(...finiteValues), Math.max(...finiteValues)];
}

function paddedExtent(values) {
  const extent = finiteExtent(values);
  if (extent === null) {
    return null;
  }
  const [minimum, maximum] = extent;
  const padding = minimum === maximum ? Math.max(Math.abs(minimum) * 0.05, 1) : (maximum - minimum) * 0.1;
  return [minimum - padding, maximum + padding];
}

function chartPath(points, valueForPoint, xScale, yScale) {
  let segmentOpen = false;
  const commands = [];
  for (const point of points) {
    const value = valueForPoint(point);
    if (!Number.isFinite(point?.tau_h) || !Number.isFinite(value)) {
      segmentOpen = false;
      continue;
    }
    commands.push(
      `${segmentOpen ? "L" : "M"} ${compactNumber(xScale(point.tau_h))} ${compactNumber(yScale(value))}`,
    );
    segmentOpen = true;
  }
  return commands.join(" ");
}

function renderAxes(group, xScale, yScale, xDomain, yDomain, unitLabel) {
  const { width, height, margin } = CHART;
  const plotRight = width - margin.right;
  const plotBottom = height - margin.bottom;

  for (const value of ticks(xDomain[0], xDomain[1])) {
    const x = xScale(value);
    group.append(
      svgElement("line", {
        class: "gridline",
        x1: compactNumber(x),
        y1: margin.top,
        x2: compactNumber(x),
        y2: plotBottom,
      }),
    );
    appendText(group, "axis-tick", `+${tickLabel(value)}h`, {
      x: compactNumber(x),
      y: plotBottom + 20,
      "text-anchor": "middle",
    });
  }

  for (const value of ticks(yDomain[0], yDomain[1])) {
    const y = yScale(value);
    group.append(
      svgElement("line", {
        class: "gridline",
        x1: margin.left,
        y1: compactNumber(y),
        x2: plotRight,
        y2: compactNumber(y),
      }),
    );
    appendText(group, "axis-tick", tickLabel(value), {
      x: margin.left - 9,
      y: compactNumber(y + 4),
      "text-anchor": "end",
    });
  }

  group.append(
    svgElement("line", {
      class: "axis-line",
      x1: margin.left,
      y1: plotBottom,
      x2: plotRight,
      y2: plotBottom,
    }),
    svgElement("line", {
      class: "axis-line",
      x1: margin.left,
      y1: margin.top,
      x2: margin.left,
      y2: plotBottom,
    }),
  );
  appendText(group, "axis-label", "預報時效（小時）", {
    x: (margin.left + plotRight) / 2,
    y: height - 10,
    "text-anchor": "middle",
  });
  appendText(group, "axis-unit", unitLabel, {
    x: margin.left,
    y: 17,
    "text-anchor": "start",
  });
}

function renderChart(svg, points, { valueForPoint, unitLabel, pointLabel, emptyMessage }) {
  if (!svg || svg.namespaceURI !== SVG_NS) {
    throw new TypeError("圖表 renderer 需要 SVG 元素");
  }
  const safePoints = Array.isArray(points) ? points : [];
  svg.setAttribute("viewBox", `0 0 ${CHART.width} ${CHART.height}`);
  svg.querySelectorAll(":scope > [data-renderer='chart']").forEach((node) => node.remove());

  const group = svgElement("g", { "data-renderer": "chart" });
  const xExtent = finiteExtent(safePoints.map((point) => point?.tau_h));
  const values = safePoints.map(valueForPoint);
  const yExtent = paddedExtent(values);
  if (xExtent === null || yExtent === null) {
    appendText(group, "empty-series", emptyMessage, {
      x: CHART.width / 2,
      y: CHART.height / 2,
      "text-anchor": "middle",
    });
    svg.append(group);
    return;
  }

  const xDomain = xExtent[0] === xExtent[1] ? [xExtent[0], xExtent[0] + 1] : xExtent;
  const xScale = linearScale(
    xDomain[0],
    xDomain[1],
    CHART.margin.left,
    CHART.width - CHART.margin.right,
  );
  const yScale = linearScale(
    yExtent[0],
    yExtent[1],
    CHART.height - CHART.margin.bottom,
    CHART.margin.top,
  );
  renderAxes(group, xScale, yScale, xDomain, yExtent, unitLabel);

  const pathData = chartPath(safePoints, valueForPoint, xScale, yScale);
  if (pathData) {
    const path = svgElement("path", { class: "mean-series", d: pathData });
    appendTitle(path, "集合平均預報");
    group.append(path);
  }

  for (const point of safePoints) {
    const value = valueForPoint(point);
    if (!Number.isFinite(point?.tau_h) || !Number.isFinite(value)) {
      continue;
    }
    const label = pointLabel(point);
    const circle = svgElement("circle", {
      class: "series-point",
      cx: compactNumber(xScale(point.tau_h)),
      cy: compactNumber(yScale(value)),
      r: 4,
      tabindex: 0,
      role: "img",
      "aria-label": label,
    });
    appendTitle(circle, label);
    group.append(circle);
  }
  svg.append(group);
}

export function renderWindChart(svg, points, { unit = "kt" } = {}) {
  formatWind(0, unit);
  const valueForPoint = (point) => {
    if (!Number.isFinite(point?.wind_kt)) {
      return null;
    }
    return unit === "m/s" ? knotsToMetresPerSecond(point.wind_kt) : point.wind_kt;
  };
  renderChart(svg, points, {
    valueForPoint,
    unitLabel: unit === "kt" ? "knots" : "m/s",
    pointLabel: (point) => `預報 ${point.tau_h} 小時｜最大風速 ${formatWind(point.wind_kt, unit)}`,
    emptyMessage: "目前沒有最大風速資料",
  });
}

export function renderPressureChart(svg, points) {
  const valueForPoint = (point) =>
    Number.isFinite(point?.pressure_hpa) ? point.pressure_hpa : null;
  renderChart(svg, points, {
    valueForPoint,
    unitLabel: "hPa",
    pointLabel: (point) => `預報 ${point.tau_h} 小時｜中心氣壓 ${point.pressure_hpa.toFixed(1)} hPa`,
    emptyMessage: "目前沒有中心氣壓資料",
  });
}
