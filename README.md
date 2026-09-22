# DividendCalendar 台股股息月曆

一套完全獨立的台股除權息月曆。Python 更新程式從 TWSE 與 TPEx 官方 OpenAPI 下載資料、統一欄位後輸出 JSON；前端只讀取本機 JSON，不依賴第三方股市網站。

## 快速開始（Windows）

1. 雙擊 `更新股息資料.bat`，等待視窗顯示更新完成。
2. 雙擊 `啟動股息月曆.bat`，瀏覽器會自動開啟月曆。
3. 伺服器視窗需保持開啟；關閉視窗即可停止。

也可在命令列執行：

```powershell
python update_dividends.py
python -m http.server 8765
```

再開啟 <http://localhost:8765/>。

## 官方資料來源

- TWSE：`https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL`（上市股票除權除息預告表）
- TPEx：`https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost`（上櫃股票除權除息預告表）

兩個 API 的日期均為民國年月日。更新器會轉成 ISO `YYYY-MM-DD`，並把不同欄位統一成 `data/dividends.json`。官方資料屬滾動式預告資料，不是完整歷史資料庫。

## JSON 結構

根層包含更新時間、來源、筆數與 `items`。每筆資料包含：`symbol`、`name`、`market`、`type`、`exDividendDate`、`cashDividend`、`stockDividendRatio`、`eventType`、`status`、`source`。

若官方已公告除息日但現金金額仍空白，`cashDividend` 為 `null`、`status` 為 `pending`。商品依台灣證券代碼規則分成 ETF、REIT、ETN 與個股。每次更新也會在 `data/history/YYYY-MM-DD.json` 留下當日快照。

## GitHub Pages

專案包含 `.github/workflows/pages.yml`。推送至 GitHub 的 `main` 分支後，工作流程會部署靜態網站；每日台北時間約 20:20 重新取得官方資料、保存快照並更新網站，也可在 Actions 頁面手動執行。

## 注意事項

- 不建議直接雙擊 `index.html`，部分瀏覽器會禁止網頁讀取本機 JSON；請使用啟動批次檔。
- 更新資料需要網路連線，官方端點偶爾可能逾時；程式會自動重試三次，紀錄存於 `logs/update.log`。
- 金額與日期以交易所及發行公司最新公告為準。
