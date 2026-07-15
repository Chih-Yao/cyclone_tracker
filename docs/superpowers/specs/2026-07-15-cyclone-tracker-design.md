# 西北太平洋氣旋集合預報追蹤器設計規格

日期：2026-07-15

狀態：已依討論結果定稿，等待書面規格確認後進入實作。

## 1. 目標與成功條件

建立一個可由 GitHub 與 Vercel 免費或低成本維運的靜態前端，呈現西北太平洋熱帶氣旋的集合預報路徑、最大風速與中心氣壓。網站不設後端、不在瀏覽器即時呼叫氣象資料服務，也不依賴外部圖磚；所有資料先由 GitHub Actions 以 `uv` 執行 Python 管線下載、正規化與產生靜態 JSON，再由 Vercel 部署 `public/`。

v1 的成功條件如下：

- 能顯示 NCEP GEFS、NCEP AIGEFS、NCEP AIGFS、ECMWF IFS ENS 與 ECMWF AIFS ENS。
- 範圍限定西北太平洋 `WP`，包含 `01W`–`49W` 與 invest `90W`–`99W`。
- 每個資料來源保留最近 12 個成功 cycle，使用者一次只查看一個來源與一個 cycle。
- 地圖同時呈現各 member 路徑與球面平均路徑；圖表呈現平均最大風速與平均中心氣壓。
- 最大風速可在 knots 與 m/s 間切換，所有相關數值、tooltip 與圖表座標軸同步更新。
- 無外部圖磚、外部字型、CDN JavaScript 或執行階段第三方資料請求。
- GitHub Actions 每 6 小時更新資料；任一來源失敗不阻擋其他來源，也不刪除該來源最後一次成功資料。
- 本機可透過 `uv` 完成安裝、測試、資料更新與預覽，不要求使用者安裝 Node.js。

Google FNV3／Weather Lab 暫緩，不列入 v1 的下載器、manifest 或來源選單。其本機研究紀錄由 `.gitignore` 排除。

## 2. 系統架構

系統分成三個界線清楚的單元：

1. **資料取得與正規化**：Python adapters 只負責來源探索、下載與解析，輸出共同的 Python 資料模型。
2. **彙整與靜態輸出**：管線執行流域與編號篩選、平均值計算、schema 驗證、cycle 保存與 manifest 更新，最後原子寫入 `public/data/`。
3. **瀏覽器視覺化**：原生 HTML、CSS 與 ES modules 只讀取 manifest、使用者選定的單一 cycle JSON，以及本機 Natural Earth GeoJSON。

部署資料流如下：

```text
NCEP / ECMWF 公開資料
        ↓
GitHub Actions（uv + Python，每 6 小時）
        ↓
public/data/*.json（通過驗證才取代舊檔）
        ↓
Git commit + push（只有內容變更時）
        ↓
Vercel Git integration 自動部署 public/
        ↓
使用者瀏覽器讀取靜態 JSON 與本機地圖資產
```

Vercel 不執行下載程式，也不保存 API key。v1 選定的 NCEP 與 ECMWF 公開資料介面不需要專案自有後端。

## 3. 資料來源與 adapters

### 3.1 NCEP tracker 產品

- **GEFS**：NCEP `ens_tracker` 的 GEFS tracker 檔案，包含 control、perturbed members 與來源 mean。
- **AIGEFS**：NCEP `ens_tracker` 的 AI ensemble tracker 檔案，包含 control、perturbed members 與來源 mean。
- **AIGFS**：NCEP `ens_tracker` 的 AI deterministic tracker 檔案。
- 預期 cycle 為 00、06、12、18 UTC；adapter 由日期與 cycle 組成候選目錄，從最新合理候選向前尋找第一個完整且可解析的 cycle。
- 解析 ATCF 風格文字欄位，至少取得 basin、storm number、member/model、初始化時間、forecast hour、緯度、經度、最大風速與中心氣壓。
- 只接受 `WP` 及 storm number `01`–`49`、`90`–`99`；其他流域與編號在寫檔前排除。

### 3.2 ECMWF Tropical Cyclone BUFR

- **IFS ENS**：ECMWF Open Data 的 `ifs / 0p25 / enfo / tf` BUFR。
- **AIFS ENS**：ECMWF Open Data 的 `aifs-ens / 0p25 / enfo / tf` BUFR。
- adapter 使用免 key 的 ECMWF Open Data HTTPS 端點；每個 cycle 只下載一個包含完整路徑的終端時效 tropical cyclone BUFR，並以 ecCodes 解碼 member、氣旋中心、最大風速與中心氣壓。
- IFS ENS 的 00/12 UTC cycle 終端檔為 360 小時，06/18 UTC cycle 為 144 小時；AIFS ENS 的 00/06/12/18 UTC cycle 均為 360 小時。終端檔 404 視為該候選尚不可用並回退較舊 cycle，不得記成空 cycle。
- BUFR 中的 SI 單位先保留來源值，再正規化為共同 schema。

每個 adapter 對管線提供相同介面：來源識別、候選 cycle、下載解析結果與可判別的結果狀態。adapter 不負責寫 manifest、保存 cycle 或計算集合平均。

## 4. 靜態資料 schema

### 4.1 Manifest

`public/data/manifest.json` 使用 `schema_version: 1`，包含：

- `generated_at`：公開資料內容最後一次實際改變的 UTC 時間。
- `sources[]`：來源 `id`、台灣中文顯示名稱、來源與授權連結、狀態、最後成功 cycle、stale 判定時數及 `cycles[]`。
- `cycles[]`：cycle ID、初始化時間、相對 cycle JSON 路徑、storm 摘要與是否為空 cycle。
- 來源狀態只使用 `ok`、`empty`、`stale`、`error`。相同錯誤連續發生時不只為更新時間而重寫 manifest，避免無資料變動仍反覆部署。

### 4.2 Cycle JSON

檔案路徑為 `public/data/{source_id}/{YYYYMMDDHH}.json`，使用 `schema_version: 1`，內容包含來源、初始化時間及 `storms[]`。每個 storm 包含：

- `id`：例如 `09W`、`90W`。
- `name`：來源有正式名稱時使用，否則為 `null`。
- `basin: "WP"` 與 `invest: true | false`。
- `members[]`：member ID、member type 與依 `tau_h` 排序的 `points[]`。
- `mean.points[]`：同一 forecast hour 可用 member 的球面平均座標、平均最大風速、平均中心氣壓與 `member_count`。

每個 member point 包含：

- `tau_h` 與由 cycle 推導的 `valid_at`。
- 十進位 `lat`、`lon`；經度統一為 `-180` 到 `180`。
- 未經顯示層四捨五入的 `wind_kt`。
- `wind_source_value` 與 `wind_source_unit`，供追溯來源單位。
- 可用時提供 `pressure_hpa`，無值時為 `null`。

前端換算使用精確關係 `m/s = knots × 1852 / 3600`；只在顯示時四捨五入一位小數。例如 `100 kt` 顯示為 `51.4 m/s`，儲存的 `wind_kt` 不因切換單位而改變。

## 5. 集合平均與資料品質

- 每個 forecast hour 只平均該時刻有資料的 members，並輸出 `member_count`。
- 緯經度先轉成單位球面笛卡兒座標，分別取平均後再轉回緯經度，以正確處理跨越國際換日線的路徑。
- 最大風速 knots 與中心氣壓 hPa 使用可用值的算術平均；缺值不以 0 代替。
- 無有效座標時不產生該 forecast hour 的 mean point。
- parser 排除超出合法範圍的座標、負 forecast hour、非單調重複點與無法識別的 storm ID；相同 member/tau 重複時採最後一筆完整記錄，並在執行日誌記錄數量。
- 來源自帶的 mean 可保存為標記 `source_mean` 的參考 member 供除錯，但不納入本站重新計算；畫面強調的平均路徑一律只使用 control、perturbed 或 deterministic members 按共同規則產生的 `mean.points[]`。

## 6. 保存、失敗與原子更新

- 每個來源獨立更新；一個來源下載或解析失敗不阻擋其他來源完成。
- 下載與輸出先寫入系統暫存目錄，通過 schema 與合理性驗證後才以原子 replace 寫入 `public/data/`。
- 成功寫入新 cycle 後，才刪除該來源第 13 個及更舊的成功 cycle。
- 失敗時保留最後成功 cycle 與所有對應檔案。若狀態由正常轉為錯誤，manifest 記錄穩定的錯誤類別；同一錯誤重複時不更新時間戳。
- 最新候選 cycle 回傳 HTTP 404 時視為「尚未發布」，繼續嘗試較舊候選，不得直接記成空 cycle。只有已確認發布完成的 NCEP cycle 索引不含符合條件的 WP tracker，或已成功解碼的 ECMWF cycle 不含符合條件的 WP storm，才記錄為成功的空 cycle。
- 若 manifest 或 cycle schema 驗證失敗，整個來源本次更新視為失敗，不提交部分輸出。
- GitHub Actions 使用 concurrency group 防止重疊執行，並只在 `public/data/` 有 staged diff 時建立資料更新 commit。

## 7. 前端資訊架構與視覺方向

使用已確認的方案 C：保留藍綠海象儀表風格，但地圖採本機 Natural Earth 向量資料，不使用外部圖磚。

### 7.1 桌面版

- 頂部為窄版狀態列：產品名稱、storm ID／名稱、資料新鮮度與最後更新時間。
- 下一列為來源、cycle、storm、最大風速單位與「重新讀取資料」控制列。
- 主要區域約 65% 為地圖、35% 為上下排列的最大風速與中心氣壓圖表。
- 地圖以淺海水藍、低彩度陸地綠、細緻海岸線構成；member 路徑保持低透明度，平均路徑以深藍綠粗線與 forecast-hour 節點突出。
- 單一具有辨識度的元素是「預報航跡帶」：眾多半透明 member 路徑形成海上鉛筆航跡，平均路徑像主航線穿過其中。其餘裝飾保持克制。

### 7.2 行動版與互動

- 小螢幕依序堆疊狀態、控制列、地圖、最大風速圖與中心氣壓圖。
- 切換來源後載入該來源最新 cycle；切換 cycle 後保留可用的同一 storm，否則選第一個 storm。
- knots/m/s 選擇保存在 `localStorage`，預設 knots；切換時地圖 tooltip、摘要、圖表線與座標軸同步更新。
- loading、空 cycle、stale 與 error 都提供可行動的台灣中文訊息，不顯示空白畫布。
- 所有控制項支援鍵盤、可見 focus、足夠色彩對比；尊重 `prefers-reduced-motion`。

### 7.3 前端技術限制

- 使用原生 HTML、CSS、JavaScript ES modules 與 SVG，不使用 Node build step。
- 地圖投影、GeoJSON 路徑、折線圖、tooltip 與 responsive resize 由專案內 JavaScript 實作。
- Natural Earth 低解析度 GeoJSON 以本機靜態資產提交，保留來源與 public-domain 說明。
- production 頁面不得載入任何第三方 CDN、外部字型或外部圖磚。

## 8. 專案檔案界線

```text
pyproject.toml                         uv 專案、CLI 與依賴
uv.lock                                鎖定 Python 依賴
src/cyclone_tracker/cli.py             update / validate 命令入口
src/cyclone_tracker/models.py          正規化資料模型與 schema 輸出
src/cyclone_tracker/mean.py            球面與強度平均
src/cyclone_tracker/pipeline.py        各來源獨立更新協調
src/cyclone_tracker/storage.py         原子寫入、manifest 與 12-cycle 保存
src/cyclone_tracker/adapters/atcf.py   GEFS / AIGEFS / AIGFS
src/cyclone_tracker/adapters/ecmwf.py  IFS ENS / AIFS ENS BUFR
public/index.html                      語意結構與初始空狀態
public/styles.css                      藍綠視覺、responsive、a11y
public/js/app.js                       頁面狀態與控制器
public/js/data.js                      manifest / cycle 靜態讀取
public/js/map.js                       本機 GeoJSON 地圖與路徑
public/js/charts.js                    最大風速與氣壓 SVG 圖表
public/js/units.js                     knots / m/s 換算與格式化
public/assets/ne_110m_land.geojson     本機 Natural Earth 陸地資料
public/data/                            GitHub Actions 產生並提交的資料
tests/                                 parser、平均、保存與瀏覽器測試
.github/workflows/update-data.yml      每 6 小時與手動資料更新
vercel.json                            純靜態輸出與 cache headers
README.md                              測試通過後撰寫的台灣中文指南
```

每個檔案只負責一個主要目的；不為 v1 建立通用 plugin framework、資料庫、serverless function、帳號系統或未啟用來源的空 adapter。

## 9. 測試與驗收

### 9.1 Python 自動測試

- ATCF fixture 驗證 WP、一般 storm、invest、座標、風速、氣壓、member 與重複點處理。
- ECMWF 解碼後的最小 fixture 驗證 member、step、SI 單位與 storm ID 正規化；另以可選的網路 smoke test 驗證目前公開端點，不讓網路狀況影響一般單元測試。
- 平均測試涵蓋一般路徑、缺 member、缺 intensity、跨越 ±180° 與無有效座標。
- storage 測試涵蓋原子替換、保留最後成功資料、成功空 cycle、來源獨立失敗與精確保留 12 cycles。
- schema 驗證測試拒絕錯誤單位、非法 storm ID、未排序 tau 與非有限數字。

### 9.2 前端與整合測試

- 使用由 `uv` 管理的 Python Playwright 啟動本機靜態伺服器與 Chromium。
- 攔截靜態資料請求提供固定 fixture，驗證來源／cycle／storm 選單、地圖與兩張圖表均成功繪製。
- 驗證 `100 kt ↔ 51.4 m/s`，且切換後 tooltip、圖表單位與 localStorage 一致。
- 驗證桌面與行動 viewport、鍵盤 focus、空資料與 stale 狀態。
- 阻擋並回報任何非 localhost 的 runtime request，證明 production 前端不依賴外部圖磚或 CDN。

### 9.3 完成前指令

所有工作完成後，必須以全新執行結果確認：

```bash
uv sync --frozen --all-groups
uv run ruff check .
uv run pytest
uv run cyclone-tracker validate public/data
```

另執行一次實際來源更新 smoke test，再以本機 HTTP server 開啟 production `public/`，用 Playwright 完成 Chromium 視覺與互動測試。若任何來源受外部服務阻擋，需清楚列出該來源與證據，其餘來源仍須完成。

## 10. GitHub Actions 與 Vercel

- workflow 由 `schedule` 與 `workflow_dispatch` 觸發，預設每 6 小時執行一次。
- 使用 `astral-sh/setup-uv`、鎖定的 `uv.lock` 與 Python 3.12。
- workflow 權限只要求 `contents: write`，使用內建 `GITHUB_TOKEN` 提交變更，不放個人 token。
- commit 只包含 `public/data/` 的有效變更；沒有 diff 時正常結束。
- Vercel 連接 GitHub repository，以 `public` 為 Output Directory，Framework Preset 使用 Other，不設定後端或排程。
- Vercel 由 main branch 的資料 commit 自動觸發 production deployment。
- 靜態資料使用短時間 CDN cache 與 `stale-while-revalidate`；HTML 不長期快取，避免使用者卡在舊版 manifest。

## 11. 文件與署名

在本機完整測試通過後才撰寫 `README.md`。README 使用台灣繁體中文術語，至少涵蓋：

- macOS／Linux 安裝 uv、建立環境、下載資料、執行測試與本機預覽。
- 資料來源、更新頻率、cycle 保存、knots/m/s 定義與免責聲明。
- 建立 GitHub repository、啟用 Actions、設定 Workflow permissions 為 Read and write、手動執行與查看失敗紀錄。
- 將 repository 匯入 Vercel、選擇 Other、設定 Output Directory `public`、確認 main production branch 與部署後檢查。
- NCEP、ECMWF 與 Natural Earth 的來源／授權／署名連結。
- 明確註明此工具供預報資料視覺化，不取代官方警報與防災決策。
