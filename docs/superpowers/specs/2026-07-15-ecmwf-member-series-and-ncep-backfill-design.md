# ECMWF 成員強度曲線與 NCEP 歷史 cycle 試用設計

日期：2026-07-15

狀態：方案 A 已確認；等待本書面規格確認後進入實作。

## 1. 目標與成功條件

本次工作包含兩個彼此獨立、但一起驗收的範圍：

1. ECMWF IFS ENS 與 AIFS ENS 的「最大風速」及「中心氣壓」圖，同時呈現各集合成員與本站計算的集合平均。
2. 將 NCEP GEFS、AIGEFS、AIGFS 的 `2026071400Z` cycle 一次性回補到靜態資料，供前端實際比較。

完成時必須滿足：

- 選擇 `ifs-ens` 或 `aifs-ens` 時，兩張強度圖先畫低透明度成員細線，再於上層畫平均粗線與既有平均節點。
- 選擇任何 NCEP 來源時，右側圖表維持目前的平均線，不因本次改動新增 NCEP 成員強度線。
- ECMWF 成員與平均共同決定圖表座標範圍，避免超出平均範圍的成員被裁切。
- knots／m/s 切換同時更新 ECMWF 成員風速線、平均風速線與座標軸；氣壓維持 hPa。
- 成員資料有缺值或 forecast-hour 間隔超過 6 小時時中斷線段，不跨越缺口連線。
- `2026071400Z` 三個 NCEP 來源皆寫入有效 cycle JSON，並在 manifest 中可選到 `09W` 與 `97W`。
- 既有 GitHub Actions、Vercel 靜態部署、12-cycle 保存及來源失敗隔離行為不變。

## 2. 已確認資料與範圍

ECMWF cycle JSON 已包含 `storm.members[]`，每個 member point 都有 `tau_h`、`wind_kt` 與可為 `null` 的 `pressure_hpa`。Python adapter、共同 schema、storage 與 manifest 不需為圖表功能修改；目前只有前端呼叫時只傳入 `storm.mean.points`。

`2026071400Z` 已用現有 NCEP adapter 實測：

- GEFS：`09W` 32 members／28 個 mean times；`97W` 32 members／33 個 mean times。
- AIGEFS：`09W` 32 members／41 個 mean times；`97W` 32 members／24 個 mean times。
- AIGFS：`09W` 1 member／41 個 mean times；`97W` 1 member／10 個 mean times。

因此回補目標是已確認存在且可解析的資料，不以 fixture 或人工製造資料代替。

## 3. ECMWF 圖表設計

### 3.1 資料流

`public/js/app.js` 依目前選定的 source ID 判斷是否為 ECMWF。只有 `ifs-ens` 與 `aifs-ens` 會把 `storm.members` 交給風速與氣壓 renderer；平均點仍由 `storm.mean.points` 傳入。初次 render、來源／cycle／storm 切換及風速單位切換必須走同一套參數組裝，避免只有部分互動更新成員線。

`public/js/charts.js` 保留現有風速與氣壓公開 renderer，擴充 options 接受 member tracks。共用 renderer 會：

1. 收集平均與所有可用 member point，計算共同 x/y extent。
2. 先為每個 member 建立一條 `.member-series` SVG path。
3. 再建立既有 `.mean-series` 與可鍵盤聚焦的平均節點，確保平均永遠位於視覺上層。
4. 對缺值或相鄰 tau 差大於 6 小時的位置開始新 segment。

每個 member path 附上 member ID 與 member type 的 `<title>`；不為每個 member point 建立 circle、tooltip 或 tabindex。這可避免最多約 52 members × 61 forecast times × 2 charts 產生數千個互動 DOM 節點。精確單點查詢與 member selector 不在本次範圍。

### 3.2 視覺與文字

- 成員線使用同色系低透明度、細 stroke；平均線維持現有高對比粗線與節點。
- 風速與氣壓使用各自既有色系，成員線不以 52 種顏色區分。
- `public/index.html` 的圖表標題、描述與 section note 改為「集合成員與平均」，不再宣稱只顯示平均或未實作的成員範圍帶。
- forced-colors 模式仍能區分成員與平均；行動版不新增控制列，也不得造成水平 overflow。

## 4. NCEP `2026071400Z` 一次性回補

本次不新增 `--cycle`、日期輸入框或通用歷史下載 API。回補以 `uv` 直接協調現有 adapters 與 `DataStore`，但分開處理兩個時間：

- adapter 的查找基準明確設為 `2026-07-14 00:00:00Z`，且三個 adapter 都必須回傳 cycle ID `2026071400`。
- 寫入 `DataStore` 時使用實際執行時間，避免把 manifest 的 `generated_at` 倒退到 7 月 14 日，也讓來源狀態仍依目前最新 cycle 判定。

三個來源必須先全部下載、解析並確認 exact cycle 成功，之後才開始寫入；任一來源未回傳 `2026071400` 就停止且不寫檔。這個一次性流程不使用 `update_sources(now=歷史時間)`，因為該介面會把同一個 `now` 同時用於來源查找與 manifest 發布時間。

回補只包含 `gefs`、`aigefs`、`aigfs`，不重抓 ECMWF，也不把 HGEFS mean-only tracker 冒充 GEFS ensemble。產出的三個 cycle 檔與 manifest 必須經既有 schema 驗證，再與功能程式碼一起提交。

這是一次性試用資料，不承諾永久固定在來源選單。既有每來源保留最近 12 個成功 cycle 的規則照常運作；未來累積超過 12 個較新 cycle 後，`2026071400Z` 會自然被移除。若後續確定需要可重複的歷史回補，再另行設計 CLI 與保存政策。

## 5. 不在本次範圍

- 不修改 ECMWF BUFR 解碼、共同 Python schema、平均算法或後端架構。
- 不對 NCEP 圖表啟用成員強度線。
- 不新增 percentile band、min/max band、member selector 或逐點 member tooltip。
- 不新增任意歷史日期 UI、排程式歷史回補或永久保存例外。
- 不新增 HGEFS、Google FNV3 或其他資料來源。
- 不清理或覆蓋主工作目錄中既有的未提交資料。

## 6. 測試與驗收

功能實作保留少量核心回歸測試，視覺驗收簡化為截圖：

1. 先擴充 ECMWF 前端 fixture，加入多 member、跨 tau 與缺 pressure 的資料。
2. 建立一組聚焦的 browser regression test，合併驗證 member path 數量、member 在 mean 下層、缺口斷線與 knots／m/s 轉換。
3. 建立一組 source integration test，驗證 ECMWF 顯示成員線、NCEP 維持平均線，且切換來源不留下舊圖層。
4. 以最小前端修改讓測試通過；視覺部分只產出桌面與 390px 行動版截圖供人工確認，不新增完整 responsive 尺寸矩陣。
5. 回補後直接驗證三個 `2026071400.json`、manifest cycle/storm 摘要及整棵 `public/data` schema；12-cycle 行為沿用既有 storage 測試，不重複新增測試。

完成前重新執行：

```bash
uv sync --frozen --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run cyclone-tracker validate public/data
uv lock --check
git diff --check
```

瀏覽器 QA 以實際 ECMWF cycle 產出桌面與行動版截圖，人工確認平均線在成員線上方仍可辨識、版面沒有明顯裁切或水平 overflow。既有全套測試仍需通過，但本次不擴充額外的視覺量測或互動 QA 矩陣。

## 7. Git 與部署界線

工作在隔離 branch 上進行，以最新 `origin/main` 為基準；主工作目錄現有未提交資料保持不動。提交時只納入本規格直接要求的前端、測試、文件與三個 NCEP 回補 cycle／manifest 變更。

完成驗證後再整合至 `main`。GitHub Actions 後續資料 commit 與 Vercel 自動部署沿用既有流程，不修改 workflow 權限、排程或 `vercel.json`。
