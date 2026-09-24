#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
official_market_fetch.py
台股研究室資料更新模組 - 只使用 TWSE / TPEx 官方來源
Python 3.11, 僅使用標準函式庫 (無第三方依賴)

資料來源：
- TWSE: https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
- TPEx: https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=YYYYMMDD&type=EW&response=json
- TWSE 個股月資料 (備用驗證): https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=CODE&response=json
"""

import argparse
import csv
import json
import re
import hashlib
import zipfile
import time
import random
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# === 常數 ===
TWSE_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL_URL_TEMPLATE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={yyyymmdd}&type=EW&response=json"

HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "User-Agent": "TWStockFactorLab/1.0 (personal research; official market data client)"
}

TIMEOUT = 20
RETRY_BACKOFF = [2, 4, 8]
REQUEST_INTERVAL = (1, 3)  # 秒

CSV_FIELDS = ["stock_no","name","trade_date","open","high","low","close","volume","amount","exchange","source"]
CSV_FIELDS_CN = {
    "stock_no":"證券代號",
    "name":"證券名稱",
    "trade_date":"交易日期",
    "open":"開盤價",
    "high":"最高價",
    "low":"最低價",
    "close":"收盤價",
    "volume":"成交股數",
    "amount":"成交金額",
    "exchange":"市場別",
    "source":"來源"
}

# === 工具函式 ===
def roc_to_iso(roc_str: str) -> str:
    """115年09月23日 -> 2026-09-23"""
    m = re.search(r'(\d+)[年/-](\d+)[月/-](\d+)', roc_str.strip())
    if not m:
        return roc_str
    roc_year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3))
    # 民國年 + 1911 = 西元
    western = roc_year + 1911 if roc_year < 1000 else roc_year
    try:
        return f"{western:04d}-{month:02d}-{day:02d}"
    except:
        return roc_str

def is_four_digit_regular(code: str) -> bool:
    """只接受四位數普通股票"""
    if not re.fullmatch(r'\d{4}', code or ""):
        return False
    # 排除常見非普通股開頭 (可依實際再擴充)
    # 00xx 多為 ETF, 00 開頭排除
    if code.startswith('00'):
        return False
    return True

def is_non_stock_name(name: str) -> bool:
    """排除指數、權證、ETF、ETN"""
    if not name:
        return False
    keywords = ["ETF","ETN","指數","權證","KY","DR","TDR"]
    # 中文 ETF 常含「元大」「富邦」但仍保留 0050 等已在 code 排除，此處保守
    for kw in keywords:
        if kw in name:
            # 0050 例外已排除，此處若出現則排除
            return True
    return False

def parse_number(val, field_name=""):
    """官方空值不可補0，保留 null，字串去逗號"""
    if val is None:
        return None
    if isinstance(val, (int,float)):
        return val
    s = str(val).strip().replace(',','').replace('X','').replace('x','').replace('-','').strip()
    # TWSE 有時用 "--" 或 "---" 表示無
    if s == "" or s in ["--","---","--.--","null","None"]:
        return None
    # 移除 + 
    s = s.lstrip('+')
    try:
        if '.' in s:
            return float(s)
        else:
            return int(s)
    except:
        # 保留原始字串讓驗證報錯
        return None

def safe_sleep():
    time.sleep(random.uniform(*REQUEST_INTERVAL))

# === API 請求 ===
def request_json(url: str, log_list: list):
    """請求 JSON，處理 403/429/500，退避 2,4,8 秒"""
    last_error = None
    for attempt, backoff in enumerate([0] + RETRY_BACKOFF):
        if backoff > 0:
            time.sleep(backoff)
        try:
            req = Request(url, headers=HEADERS)
            start = datetime.now()
            with urlopen(req, timeout=TIMEOUT) as resp:
                status = resp.status
                body = resp.read()
                # 檢查是否 HTML
                content_type = resp.headers.get('Content-Type','')
                text_preview = body[:500].decode('utf-8', errors='ignore')
                log_entry = {
                    "url": url,
                    "status": status,
                    "content_type": content_type,
                    "timestamp": datetime.now().isoformat(),
                    "attempt": attempt+1,
                    "preview": text_preview[:200]
                }
                log_list.append(log_entry)

                if status in (403,429,500):
                    last_error = f"HTTP {status}"
                    if status == 429:
                        # 429 不可快速重試，已退避
                        continue
                    if status == 403:
                        # 記錄後重試
                        continue
                    continue

                # 檢查是否 HTML
                if text_preview.strip().lower().startswith('<!doctype') or '<html' in text_preview.lower():
                    raise ValueError(f"官方回傳 HTML，非 JSON: {text_preview[:100]}")

                # 解析 JSON
                try:
                    data = json.loads(body.decode('utf-8'))
                    return data, status, None
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON 解析錯誤: {e}, 內容: {text_preview[:200]}")

        except HTTPError as e:
            last_error = f"HTTPError {e.code}: {e.reason}"
            log_list.append({"url": url, "error": last_error, "attempt": attempt+1, "timestamp": datetime.now().isoformat()})
            if e.code in (403,429,500):
                if e.code == 429 and attempt < len(RETRY_BACKOFF):
                    continue
                if e.code in (403,500) and attempt < len(RETRY_BACKOFF):
                    continue
            return None, e.code, last_error
        except (URLError, ValueError, Exception) as e:
            last_error = str(e)
            log_list.append({"url": url, "error": last_error, "attempt": attempt+1, "timestamp": datetime.now().isoformat()})
            if attempt < len(RETRY_BACKOFF):
                continue
            return None, 0, last_error

    return None, 0, last_error

# === TWSE 解析 ===
def parse_twse(data, requested_date: str):
    """data 為 list[dict] Code, Name..."""
    rows = []
    warnings = []
    errors = []

    if not isinstance(data, list):
        errors.append({"type":"official_api_error","message":f"TWSE 回應非 list: {type(data)}"})
        return rows, warnings, errors

    seen = set()
    for item in data:
        code = str(item.get('Code','')).strip()
        name = str(item.get('Name','')).strip()

        if not is_four_digit_regular(code):
            continue
        if is_non_stock_name(name):
            continue
        if code in seen:
            errors.append({"type":"duplicate_stock_no","stock_no":code,"market":"TWSE"})
            continue
        seen.add(code)

        open_p = parse_number(item.get('OpeningPrice'))
        high_p = parse_number(item.get('HighestPrice'))
        low_p = parse_number(item.get('LowestPrice'))
        close_p = parse_number(item.get('ClosingPrice'))
        vol = parse_number(item.get('TradeVolume'))
        amt = parse_number(item.get('TradeValue'))

        # 官方空值不可補0
        if open_p is None or high_p is None or low_p is None or close_p is None:
            warnings.append({"stock_no":code,"type":"null_ohlc","message":f"OHLC 空值 開:{open_p} 高:{high_p} 低:{low_p} 收:{close_p}"})

        # 驗證
        if open_p is not None and open_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"open","value":open_p})
        if high_p is not None and high_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"high","value":high_p})
        if low_p is not None and low_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"low","value":low_p})
        if close_p is not None and close_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"close","value":close_p})
        if low_p is not None and high_p is not None and low_p > high_p:
            errors.append({"stock_no":code,"type":"low_higher_than_high","low":low_p,"high":high_p})
        if vol is not None and vol < 0:
            errors.append({"stock_no":code,"type":"negative_volume","volume":vol})

        rows.append({
            "stock_no": code,
            "name": name,
            "trade_date": requested_date,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
            "amount": amt,
            "exchange": "TWSE",
            "source": "twse_stock_day_all"
        })

    return rows, warnings, errors

# === TPEx 解析 ===
def parse_tpex(data, requested_date: str):
    rows = []
    warnings = []
    errors = []

    if not isinstance(data, dict):
        errors.append({"type":"official_api_error","message":f"TPEx 回應非 dict"})
        return rows, warnings, errors, None

    tables = data.get('tables', [])
    if not tables:
        errors.append({"type":"no_trading_data","message":"TPEx tables 空"})
        return rows, warnings, errors, None

    # 取第一個 table (EW)
    table = tables[0] if tables else {}
    roc_date = table.get('date','')
    iso_date = roc_to_iso(roc_date) if roc_date else None

    # 檢查日期是否等於請求日期
    date_mismatch = False
    if iso_date and iso_date != requested_date:
        errors.append({"type":"date_mismatch","requested":requested_date,"official":iso_date,"roc_raw":roc_date})
        date_mismatch = True

    fields = table.get('fields', [])
    data_rows = table.get('data', [])

    if not fields or not data_rows:
        errors.append({"type":"no_trading_data","message":"TPEx fields 或 data 空"})
        return rows, warnings, errors, iso_date

    # 動態尋找欄位位置
    def find_field_idx(keywords):
        for idx, f in enumerate(fields):
            for kw in keywords:
                if kw in f:
                    return idx
        return -1

    idx_code = find_field_idx(["代號","Code"])
    idx_name = find_field_idx(["名稱","Name"])
    idx_close = find_field_idx(["收盤","Closing"])
    idx_open = find_field_idx(["開盤","Opening"])
    idx_high = find_field_idx(["最高","Highest"])
    idx_low = find_field_idx(["最低","Lowest"])
    idx_vol = find_field_idx(["成交股數","Volume"])
    idx_amt = find_field_idx(["成交金額","Amount","Value"])

    if idx_code == -1:
        errors.append({"type":"missing_field","field":"代號"})
        return rows, warnings, errors, iso_date

    seen = set()
    for row in data_rows:
        if not isinstance(row, list) or len(row) <= max(idx_code, idx_name, 0):
            continue
        code = str(row[idx_code]).strip() if idx_code != -1 and idx_code < len(row) else ""
        name = str(row[idx_name]).strip() if idx_name != -1 and idx_name < len(row) else ""

        if not is_four_digit_regular(code):
            continue
        if is_non_stock_name(name):
            continue
        if code in seen:
            errors.append({"type":"duplicate_stock_no","stock_no":code,"market":"TPEx"})
            continue
        seen.add(code)

        open_p = parse_number(row[idx_open]) if idx_open != -1 and idx_open < len(row) else None
        high_p = parse_number(row[idx_high]) if idx_high != -1 and idx_high < len(row) else None
        low_p = parse_number(row[idx_low]) if idx_low != -1 and idx_low < len(row) else None
        close_p = parse_number(row[idx_close]) if idx_close != -1 and idx_close < len(row) else None
        vol = parse_number(row[idx_vol]) if idx_vol != -1 and idx_vol < len(row) else None
        amt = parse_number(row[idx_amt]) if idx_amt != -1 and idx_amt < len(row) else None

        if open_p is None or high_p is None or low_p is None or close_p is None:
            warnings.append({"stock_no":code,"type":"null_ohlc","message":f"TPEx OHLC 空值"})

        if open_p is not None and open_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"open"})
        if high_p is not None and high_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"high"})
        if low_p is not None and low_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"low"})
        if close_p is not None and close_p <= 0:
            errors.append({"stock_no":code,"type":"ohlc_non_positive","field":"close"})
        if low_p is not None and high_p is not None and low_p > high_p:
            errors.append({"stock_no":code,"type":"low_higher_than_high","stock_no":code})
        if vol is not None and vol < 0:
            errors.append({"stock_no":code,"type":"negative_volume"})

        rows.append({
            "stock_no": code,
            "name": name,
            "trade_date": iso_date or requested_date,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
            "amount": amt,
            "exchange": "TPEx",
            "source": "tpex_daily_close"
        })

    return rows, warnings, errors, iso_date

# === 主流程 ===
def main():
    parser = argparse.ArgumentParser(description="official_market_fetch - TWSE/TPEx 官方資料抓取")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD, 例如 2026-09-23")
    parser.add_argument("--out", required=True, help="輸出資料夾 ./official-output")
    args = parser.parse_args()

    requested_date = args.date
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 驗證日期格式
    try:
        dt = datetime.strptime(requested_date, "%Y-%m-%d")
    except:
        print(json.dumps({"status":"validation_failed","message":"日期格式錯誤，需 YYYY-MM-DD"}))
        sys.exit(1)

    # 週末檢查
    if dt.weekday() >= 5:
        summary = {
            "status":"market_not_closed",
            "requested_date": requested_date,
            "twse_rows":0,"tpex_rows":0,
            "twse_source":"twse_stock_day_all","tpex_source":"tpex_daily_close",
            "warnings":[{"type":"weekend","message":f"{requested_date} 為週末，無交易"}],
            "errors":[]
        }
        with open(out_dir/"summary.json","w",encoding="utf-8") as f:
            json.dump(summary,f,ensure_ascii=False,indent=2)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return

    yyyymmdd = dt.strftime("%Y%m%d")

    request_log = []
    all_warnings = []
    all_errors = []

    twse_rows = []
    tpex_rows = []
    twse_iso_date = None
    tpex_iso_date = None

    # === TWSE ===
    print(f"[TWSE] 抓取 {TWSE_ALL_URL}")
    twse_data, twse_status, twse_err = request_json(TWSE_ALL_URL, request_log)
    safe_sleep()
    if twse_data is None:
        all_errors.append({"market":"TWSE","type":"official_api_error","http_status":twse_status,"message":twse_err})
        twse_rows = []
    else:
        rows, warns, errs = parse_twse(twse_data, requested_date)
        twse_rows = rows
        all_warnings.extend(warns)
        all_errors.extend(errs)
        # 檢查 <500
        if len(rows) < 500:
            all_errors.append({"market":"TWSE","type":"insufficient_rows","rows":len(rows),"message":f"TWSE 回傳僅 {len(rows)} 檔，少於 500"})

    # === TPEx ===
    tpex_url = TPEX_ALL_URL_TEMPLATE.format(yyyymmdd=yyyymmdd)
    print(f"[TPEx] 抓取 {tpex_url}")
    tpex_data, tpex_status, tpex_err = request_json(tpex_url, request_log)
    safe_sleep()
    if tpex_data is None:
        all_errors.append({"market":"TPEx","type":"official_api_error","http_status":tpex_status,"message":tpex_err})
        tpex_rows = []
    else:
        rows, warns, errs, iso_date = parse_tpex(tpex_data, requested_date)
        tpex_rows = rows
        tpex_iso_date = iso_date
        all_warnings.extend(warns)
        all_errors.extend(errs)
        if len(rows) < 500:
            all_errors.append({"market":"TPEx","type":"insufficient_rows","rows":len(rows),"message":f"TPEx 回傳僅 {len(rows)} 檔，少於 500"})

    # === 判斷狀態 ===
    status = "success"
    if any(e.get("type")=="official_api_error" for e in all_errors):
        status = "official_api_error"
    if len(twse_rows) < 500 or len(tpex_rows) < 500:
        # 若無交易資料
        if len(twse_rows)==0 and len(tpex_rows)==0:
            status = "no_trading_data"
        elif status == "success":
            # 仍標記 success 但 warnings
            pass
    if any(e.get("type") in ("low_higher_than_high","ohlc_non_positive","negative_volume","duplicate_stock_no") for e in all_errors):
        if status == "success":
            status = "validation_failed"

    # 檢查是否尚未收盤：API 回傳空
    if len(twse_rows)==0 and len(tpex_rows)==0 and status not in ("market_not_closed",):
        # 若平日卻無資料，可能是尚未收盤
        # 不直接判定為一般失敗，給明確狀態
        if dt.date() == datetime.now().date():
            status = "market_not_closed"

    # === 寫 CSV ===
    twse_csv_path = out_dir / f"twse_{yyyymmdd}.csv"
    tpex_csv_path = out_dir / f"tpex_{yyyymmdd}.csv"

    # 先寫臨時
    tmp_dir = out_dir / "tmp_csv"
    tmp_dir.mkdir(exist_ok=True)

    def write_csv(rows, path):
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in sorted(rows, key=lambda x: x["stock_no"]):
                # 空值保留空白
                out = {}
                for k in CSV_FIELDS:
                    v = r.get(k)
                    if v is None:
                        out[k] = ""
                    else:
                        out[k] = v
                w.writerow(out)

    write_csv(twse_rows, tmp_dir / f"{yyyymmdd}_twse.csv")
    write_csv(tpex_rows, tmp_dir / f"{yyyymmdd}_tpex.csv")

    # === ZIP ===
    zip_path = out_dir / "official-market-history.zip"
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zf:
        # 禁止路徑穿越檢查
        twse_arc = f"twse/{yyyymmdd}.csv"
        tpex_arc = f"tpex/{yyyymmdd}.csv"
        # 確保無 .. 或 絕對路徑
        for arc in [twse_arc, tpex_arc]:
            if ".." in arc or arc.startswith("/"):
                raise ValueError(f"ZIP 路徑穿越: {arc}")
        zf.write(tmp_dir / f"{yyyymmdd}_twse.csv", twse_arc)
        zf.write(tmp_dir / f"{yyyymmdd}_tpex.csv", tpex_arc)

    # 正式移到 out_dir/twse, tpex 結構展示用 (實際交付只要求 ZIP 內結構)
    (out_dir / "twse").mkdir(exist_ok=True)
    (out_dir / "tpex").mkdir(exist_ok=True)
    # 保留一份在 out_dir 根部以便檢查
    write_csv(twse_rows, out_dir / f"twse/{yyyymmdd}.csv")
    write_csv(tpex_rows, out_dir / f"tpex/{yyyymmdd}.csv")

    # === summary.json ===
    summary = {
        "status": status,
        "requested_date": requested_date,
        "twse_rows": len(twse_rows),
        "tpex_rows": len(tpex_rows),
        "twse_source": "twse_stock_day_all",
        "tpex_source": "tpex_daily_close",
        "warnings": all_warnings,
        "errors": all_errors
    }
    with open(out_dir/"summary.json","w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)

    # === coverage.csv ===
    def calc_sha256(file_path):
        h = hashlib.sha256()
        with open(file_path,"rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    twse_sha = calc_sha256(out_dir / f"twse/{yyyymmdd}.csv") if (out_dir / f"twse/{yyyymmdd}.csv").exists() else ""
    tpex_sha = calc_sha256(out_dir / f"tpex/{yyyymmdd}.csv") if (out_dir / f"tpex/{yyyymmdd}.csv").exists() else ""

    with open(out_dir/"coverage.csv","w",newline="",encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["市場別","資料日期","有效股票數","第一筆股票代號","最後一筆股票代號","完整度狀態","來源","SHA-256"])
        def first_last(rows):
            if not rows:
                return "",""
            sorted_rows = sorted(rows, key=lambda x: x["stock_no"])
            return sorted_rows[0]["stock_no"], sorted_rows[-1]["stock_no"]
        twse_first, twse_last = first_last(twse_rows)
        tpex_first, tpex_last = first_last(tpex_rows)
        w.writerow(["TWSE",requested_date,len(twse_rows),twse_first,twse_last,"完整" if len(twse_rows)>=500 else "不完整","twse_stock_day_all",twse_sha])
        w.writerow(["TPEx",requested_date,len(tpex_rows),tpex_first,tpex_last,"完整" if len(tpex_rows)>=500 else "不完整","tpex_daily_close",tpex_sha])

    # === errors.csv ===
    with open(out_dir/"errors.csv","w",newline="",encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["market","type","stock_no","message","value"])
        w.writeheader()
        for e in all_errors:
            w.writerow({
                "market": e.get("market",""),
                "type": e.get("type",""),
                "stock_no": e.get("stock_no",""),
                "message": json.dumps(e,ensure_ascii=False),
                "value": e.get("value","")
            })

    # === request-log.json ===
    with open(out_dir/"request-log.json","w",encoding="utf-8") as f:
        json.dump(request_log,f,ensure_ascii=False,indent=2)

    # === sha256.txt ===
    zip_sha = calc_sha256(zip_path)
    with open(out_dir/"sha256.txt","w",encoding="utf-8") as f:
        f.write(f"{zip_sha}  official-market-history.zip\n")
        f.write(f"{twse_sha}  twse/{yyyymmdd}.csv\n")
        f.write(f"{tpex_sha}  tpex/{yyyymmdd}.csv\n")

    print(f"\n完成: {zip_path}")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
