# TWStockLab - 全市場 X因子台股研究室

**不是只有六檔**，六檔只是當初驗證 X因子用的考卷，現在已收斂為全市場 100 DISTINCT。

[[Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/YOUR_USERNAME/twstocklab)

## 功能

- **Tab1 全市場掃描**：上市50 + 上櫃50 DISTINCT，X因子 糾結/量比/乖離/RR/PF/波段 篩選，🟢試單區 🟡加碼 🔴觀察
- **Tab2 單檔透視 長短波**：任意代碼，快波5日 vs 慢波12日不同解讀，RR、PF、入場、停損、目標
- **Tab3 X因子驗證歷史**：六檔當初驗證數據，已驗證通過，現為歷史錨點

## 六檔 FILE_TRUE（2026-09-23 驗證）

- 7712 博盛 156 糾結7 量0.35x 乖離2.3% 高檔無量等噴發
- 5439 高技 245 糾結23 量0.31x 乖離-0.8% 低檔剛轉強
- 2356 英業達 61.2 成本66.3 糾結20 量0.26x 乖離-4.3% RR 1:2.14/1:3.30 PF 1.48/2.35 關鍵56.8
- 2330 台積電 2460 糾結7 量0.56x 乖離1.4% PF慢2.69最高 對照用（需官方重抓）
- 1101 台泥 25.85 成本23.9 糾結5 量0.89x 乖離0.6% PF 1.18/1.78
- 2603 長榮 242.5 糾結41 量0.44x 乖離2.9% 快波18天 慢波21天

## 官方資料來源（只用官方）

- TWSE: `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
- TPEx: `https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=YYYYMMDD&type=EW&response=json`
- 流程：官方API全市場批次 → HTTP驗證 → JSON結構 → 日期驗證 → 欄位正規化 → OHLCV驗證 → 完整度 → CSV/ZIP → App匯入

## 本機執行

```bash
python3 official_market_fetch.py --date 2026-09-23 --out ./official-output
python3 official_market_diagnose.py --date 2026-09-23

# 會產生
# official-output/official-market-history.zip
# official-output/twse/20260923.csv
# official-output/tpex/20260923.csv
# official-output/summary.json
# official-output/coverage.csv
# official-output/sha256.txt
```

## 部署

### GitHub

```bash
git init
git add .
git commit -m "TWStockLab 全市場版 - 不只是六檔"
git remote add origin https://github.com/YOUR_USERNAME/twstocklab.git
git push -u origin main
```

### Vercel / GitHub Pages

- 根目錄 `index.html` 是單檔可執行版，無需 `_next`，無需 build
- 直接上傳到 GitHub Pages 或 Vercel 就能跑
- `TWStockLab_standalone_single_file.html` 是手機免傳版

## 檔案結構

```
index.html                              # 全市場最終版（單檔可執行）
TWStockLab_standalone_single_file.html  # 手機免傳版
mobile_easy_no_manus.html               # 手機驗證版
six_stock_portfolio.json                # 六檔 FILE_TRUE 驗證數據
official_market_fetch.py                # 官方抓取（只用官方）
official_market_diagnose.py             # 15項診斷表
official-output/                        # 官方批次輸出
```

## 為何 Meta AI 預覽不能聯網

- Meta AI 預覽沙盒 `Name or service not known` 物理斷網
- corsproxy.io 舊版 `keyless_legacy_url 403`
- api.corsproxy.io 新版 `Failed to fetch / network_error`
- 在 GitHub Pages / Vercel / Manus 有網路，官方API直接通

## License

MIT - 個人研究用
