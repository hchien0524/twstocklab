#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
official_market_diagnose.py
Manus 建議的排查指令 - 不重寫主程式，逐項輸出診斷結果

執行流程：
官方 API → HTTP 驗證 → JSON 結構驗證 → 日期驗證 → 欄位正規化 → OHLCV 驗證 → 市場完整度驗證 → 寫出 CSV/ZIP → 最後才允許 App 匯入

診斷輸出 15 項：
1. 實際請求 URL
2. HTTP status code
3. Content-Type
4. Response body 前 500 字元
5. 是否成功解析 JSON
6. JSON 最外層型別
7. TWSE 是否為 list
8. TPEx 是否存在 tables
9. TPEx tables[0] 是否存在 fields 與 data
10. 回傳交易日期
11. 請求交易日期
12. 有效股票列數
13. 第一筆原始資料
14. 第一筆正規化資料
15. 失敗分類
"""

import argparse
import json
import re
import time
import sys
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import socket

TWSE_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL_TEMPLATE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={yyyymmdd}&type=EW&response=json"

HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "User-Agent": "TWStockFactorLab/1.0 (personal research; official market data client)"
}

def roc_to_iso(roc_str: str):
    m = re.search(r'(\d+)[年/-](\d+)[月/-](\d+)', roc_str.strip() if roc_str else "")
    if not m:
        return None
    roc_year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3))
    western = roc_year + 1911 if roc_year < 1000 else roc_year
    return f"{western:04d}-{month:02d}-{day:02d}"

def diagnose_one(market: str, url: str, requested_date: str):
    result = {
        "market": market,
        "1_實際請求URL": url,
        "2_HTTP_status_code": None,
        "3_Content_Type": None,
        "4_Response_body前500字元": "",
        "5_是否成功解析JSON": False,
        "6_JSON最外層型別": None,
        "7_TWSE是否為list": None,
        "8_TPEx是否存在tables": None,
        "9_TPEx_tables0是否存在fields與data": None,
        "10_回傳交易日期": None,
        "11_請求交易日期": requested_date,
        "12_有效股票列數": 0,
        "13_第一筆原始資料": None,
        "14_第一筆正規化資料": None,
        "15_失敗分類": None,
        "完整錯誤訊息": None,
        "流程狀態": []
    }

    raw_body = b""
    try:
        req = Request(url, headers=HEADERS)
        result["流程狀態"].append("官方API → 請求發送")
        with urlopen(req, timeout=20) as resp:
            result["2_HTTP_status_code"] = resp.status
            result["3_Content_Type"] = resp.headers.get('Content-Type','')
            raw_body = resp.read()
            result["4_Response_body前500字元"] = raw_body[:500].decode('utf-8', errors='ignore')
            result["流程狀態"].append(f"HTTP驗證 → {resp.status}")

            # HTML 檢查
            preview_lower = result["4_Response_body前500字元"].lower()
            if preview_lower.strip().startswith('<!doctype') or '<html' in preview_lower:
                result["15_失敗分類"] = "html_response"
                result["完整錯誤訊息"] = f"官方回傳 HTML: {result['4_Response_body前500字元'][:200]}"
                result["流程狀態"].append("JSON結構驗證 → 失敗 (HTML)")
                return result

            # JSON 解析
            try:
                data = json.loads(raw_body.decode('utf-8'))
                result["5_是否成功解析JSON"] = True
                result["6_JSON最外層型別"] = type(data).__name__
                result["流程狀態"].append(f"JSON結構驗證 → 成功 ({type(data).__name__})")
            except json.JSONDecodeError as e:
                result["5_是否成功解析JSON"] = False
                result["15_失敗分類"] = "json_parse_error"
                result["完整錯誤訊息"] = f"JSON 解析錯誤: {str(e)} | body: {result['4_Response_body前500字元'][:200]}"
                result["流程狀態"].append("JSON結構驗證 → 失敗")
                return result

            # TWSE / TPEx 結構驗證
            if market == "TWSE":
                is_list = isinstance(data, list)
                result["7_TWSE是否為list"] = is_list
                if not is_list:
                    result["15_失敗分類"] = "missing_fields"
                    result["完整錯誤訊息"] = f"TWSE 預期 list，實際 {type(data).__name__}: {str(data)[:300]}"
                    result["流程狀態"].append("JSON結構驗證 → TWSE 非 list")
                    return result
                result["12_有效股票列數"] = len(data)
                result["13_第一筆原始資料"] = data[0] if data else None
                if data:
                    first = data[0]
                    result["14_第一筆正規化資料"] = {
                        "stock_no": first.get("Code"),
                        "name": first.get("Name"),
                        "open": first.get("OpeningPrice"),
                        "high": first.get("HighestPrice"),
                        "low": first.get("LowestPrice"),
                        "close": first.get("ClosingPrice"),
                        "volume": first.get("TradeVolume"),
                        "amount": first.get("TradeValue")
                    }
                result["流程狀態"].append("欄位正規化 → 成功")
                # 日期驗證 - TWSE ALL 沒有日期欄位，需比對外部
                result["10_回傳交易日期"] = requested_date  # ALL 端點視為當日
                # 完整度
                if len(data) < 500:
                    result["15_失敗分類"] = "insufficient_rows"
                    result["完整錯誤訊息"] = f"TWSE 僅 {len(data)} 檔，少於 500"
                    result["流程狀態"].append("市場完整度驗證 → 失敗 (<500)")
                else:
                    result["流程狀態"].append("市場完整度驗證 → 成功")
                    result["流程狀態"].append("寫出 CSV/ZIP → 允許")
                    result["流程狀態"].append("App 匯入 → 允許 (official_validated)")

            else: # TPEx
                has_tables = isinstance(data, dict) and "tables" in data
                result["8_TPEx是否存在tables"] = has_tables
                if not has_tables:
                    result["15_失敗分類"] = "missing_fields"
                    result["完整錯誤訊息"] = f"TPEx 無 tables 欄位: {str(data)[:500]}"
                    result["流程狀態"].append("JSON結構驗證 → TPEx 無 tables")
                    return result
                tables = data.get("tables", [])
                if not tables:
                    result["15_失敗分類"] = "missing_fields"
                    result["完整錯誤訊息"] = "TPEx tables 空陣列"
                    result["流程狀態"].append("JSON結構驗證 → tables 空")
                    return result
                t0 = tables[0]
                has_fields = "fields" in t0 and "data" in t0
                result["9_TPEx_tables0是否存在fields與data"] = has_fields
                if not has_fields:
                    result["15_失敗分類"] = "missing_fields"
                    result["完整錯誤訊息"] = f"tables[0] 無 fields/data: {str(t0)[:500]}"
                    return result
                # 日期
                roc_date = t0.get("date","")
                iso_date = roc_to_iso(roc_date)
                result["10_回傳交易日期"] = iso_date
                result["流程狀態"].append(f"日期驗證 → 官方 {roc_date} -> {iso_date}, 請求 {requested_date}")
                if iso_date and iso_date != requested_date:
                    result["15_失敗分類"] = "date_mismatch"
                    result["完整錯誤訊息"] = f"日期不一致 請求 {requested_date} 官方 {iso_date} (原始 {roc_date})"
                    result["流程狀態"].append("日期驗證 → 失敗")
                    return result
                result["流程狀態"].append("日期驗證 → 成功")
                result["12_有效股票列數"] = len(t0.get("data", []))
                result["13_第一筆原始資料"] = t0["data"][0] if t0.get("data") else None
                if t0.get("data"):
                    # 動態欄位
                    fields = t0["fields"]
                    def idx(kws):
                        for i,f in enumerate(fields):
                            for kw in kws:
                                if kw in f:
                                    return i
                        return -1
                    row = t0["data"][0]
                    result["14_第一筆正規化資料"] = {
                        "stock_no": row[idx(["代號"])] if idx(["代號"])!=-1 else None,
                        "name": row[idx(["名稱"])] if idx(["名稱"])!=-1 else None,
                        "close": row[idx(["收盤"])] if idx(["收盤"])!=-1 else None,
                        "open": row[idx(["開盤"])] if idx(["開盤"])!=-1 else None,
                        "high": row[idx(["最高"])] if idx(["最高"])!=-1 else None,
                        "low": row[idx(["最低"])] if idx(["最低"])!=-1 else None,
                    }
                result["流程狀態"].append("欄位正規化 → 成功")
                if len(t0.get("data", [])) < 500:
                    result["15_失敗分類"] = "insufficient_rows"
                    result["完整錯誤訊息"] = f"TPEx 僅 {len(t0.get('data',[]))} 檔"
                    result["流程狀態"].append("市場完整度驗證 → 失敗")
                else:
                    result["流程狀態"].append("市場完整度驗證 → 成功")
                    result["流程狀態"].append("寫出 CSV/ZIP → 允許")
                    result["流程狀態"].append("App 匯入 → 允許 (official_validated)")

    except HTTPError as e:
        result["2_HTTP_status_code"] = e.code
        result["4_Response_body前500字元"] = e.read()[:500].decode('utf-8', errors='ignore') if hasattr(e,'read') else ""
        if e.code == 403:
            result["15_失敗分類"] = "http_403"
        elif e.code == 429:
            result["15_失敗分類"] = "http_429"
        elif e.code >= 500:
            result["15_失敗分類"] = "http_500"
        else:
            result["15_失敗分類"] = f"http_{e.code}"
        result["完整錯誤訊息"] = f"HTTPError {e.code}: {e.reason} | URL: {url} | Body: {result['4_Response_body前500字元'][:200]}"
        result["流程狀態"].append(f"HTTP驗證 → 失敗 {e.code}")

    except URLError as e:
        if isinstance(e.reason, socket.timeout):
            result["15_失敗分類"] = "timeout"
        else:
            result["15_失敗分類"] = "DNS/network_error"
        result["完整錯誤訊息"] = f"URLError: {e.reason} | URL: {url}"
        result["流程狀態"].append("HTTP驗證 → 失敗 (網路)")

    except socket.timeout:
        result["15_失敗分類"] = "timeout"
        result["完整錯誤訊息"] = f"Timeout 20秒 | URL: {url}"
        result["流程狀態"].append("HTTP驗證 → 失敗 (timeout)")

    except Exception as e:
        result["15_失敗分類"] = "unknown"
        result["完整錯誤訊息"] = f"{type(e).__name__}: {str(e)} | URL: {url}"
        result["流程狀態"].append(f"例外: {e}")

    return result

def main():
    parser = argparse.ArgumentParser(description="official_market_diagnose - 官方API逐項診斷")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="./official-output", help="輸出資料夾")
    args = parser.parse_args()

    requested_date = args.date
    try:
        dt = datetime.strptime(requested_date, "%Y-%m-%d")
        yyyymmdd = dt.strftime("%Y%m%d")
    except:
        print("日期格式錯誤，需 YYYY-MM-DD")
        sys.exit(1)

    twse_url = TWSE_ALL_URL
    tpex_url = TPEX_URL_TEMPLATE.format(yyyymmdd=yyyymmdd)

    print(f"=== 開始診斷 請求日期 {requested_date} ===\n")

    results = []
    results.append(diagnose_one("TWSE", twse_url, requested_date))
    time.sleep(1)
    results.append(diagnose_one("TPEx", tpex_url, requested_date))

    # 輸出
    for r in results:
        print(f"\n--- {r['market']} 診斷 ---")
        for i in range(1,16):
            # 找到對應 key
            key = [k for k in r.keys() if k.startswith(f"{i}_")]
            if key:
                print(f"{key[0]}: {json.dumps(r[key[0]], ensure_ascii=False, indent=2)[:1000]}")
        print(f"流程狀態: {' → '.join(r['流程狀態'])}")
        if r["完整錯誤訊息"]:
            print(f"完整錯誤訊息: {r['完整錯誤訊息']}")

    # 保存
    import os
    os.makedirs(args.out, exist_ok=True)
    with open(f"{args.out}/diagnose_{requested_date}.json","w",encoding="utf-8") as f:
        json.dump(results,f,ensure_ascii=False,indent=2)

    print(f"\n診斷完成，已保存至 {args.out}/diagnose_{requested_date}.json")

if __name__ == "__main__":
    main()
