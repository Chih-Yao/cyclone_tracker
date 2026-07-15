# ECMWF Member Strength Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ECMWF IFS ENS／AIFS ENS 的最大風速與中心氣壓圖加入低透明度成員曲線，同時保留上層的平均線與平均節點。

**Architecture:** 現有 cycle JSON 已含完整 `storm.members[]`，因此只擴充瀏覽器 renderer 與 app 呼叫介面。圖表 renderer 以平均點與成員點共同計算尺度、先畫成員 path 再畫平均；app 只在 ECMWF source 傳入 members，NCEP 行為保持不變。

**Tech Stack:** 原生 JavaScript ES modules、SVG、CSS、Python Playwright／pytest、uv。

## Global Constraints

- 只有 `ifs-ens` 與 `aifs-ens` 顯示成員強度線；NCEP 三來源維持平均線。
- 不修改 Python adapters、schema、storage、GitHub Actions 或 Vercel 設定。
- 不新增 member point circles、逐點 tooltip、member selector 或 percentile band。
- 成員缺值或相鄰 `tau_h` 差大於 6 小時時必須斷線。
- knots／m/s 使用既有精確換算；中心氣壓維持 hPa。
- 新增測試限於兩組聚焦 browser regression；視覺驗收以桌面與 390px 行動版截圖完成。
- production 前端不得增加外部 runtime request 或 Node build step。

## File Structure

- `public/js/charts.js`：接受可選 member tracks、計算共同尺度、依序畫成員與平均。
- `public/js/app.js`：只為 ECMWF source 將選定 storm 的 members 傳入兩張圖。
- `public/styles.css`：成員細線、氣壓色系與 forced-colors 規則。
- `public/index.html`：將圖表說明改成「集合成員與平均」。
- `tests/fixtures/frontend/ifs-ens-2026071500.json`：提供兩位 member、缺氣壓與跨 tau 缺口。
- `tests/browser/test_dashboard.py`：renderer 與 source integration 的兩組回歸測試。

---

### Task 1: Render Member Paths Beneath the Mean

**Files:**
- Modify: `tests/fixtures/frontend/ifs-ens-2026071500.json`
- Modify: `tests/browser/test_dashboard.py:865-953`
- Modify: `public/js/charts.js:72-248`

**Interfaces:**
- Consumes: `members: Array<{id: string, member_type: string, points: Array<object>}>` from existing cycle JSON.
- Produces: `renderWindChart(svg, meanPoints, { unit = "kt", members = [] } = {})`.
- Produces: `renderPressureChart(svg, meanPoints, { members = [] } = {})`.
- Produces: one `.member-series` path per member with at least one valid value; `.mean-series` remains after all member paths in DOM order.

- [ ] **Step 1: Expand the ECMWF browser fixture**

Replace the single-point `cf00` fixture with two members and four mean times. Use these values so the test contains both a pressure null and a `6 → 18` hour member gap:

```json
"members": [
  {
    "id": "cf00",
    "member_type": "control",
    "points": [
      {"tau_h": 0, "valid_at": "2026-07-15T00:00:00Z", "lat": 18.2, "lon": 131.8, "wind_kt": 100.0, "wind_source_value": 100.0, "wind_source_unit": "kt", "pressure_hpa": 1000.0},
      {"tau_h": 6, "valid_at": "2026-07-15T06:00:00Z", "lat": 18.9, "lon": 131.3, "wind_kt": 90.0, "wind_source_value": 90.0, "wind_source_unit": "kt", "pressure_hpa": null},
      {"tau_h": 18, "valid_at": "2026-07-15T18:00:00Z", "lat": 20.1, "lon": 130.0, "wind_kt": 70.0, "wind_source_value": 70.0, "wind_source_unit": "kt", "pressure_hpa": 980.0}
    ]
  },
  {
    "id": "pf01",
    "member_type": "perturbed",
    "points": [
      {"tau_h": 0, "valid_at": "2026-07-15T00:00:00Z", "lat": 18.0, "lon": 132.0, "wind_kt": 80.0, "wind_source_value": 80.0, "wind_source_unit": "kt", "pressure_hpa": 990.0},
      {"tau_h": 6, "valid_at": "2026-07-15T06:00:00Z", "lat": 18.7, "lon": 131.5, "wind_kt": 85.0, "wind_source_value": 85.0, "wind_source_unit": "kt", "pressure_hpa": 985.0},
      {"tau_h": 12, "valid_at": "2026-07-15T12:00:00Z", "lat": 19.4, "lon": 130.8, "wind_kt": 75.0, "wind_source_value": 75.0, "wind_source_unit": "kt", "pressure_hpa": 975.0}
    ]
  }
]
```

Set mean points to tau `0, 6, 12, 18`, with wind `90, 87.5, 75, 70`, pressure `995, 985, 975, 980`, and member counts `2, 2, 1, 1`.

- [ ] **Step 2: Write the failing renderer regression test**

Add `test_charts_render_ecmwf_members_below_mean_and_break_gaps` after the existing chart tests. Load the ECMWF fixture and call both renderers directly:

```python
storm = json.loads(
    (FRONTEND_FIXTURES / "ifs-ens-2026071500.json").read_text()
)["storms"][0]
result = page.evaluate(
    """async (storm) => {
      const charts = await import('/js/charts.js');
      charts.renderWindChart(
        document.querySelector('#wind-chart'),
        storm.mean.points,
        {unit: 'm/s', members: storm.members},
      );
      charts.renderPressureChart(
        document.querySelector('#pressure-chart'),
        storm.mean.points,
        {members: storm.members},
      );
      const summarize = (selector) => {
        const svg = document.querySelector(selector);
        const members = [...svg.querySelectorAll('path.member-series')];
        const mean = svg.querySelector('path.mean-series');
        return {
          memberPaths: members.map((path) => path.getAttribute('d')),
          memberTitles: members.map((path) => path.querySelector('title').textContent),
          memberYCoordinates: members.flatMap((path) => {
            const coordinates = path.getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
            return coordinates.filter((_, index) => index % 2 === 1);
          }),
          meanAfterMembers: members.every(
            (path) => Boolean(path.compareDocumentPosition(mean) & Node.DOCUMENT_POSITION_FOLLOWING)
          ),
        };
      };
      return {wind: summarize('#wind-chart'), pressure: summarize('#pressure-chart')};
    }""",
    storm,
)
```

Assert two member paths per chart, mean after members, titles containing `cf00`／`pf01`, no `NaN`, two `M` commands in `cf00` wind and pressure paths, and every member y coordinate within the plot range `28 <= y <= 270`. The y-range assertion proves the scale includes member extremes instead of using mean points alone.

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/browser/test_dashboard.py::test_charts_render_ecmwf_members_below_mean_and_break_gaps -q
```

Expected: FAIL because the current renderers ignore `members` and create zero `.member-series` paths.

- [ ] **Step 4: Implement gap-aware member paths and shared extents**

Extend `chartPath` with an optional maximum step:

```javascript
function chartPath(points, valueForPoint, xScale, yScale, maxStepHours = null) {
  let segmentOpen = false;
  let previousTau = null;
  const commands = [];
  for (const point of points) {
    const value = valueForPoint(point);
    if (!Number.isFinite(point?.tau_h) || !Number.isFinite(value)) {
      segmentOpen = false;
      previousTau = null;
      continue;
    }
    if (
      previousTau !== null &&
      maxStepHours !== null &&
      point.tau_h - previousTau > maxStepHours
    ) {
      segmentOpen = false;
    }
    commands.push(
      `${segmentOpen ? "L" : "M"} ${compactNumber(xScale(point.tau_h))} ${compactNumber(yScale(value))}`,
    );
    segmentOpen = true;
    previousTau = point.tau_h;
  }
  return commands.join(" ");
}
```

Extend `renderChart` options with `members = []`. Normalize to member point arrays, flatten them with mean points for extents, then append member paths before the existing mean path:

```javascript
const safeMembers = Array.isArray(members)
  ? members.filter((member) => Array.isArray(member?.points))
  : [];
const allPoints = [safePoints, ...safeMembers.map((member) => member.points)].flat();
const xExtent = finiteExtent(allPoints.map((point) => point?.tau_h));
const yExtent = paddedExtent(allPoints.map(valueForPoint));

for (const member of safeMembers) {
  const memberPathData = chartPath(member.points, valueForPoint, xScale, yScale, 6);
  if (!memberPathData) continue;
  const memberPath = svgElement("path", { class: "member-series", d: memberPathData });
  appendTitle(memberPath, `集合成員 ${member.id}（${member.member_type}）`);
  group.append(memberPath);
}
```

Pass `members` from `renderWindChart` and `renderPressureChart` into `renderChart`. Keep the existing mean points, mean path, and mean circles unchanged.

- [ ] **Step 5: Run renderer tests and verify GREEN**

Run:

```bash
uv run pytest \
  tests/browser/test_dashboard.py::test_charts_render_axes_units_and_accessible_points \
  tests/browser/test_dashboard.py::test_charts_convert_wind_at_render_and_leave_null_gaps \
  tests/browser/test_dashboard.py::test_charts_render_ecmwf_members_below_mean_and_break_gaps -q
```

Expected: `3 passed` with no browser errors.

- [ ] **Step 6: Commit the renderer slice**

```bash
git add public/js/charts.js tests/browser/test_dashboard.py tests/fixtures/frontend/ifs-ens-2026071500.json
git commit -m "feat: render ECMWF member strength series"
```

---

### Task 2: Enable Members Only for ECMWF Sources

**Files:**
- Modify: `tests/browser/test_dashboard.py:999-1026`
- Modify: `public/js/app.js:5-6,194-208,366-378`
- Modify: `public/index.html:119-165`
- Modify: `public/styles.css:562-584,719-739`

**Interfaces:**
- Consumes: the Task 1 renderer options `members` and the existing `state.sourceId`.
- Produces: `renderStrengthCharts(storm)` as the only app-level caller of both strength renderers.
- Produces: zero `.member-series` paths for NCEP and one path per eligible member for ECMWF.

- [ ] **Step 1: Write the failing source integration assertions**

Extend `test_source_storm_and_unit_controls_update_the_view` with these assertions:

```python
assert page.locator("#wind-chart path.member-series").count() == 0
assert page.locator("#pressure-chart path.member-series").count() == 0

page.get_by_label("資料來源").select_option("ifs-ens")
page.locator(".instrument-shell[data-current-source='ifs-ens']").wait_for(timeout=5_000)
assert page.locator("#wind-chart path.member-series").count() == 2
assert page.locator("#pressure-chart path.member-series").count() == 2

page.get_by_role("button", name="knots").click()
assert page.locator("#wind-chart path.member-series").count() == 2
assert page.locator("#wind-chart").get_by_text("knots", exact=True).is_visible()

page.get_by_label("資料來源").select_option("gefs")
page.locator(".instrument-shell[data-current-source='gefs']").wait_for(timeout=5_000)
assert page.locator("#wind-chart path.member-series").count() == 0
assert page.locator("#pressure-chart path.member-series").count() == 0
```

Keep the existing storm, stale-status, map, and unit assertions in the same test.

- [ ] **Step 2: Run the integration test and verify RED**

Run:

```bash
uv run pytest tests/browser/test_dashboard.py::test_source_storm_and_unit_controls_update_the_view -q
```

Expected: FAIL at the first ECMWF member count assertion because app.js still passes only mean points.

- [ ] **Step 3: Centralize strength-chart rendering in app.js**

Add the ECMWF source set near the existing constants and introduce one helper:

```javascript
const ECMWF_SOURCE_IDS = new Set(["ifs-ens", "aifs-ens"]);

function renderStrengthCharts(storm) {
  const points = storm?.mean?.points ?? [];
  const members = ECMWF_SOURCE_IDS.has(state.sourceId) ? (storm?.members ?? []) : [];
  renderWindChart(view.wind, points, { unit: state.unit, members });
  renderPressureChart(view.pressure, points, { members });
}
```

In `renderAll`, keep `renderMap` and replace the two direct chart calls with `renderStrengthCharts(storm)`. In the wind-unit click handler, keep the map rerender and replace the direct wind call with `renderStrengthCharts(storm)` so member wind and axis units update through the same path.

- [ ] **Step 4: Add member styling and truthful chart copy**

Add before `.mean-series`:

```css
.member-series {
  fill: none;
  stroke: var(--sea-700);
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-opacity: 0.2;
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

#pressure-chart .member-series {
  stroke: var(--pressure);
}
```

Add `.member-series` to the GrayText group in `@media (forced-colors: active)`. In `public/index.html`, change both chart section notes to `集合成員與平均`, titles to `集合成員與平均最大風速預報圖`／`集合成員與平均中心氣壓預報圖`, and descriptions to state that thin lines are individual members and the thick line is the ensemble mean.

- [ ] **Step 5: Run focused and full browser tests**

Run:

```bash
uv run pytest tests/browser/test_dashboard.py::test_source_storm_and_unit_controls_update_the_view -q
uv run pytest tests/browser/test_dashboard.py -q
```

Expected: integration test passes; the browser suite reports all tests passing.

- [ ] **Step 6: Commit the app integration slice**

```bash
git add public/js/app.js public/index.html public/styles.css tests/browser/test_dashboard.py
git commit -m "feat: show ECMWF members in strength charts"
```

---

### Task 3: Screenshot QA and Final Frontend Verification

**Files:**
- No committed file is required unless the screenshots reveal a scoped defect.
- Inspect: `public/data/ifs-ens/*.json`, `public/data/aifs-ens/*.json`

**Interfaces:**
- Consumes: completed Task 1 and Task 2 frontend behavior.
- Produces: desktop and mobile screenshot evidence plus a clean full verification result.

- [ ] **Step 1: Run all static checks and tests**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run cyclone-tracker validate public/data
uv lock --check
git diff --check
```

Expected: Ruff and format clean, `249+` tests pass with only the opt-in ECMWF live smoke skipped, data validates, lock resolves, and diff check is empty.

- [ ] **Step 2: Start the production static server**

```bash
uv run python -m http.server 8000 --directory public
```

Open `http://127.0.0.1:8000`, select an ECMWF source and `09W`, and keep the server running only for screenshot capture.

- [ ] **Step 3: Capture desktop and mobile screenshots**

Capture:

- 1440 × 900 desktop with both strength charts visible.
- 390 × 844 mobile after scrolling so both strength charts can be reviewed.

Save the screenshots outside the repository under `/tmp/cyclone-ecmwf-members-desktop.png` and `/tmp/cyclone-ecmwf-members-mobile.png`.

- [ ] **Step 4: Review screenshots and branch state**

Confirm the mean remains visually dominant, member lines are visible without obscuring labels, and there is no obvious clipping or horizontal overflow. Then run:

```bash
git status --short --branch
git log -4 --oneline
```

Expected: only intentional commits are present and the worktree is clean before the NCEP backfill plan begins.
