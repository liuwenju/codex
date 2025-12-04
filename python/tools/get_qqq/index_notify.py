#!/usr/bin/env python3
"""
index_notify.py
自动获取 Nasdaq-100 和 S&P500 最新指数，并通过 ServerChan 推送。
支持多数据源、重试、代理、超时和简单缓存。
配置通过环境变量。

ENV vars:
  SERVERCHAN_SCKEY   - 必需: ServerChan 的 SCKEY (SCT 或 老版 SCKEY 均支持)
  PROXY_URL          - 可选: HTTP/HTTPS 代理 (例如 http://127.0.0.1:7890)
  ALPHAVANTAGE_KEY   - 可选: 如果想用 AlphaVantage 之类的 API（这里未实现），留作扩展
  TIMEOUT            - 可选: 请求超时秒数 (默认 6)
  USER_AGENT         - 可选: 自定义 UA，默认使用常见浏览器 UA
  PREFERRED_ORDER    - 可选: 用逗号分隔的数据源优先级, e.g. "yahoo,stooq"
"""
from __future__ import annotations
import os, sys, time, json, random, logging, traceback
from typing import Optional, Dict, Any
import requests
from datetime import datetime, timezone

# ----- config -----
SERVERCHAN_SCKEY = os.getenv("SERVERCHAN_SCKEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
TIMEOUT = float(os.getenv("TIMEOUT", "6"))
USER_AGENT = os.getenv("USER_AGENT",
                       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
PREFERRED_ORDER = os.getenv("PREFERRED_ORDER", "yahoo,stooq").split(",")

# indices to fetch (key -> display name & symbols per source)
INDICES = {
    "nasdaq100": {"name": "Nasdaq-100", "yahoo": "^NDX", "stooq": "^NDX", "alt_symbol": "NDX"},
    "sp500": {"name": "S&P 500", "yahoo": "^GSPC", "stooq": "^SPX", "alt_symbol": "SPX"},
}

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# session
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
if PROXY_URL:
    session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})

# helpers
def now_iso(): return datetime.now(timezone.utc).astimezone().isoformat()

def send_serverchan(title: str, content_md: str) -> bool:
    if not SERVERCHAN_SCKEY:
        logging.error("SERVERCHAN_SCKEY not set, skip push.")
        return False
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SCKEY}.send"
    data = {"title": title, "desp": content_md}
    try:
        resp = session.post(url, data=data, timeout=TIMEOUT)
        logging.info("ServerChan resp: %s %s", resp.status_code, resp.text[:400])
        return resp.ok
    except Exception as e:
        logging.exception("ServerChan request failed: %s", e)
        return False

# Data source: Yahoo Finance (JSON quote endpoint)
def fetch_from_yahoo(symbols: list[str]) -> Dict[str, dict]:
    """
    Query Yahoo finance quote API for multiple symbols.
    Returns dict keyed by symbol (e.g. ^NDX) with fields: price, change, pct, time, raw
    """
    q = ",".join(symbols)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={requests.utils.quote(q)}"
    logging.debug("Yahoo URL: %s", url)
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = {}
    for qd in data.get("quoteResponse", {}).get("result", []):
        sym = qd.get("symbol")
        price = qd.get("regularMarketPrice") or qd.get("previousClose")
        change = qd.get("regularMarketChange")
        pct = qd.get("regularMarketChangePercent")
        ts = qd.get("regularMarketTime")
        tstr = datetime.fromtimestamp(ts).astimezone().isoformat() if ts else now_iso()
        out[sym] = {"price": price, "change": change, "pct": pct, "time": tstr, "raw": qd}
    return out

# Data source: Stooq CSV (daily). use descent fallback: latest non-empty close
def fetch_from_stooq(symbol: str) -> Optional[dict]:
    """
    Fetch latest daily data from Stooq CSV endpoint.
    Example URL: https://stooq.com/q/d/l/?s=^spx&i=d
    Returns dict like {"price": ..., "date": ..., "raw_line": ...} or None.
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    logging.debug("Stooq URL: %s", url)
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text.strip().splitlines()
        if not text or len(text) < 2:
            logging.warning("Stooq returned no data for %s", symbol)
            return None
        # CSV header: Date,Open,High,Low,Close,Volume
        # take the last non-empty data row
        for line in reversed(text[1:]):
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 5:
                    date = parts[0]
                    close = parts[4]
                    try:
                        price = float(close)
                        return {"price": price, "date": date, "raw_line": line}
                    except:
                        continue
        return None
    except Exception as e:
        logging.exception("Stooq fetch failed for %s: %s", symbol, e)
        return None

# primary orchestrator: try preferred sources in order per index
def get_index_values() -> dict:
    results = {}
    # build yahoo symbol list for those indices that have yahoo mapping
    yahoo_symbols = []
    mapping = {}
    for k, meta in INDICES.items():
        s = meta.get("yahoo")
        if s:
            yahoo_symbols.append(s)
            mapping[s] = k
    # attempt per PREFERRED_ORDER
    tried = set()
    for src in PREFERRED_ORDER:
        src = src.strip().lower()
        if src == "yahoo":
            try:
                logging.info("Trying Yahoo for symbols: %s", yahoo_symbols)
                yres = fetch_from_yahoo(yahoo_symbols)
                for sym, data in yres.items():
                    idx_key = mapping.get(sym)
                    if idx_key and (idx_key not in results):
                        results[idx_key] = {"source": "yahoo", "symbol": sym, **data}
                tried.add("yahoo")
            except Exception:
                logging.exception("Yahoo source failed")
        elif src == "stooq":
            # for each index that needs value and not set, query stooq symbol
            for k, meta in INDICES.items():
                if k in results: continue
                s = meta.get("stooq")
                if not s: continue
                logging.info("Trying Stooq for %s (%s)", k, s)
                res = fetch_from_stooq(s)
                if res:
                    results[k] = {"source": "stooq", "symbol": s, "price": res["price"], "time": res.get("date")}
            tried.add("stooq")
        # (can add more sources like alphavantage/finnhub here)
    # final fallback: try each source we didn't try yet once more randomly
    if len(results) < len(INDICES):
        logging.info("Not all indices fetched. Trying remaining sources randomly as fallback.")
        for k, meta in INDICES.items():
            if k in results: continue
            # try yahoo single
            s = meta.get("yahoo")
            if s and "yahoo" not in tried:
                try:
                    y = fetch_from_yahoo([s])
                    if s in y:
                        results[k] = {"source": "yahoo", "symbol": s, **y[s]}
                        continue
                except:
                    pass
            s2 = meta.get("stooq")
            if s2 and "stooq" not in tried:
                v = fetch_from_stooq(s2)
                if v:
                    results[k] = {"source": "stooq", "symbol": s2, "price": v["price"], "time": v.get("date")}
                    continue
    return results

def build_message(results: dict) -> (str, str):
    title = f"指数快讯 — {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
    lines = []
    md = []
    md.append(f"**{title}**\n")
    for k, meta in INDICES.items():
        name = meta["name"]
        r = results.get(k)
        if not r:
            lines.append(f"{name}: ❌ 获取失败")
            md.append(f"- **{name}**: ❌ 获取失败")
            continue
        price = r.get("price")
        change = r.get("change")
        pct = r.get("pct")
        t = r.get("time")
        src = r.get("source")
        pstr = f"{price:.2f}" if isinstance(price, (int, float)) else str(price)
        chstr = ""
        if change is not None and pct is not None:
            chstr = f"{change:+.2f} ({pct:+.2f}%)"
        lines.append(f"{name}: {pstr} {chstr}  [{src}] @{t}")
        md.append(f"- **{name}**: `{pstr}` {chstr}  （来源：`{src}`，时间：{t}）")
    md.append("\n> 数据来源示例：Yahoo Finance / Stooq。")
    return title, "\n\n".join(md) + "\n\n----\n" + "`Generated at " + now_iso() + "`"

def main():
    try:
        results = get_index_values()
        logging.info("Fetched results: %s", results)
        title, content = build_message(results)
        ok = send_serverchan(title, content)
        if ok:
            logging.info("Push success")
        else:
            logging.error("Push failed")
    except Exception as e:
        logging.exception("Unhandled error: %s", e)
        # attempt to push error to ServerChan if possible
        if SERVERCHAN_SCKEY:
            send_serverchan("指数推送脚本异常", f"脚本异常：```\n{traceback.format_exc()}\n```")

if __name__ == "__main__":
    main()

