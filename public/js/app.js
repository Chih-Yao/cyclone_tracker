import { renderPressureChart, renderWindChart } from "./charts.js";
import { loadCycle, loadManifest } from "./data.js";
import { renderMap } from "./map.js";

const WIND_UNIT_KEY = "cyclone-wind-unit";
const NO_STORE = Object.freeze({ cache: "no-store" });
const SOURCE_MEAN_MEMBER_TYPE = "source_mean";
const MAP_FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

function storedUnit() {
  try {
    return localStorage.getItem(WIND_UNIT_KEY) === "m/s" ? "m/s" : "kt";
  } catch {
    return "kt";
  }
}

const state = {
  manifest: null,
  sourceId: null,
  cycleId: null,
  stormId: null,
  unit: storedUnit(),
  cycle: null,
  land: null,
};

const view = {
  shell: document.querySelector(".instrument-shell"),
  source: document.querySelector("#source-select"),
  cycle: document.querySelector("#cycle-select"),
  storm: document.querySelector("#storm-select"),
  unitButtons: [...document.querySelectorAll("[data-wind-unit]")],
  reload: document.querySelector(".reload-button"),
  status: document.querySelector("#load-status"),
  activeStorm: document.querySelector("#active-storm"),
  freshness: document.querySelector("#source-freshness"),
  generatedAt: document.querySelector("#generated-at"),
  attribution: document.querySelector("#source-attribution"),
  mapPanel: document.querySelector("#map-panel"),
  mapExpand: document.querySelector("#map-expand-button"),
  mapExpandLabel: document.querySelector("[data-map-expand-label]"),
  mapFrame: document.querySelector(".map-frame"),
  map: document.querySelector("#forecast-map"),
  wind: document.querySelector("#wind-chart"),
  pressure: document.querySelector("#pressure-chart"),
  mapBackground: [
    document.querySelector(".skip-link"),
    document.querySelector("header"),
    document.querySelector(".chart-stack"),
    document.querySelector(".disclaimer-rail"),
  ],
};

function selectedSource(manifest = state.manifest) {
  return manifest?.sources.find((source) => source.id === state.sourceId) ?? null;
}

function selectedCycleSummary(source = selectedSource()) {
  return source?.cycles.find((cycle) => cycle.id === state.cycleId) ?? null;
}

function selectedStorm() {
  return state.cycle?.storms.find((storm) => storm.id === state.stormId) ?? null;
}

function individualMembers(storm, sourceId = state.sourceId) {
  return (storm?.members ?? []).filter((member) => {
    if (member.member_type === SOURCE_MEAN_MEMBER_TYPE) {
      return false;
    }
    return sourceId !== "aigfs" || member.member_type !== "deterministic";
  });
}

function renderStrengthCharts(storm) {
  const points = storm?.mean?.points ?? [];
  const members = individualMembers(storm);
  renderWindChart(view.wind, points, { unit: state.unit, members });
  renderPressureChart(view.pressure, points, { members });
}

function setMapExpanded(expanded) {
  view.mapPanel.dataset.expanded = String(expanded);
  view.mapExpand.setAttribute("aria-expanded", String(expanded));
  view.mapExpandLabel.textContent = expanded ? "還原地圖" : "放大地圖";
  document.body.classList.toggle("map-expanded", expanded);
  for (const element of view.mapBackground) {
    element.inert = expanded;
  }
  if (expanded) {
    view.mapPanel.setAttribute("role", "dialog");
    view.mapPanel.setAttribute("aria-modal", "true");
    requestAnimationFrame(() => {
      view.mapFrame.scrollLeft = Math.max(
        0,
        (view.mapFrame.scrollWidth - view.mapFrame.clientWidth) / 2,
      );
    });
  } else {
    view.mapPanel.removeAttribute("role");
    view.mapPanel.removeAttribute("aria-modal");
  }
}

function trapMapFocus(event) {
  const focusable = [...view.mapPanel.querySelectorAll(MAP_FOCUSABLE_SELECTOR)].filter(
    (element) => element.getClientRects().length > 0,
  );
  if (focusable.length === 0) {
    event.preventDefault();
    return;
  }

  const first = focusable[0];
  const last = focusable.at(-1);
  const focusIsOutside = !view.mapPanel.contains(document.activeElement);
  if (event.shiftKey && (document.activeElement === first || focusIsOutside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || focusIsOutside)) {
    event.preventDefault();
    first.focus();
  }
}

function firstAvailableSource(manifest, preferredId = null) {
  const preferred = manifest.sources.find(
    (source) => source.id === preferredId && source.cycles.length > 0,
  );
  return preferred ?? manifest.sources.find((source) => source.cycles.length > 0) ?? null;
}

function newestCycle(source) {
  return [...source.cycles].sort((left, right) =>
    right.initialized_at.localeCompare(left.initialized_at),
  )[0];
}

function effectiveSourceStatus(source) {
  if (!source || source.status === "error" || source.status === "stale") {
    return source?.status ?? null;
  }
  if (!["ok", "empty"].includes(source.status) || source.cycles.length === 0) {
    return source.status;
  }
  const initializedAt = Date.parse(newestCycle(source).initialized_at);
  const staleAfterHours = Number(source.stale_after_hours);
  if (!Number.isFinite(initializedAt) || !Number.isFinite(staleAfterHours)) {
    return source.status;
  }
  const ageMilliseconds = Date.now() - initializedAt;
  return ageMilliseconds > staleAfterHours * 60 * 60 * 1000 ? "stale" : source.status;
}

function utcLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function stormLabel(storm) {
  if (storm.name) {
    return `${storm.id}｜${storm.name}`;
  }
  return `${storm.id}｜${storm.invest ? "熱帶擾動" : "未命名系統"}`;
}

function setStatus(message, kind) {
  view.status.textContent = message;
  view.status.dataset.state = kind;
}

function fillSelect(select, options, selectedValue, emptyLabel) {
  select.replaceChildren();
  if (options.length === 0) {
    const option = new Option(emptyLabel, "", true, true);
    option.disabled = true;
    select.add(option);
    return;
  }
  for (const item of options) {
    const option = new Option(item.label, item.value, false, item.value === selectedValue);
    option.disabled = Boolean(item.disabled);
    select.add(option);
  }
}

function syncControls() {
  const source = selectedSource();
  fillSelect(
    view.source,
    (state.manifest?.sources ?? []).map((candidate) => ({
      value: candidate.id,
      label: candidate.name_zh_tw,
      disabled: candidate.cycles.length === 0,
    })),
    state.sourceId,
    "沒有可用來源",
  );
  fillSelect(
    view.cycle,
    source
      ? [...source.cycles]
          .sort((left, right) => right.initialized_at.localeCompare(left.initialized_at))
          .map((cycle) => ({ value: cycle.id, label: utcLabel(cycle.initialized_at) }))
      : [],
    state.cycleId,
    "沒有可用起報時間",
  );
  fillSelect(
    view.storm,
    (state.cycle?.storms ?? []).map((storm) => ({
      value: storm.id,
      label: stormLabel(storm),
    })),
    state.stormId,
    "這個起報時間沒有氣旋",
  );
  for (const button of view.unitButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.windUnit === state.unit));
  }
}

function syncDisabledState(busy) {
  const source = selectedSource();
  view.shell.setAttribute("aria-busy", String(busy));
  view.source.disabled = busy || state.manifest === null;
  view.cycle.disabled = busy || source === null || source.cycles.length === 0;
  view.storm.disabled = busy || state.cycle === null || state.cycle.storms.length === 0;
  view.reload.disabled = busy;
}

function syncTelemetry() {
  const source = selectedSource();
  const storm = selectedStorm();
  const sourceStatus = effectiveSourceStatus(source);
  view.activeStorm.textContent = storm ? stormLabel(storm) : "這個起報時間沒有氣旋";
  view.generatedAt.textContent = state.manifest ? utcLabel(state.manifest.generated_at) : "—";
  const freshness = {
    ok: "資料正常",
    empty: "本次無氣旋",
    stale: "資料可能過時",
    error: "更新失敗／保留舊資料",
  };
  view.freshness.textContent = source ? freshness[sourceStatus] : "沒有可用資料";

  view.attribution.replaceChildren();
  const label = document.createElement("span");
  label.textContent = "資料來源署名";
  view.attribution.append(label);
  if (source) {
    const link = document.createElement("a");
    link.href = source.attribution_url;
    link.textContent = source.name_zh_tw;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    view.attribution.append(link);
  } else {
    view.attribution.append("尚無可用資料來源");
  }
}

function renderAll() {
  const storm = selectedStorm();
  renderMap(view.map, state.land, storm, { unit: state.unit });
  renderStrengthCharts(storm);
  view.shell.dataset.currentSource = state.sourceId ?? "";
  view.shell.dataset.currentCycle = state.cycleId ?? "";
  if (state.stormId) {
    view.shell.dataset.currentStorm = state.stormId;
  } else {
    delete view.shell.dataset.currentStorm;
  }
  syncTelemetry();
}

function announceCurrentData() {
  const source = selectedSource();
  const sourceStatus = effectiveSourceStatus(source);
  if (sourceStatus === "stale") {
    setStatus(
      "這個資料來源已超過更新時限；目前顯示最後成功資料。可重新讀取資料。",
      "stale",
    );
  } else if (sourceStatus === "error") {
    setStatus(
      "資料來源更新失敗；目前顯示最後成功資料。可重新讀取資料。",
      "error",
    );
  } else if (!state.cycle || state.cycle.storms.length === 0) {
    setStatus(
      "這個起報時間沒有西北太平洋氣旋。可改選其他起報時間或重新讀取資料。",
      "empty",
    );
  } else {
    setStatus(`已載入 ${source.name_zh_tw} 的預報。`, "ready");
  }
}

function commitSelection(manifest, land, source, summary, cycle) {
  const previousStormId = state.stormId;
  state.manifest = manifest;
  state.land = land;
  state.sourceId = source.id;
  state.cycleId = summary.id;
  state.cycle = cycle;
  state.stormId = cycle.storms.some((storm) => storm.id === previousStormId)
    ? previousStormId
    : (cycle.storms[0]?.id ?? null);
  syncControls();
  renderAll();
  announceCurrentData();
}

function showLoadError(error, reload = false) {
  const message = error instanceof Error ? error.message : "目前無法取得預報資料";
  const action = state.cycle
    ? "保留上次可用的預報；請稍後再重新讀取資料。"
    : "請按「重新讀取資料」再試一次。";
  setStatus(`${reload ? "重新讀取失敗：" : ""}${message}。${action}`, "error");
}

async function loadLand(fetchOptions) {
  let response;
  try {
    response = await fetch("/assets/ne_110m_land.geojson", fetchOptions);
  } catch {
    throw new Error("目前無法取得本機地圖資料");
  }
  if (!response.ok) {
    throw new Error("目前無法取得本機地圖資料");
  }
  try {
    return await response.json();
  } catch {
    throw new Error("本機地圖資料格式不正確");
  }
}

async function loadSelection(source, summary) {
  setStatus("正在讀取所選起報時間…", "loading");
  syncDisabledState(true);
  try {
    const cycle = await loadCycle(summary.href);
    commitSelection(state.manifest, state.land, source, summary, cycle);
  } catch (error) {
    syncControls();
    showLoadError(error);
  } finally {
    syncDisabledState(false);
  }
}

async function initialize(fetchOptions) {
  setStatus("正在讀取資料來源與本機地圖…", "loading");
  syncDisabledState(true);
  try {
    const [manifest, land] = await Promise.all([
      loadManifest(undefined, fetchOptions),
      loadLand(fetchOptions),
    ]);
    const source = firstAvailableSource(manifest);
    if (!source) {
      state.manifest = manifest;
      state.land = land;
      state.sourceId = null;
      state.cycleId = null;
      state.stormId = null;
      state.cycle = null;
      syncControls();
      renderAll();
      setStatus("目前沒有可用起報時間。請稍後重新讀取資料。", "empty");
      return;
    }
    const summary = newestCycle(source);
    const cycle = await loadCycle(summary.href, fetchOptions);
    commitSelection(manifest, land, source, summary, cycle);
  } catch (error) {
    showLoadError(error, Boolean(fetchOptions));
  } finally {
    syncDisabledState(false);
  }
}

async function reloadData() {
  if (!state.cycle) {
    await initialize(NO_STORE);
    return;
  }
  setStatus("正在重新讀取 manifest 與所選起報時間…", "loading");
  syncDisabledState(true);
  try {
    const manifest = await loadManifest(undefined, NO_STORE);
    const source = firstAvailableSource(manifest, state.sourceId);
    if (source === null) {
      setStatus("重新讀取完成，但目前沒有可用起報時間；保留上次可用的預報。", "empty");
      return;
    }
    const summary =
      source.cycles.find((cycle) => cycle.id === state.cycleId) ?? newestCycle(source);
    const cycle = await loadCycle(summary.href, NO_STORE);
    commitSelection(manifest, state.land, source, summary, cycle);
  } catch (error) {
    showLoadError(error, true);
  } finally {
    syncDisabledState(false);
  }
}

view.source.addEventListener("change", () => {
  const source = state.manifest.sources.find((candidate) => candidate.id === view.source.value);
  if (source?.cycles.length) {
    void loadSelection(source, newestCycle(source));
  }
});

view.cycle.addEventListener("change", () => {
  const source = selectedSource();
  const summary = source?.cycles.find((cycle) => cycle.id === view.cycle.value);
  if (source && summary) {
    void loadSelection(source, summary);
  }
});

view.storm.addEventListener("change", () => {
  if (state.cycle?.storms.some((storm) => storm.id === view.storm.value)) {
    state.stormId = view.storm.value;
    syncControls();
    renderAll();
  }
});

for (const button of view.unitButtons) {
  button.addEventListener("click", () => {
    state.unit = button.dataset.windUnit;
    try {
      localStorage.setItem(WIND_UNIT_KEY, state.unit);
    } catch {
      // The selected unit still applies for this page view when storage is unavailable.
    }
    syncControls();
    const storm = selectedStorm();
    renderMap(view.map, state.land, storm, { unit: state.unit });
    renderStrengthCharts(storm);
  });
}

view.reload.addEventListener("click", () => void reloadData());
view.mapExpand.addEventListener("click", () => {
  setMapExpanded(view.mapPanel.dataset.expanded !== "true");
});
document.addEventListener("keydown", (event) => {
  if (view.mapPanel.dataset.expanded !== "true") {
    return;
  }
  if (event.key === "Escape") {
    setMapExpanded(false);
    view.mapExpand.focus();
  } else if (event.key === "Tab") {
    trapMapFocus(event);
  }
});
document.addEventListener("DOMContentLoaded", () => void initialize(), { once: true });
