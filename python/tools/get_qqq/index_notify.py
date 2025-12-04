#!/usr/bin/env python3
"""
修改说明：
- 保留免费源 Yahoo、Stooq
- 新增 Sina、Investing 两个免费源
- 自动对比昨日涨跌（change/pct 缺失时尝试补）
- 不加入任何收费源
"""

from __future__ import annotations
import os, sys, time, json, random, logging, traceback
from typing import Optional, Dict, Any
import requests
from datetime import datetime, timezone

# -------------------------------------------------------
# config
# -------------------------------------------------------
SERVERCHAN_SCKEY = os.getenv("SERVERCHAN_SCKEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
TIMEOUT = float(os.getenv("TIMEOUT", "6"))
USER_AGENT = os.getenv("USER_AGENT",
                       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
PREFERRED_ORDER = os.getenv("PREFERRED_ORDER", "yahoo,sina,stooq,investing").split(",")

INDICES = {
    "nasdaq100": {"name": "Nasdaq-100", "yahoo": "^NDX", "stooq": "^NDX", "sina": "int_nasdaq", "alt_symbol": "NDX"},
    "sp500": {"name": "S&P 500", "yahoo": "^GSPC", "stooq": "^SPX", "sina": "int_sp500", "alt_symbol": "SPX"},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
if PROXY_URL:
    session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})

def now_iso(): return datetime.now(timezone.utc).astimezone().isoformat()


# -------------------------------------------------------
# ServerChan
# -------------------------------------------------------
def send_serverchan(title: str, content_md: str) -> bool:
    if not SERVERCHAN_SCKEY:
        logging.error("SERVERCHAN_SCKEY not set, skip push.")
        return False
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SCKEY}.send"
    data = {"title": title, "desp": content_md}
    try:
        r = session.post(url, data=data, timeout=TIMEOUT)
        logging.info("ServerChan resp: %s %s", r.status_code, r.text[:200])
        return r.ok
    except Exception:
        logging.exception("ServerChan error")
        return False


# -------------------------------------------------------
# Yahoo Finance (JSON quote)
# -------------------------------------------------------
def fetch_from_yahoo(symbols: list[str]) -> Dict[str, dict]:
    q = ",".join(symbols)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={q}"
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    out = {}
    for qd in data.get("quoteResponse", {}).get("result", []):
        sym = qd.get("symbol")
        price = qd.get("regularMarketPrice") or qd.get("previousClose")
        prev = qd.get("regularMarketPreviousClose")
        change = price - prev if (price and prev) else None
        pct = (change / prev * 100) if (change is not None and prev) else None
        ts = qd.get("regularMarketTime")

        out[sym] = {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": datetime.fromtimestamp(ts).astimezone().isoformat() if ts else now_iso(),
            "source": "yahoo",
            "symbol": sym,
            "raw": qd
        }
    return out


# -------------------------------------------------------
# Sina 免费行情（仅美股指数）
# -------------------------------------------------------
def fetch_from_sina(symbol: str) -> Optional[dict]:
    url = f"https://hq.sinajs.cn/list={symbol}"
    try:
        r = session.get(url, timeout=TIMEOUT)
        raw = r.text
        arr = raw.split(",")
        price = float(arr[1])
        prev = float(arr[2])

        change = price - prev
        pct = change / prev * 100 if prev else None

        return {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": now_iso(),
            "source": "sina",
            "symbol": symbol,
            "raw": raw
        }
    except:
        return None


# -------------------------------------------------------
# Stooq 免费 CSV
# -------------------------------------------------------
def fetch_from_stooq(symbol: str) -> Optional[dict]:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = session.get(url, timeout=TIMEOUT)
        lines = r.text.strip().splitlines()
        if len(lines) < 3:
            return None

        # last and second last rows
        last = lines[-1].split(",")
        prev = lines[-2].split(",")

        close = float(last[4])
        prev_close = float(prev[4])

        change = close - prev_close
        pct = change / prev_close * 100

        return {
            "price": close,
            "prev": prev_close,
            "change": change,
            "pct": pct,
            "time": last[0],
            "source": "stooq",
            "symbol": symbol,
            "raw": last
        }
    except:
        return None


# -------------------------------------------------------
# Investing.com 免费 JSON API（无需登录）
# -------------------------------------------------------
def fetch_from_investing(symbol: str) -> Optional[dict]:
    """
    非官方免费源，返回：price, prev, change, pct
    """
    url = f"https://tvc4.forexpros.com/{random.randint(1000000000,1999999999)}/1/1/8/history?symbol={symbol}&resolution=1"
    try:
        r = session.get(url, timeout=TIMEOUT)
        j = r.json()
        if "c" not in j:
            return None

        close = j["c"][-1]
        prev_close = j["c"][-2]

        change = close - prev_close
        pct = change / prev_close * 100

        return {
            "price": close,
            "prev": prev_close,
            "change": change,
            "pct": pct,
            "time": now_iso(),
            "source": "investing",
            "symbol": symbol,
            "raw": j
        }
    except:
        return None


# -------------------------------------------------------
# 调度器：按 PREFERRED_ORDER 依次尝试
# -------------------------------------------------------
def get_index_values() -> dict:
    results = {}

    for src in PREFERRED_ORDER:
        src = src.strip().lower()

        for k, meta in INDICES.items():
            if k in results:
                continue

            if src == "yahoo" and meta.get("yahoo"):
                try:
                    out = fetch_from_yahoo([meta["yahoo"]])
                    if meta["yahoo"] in out:
                        results[k] = out[meta["yahoo"]]
                except:
                    pass

            elif src == "sina" and meta.get("sina"):
                r = fetch_from_sina(meta["sina"])
                if r:
                    results[k] = r

            elif src == "stooq" and meta.get("stooq"):
                r = fetch_from_stooq(meta["stooq"])
                if r:
                    results[k] = r

            elif src == "investing":
                r = fetch_from_investing(meta["alt_symbol"])
                if r:
                    results[k] = r

    return results


# -------------------------------------------------------
# 生成推送内容
# -------------------------------------------------------
def build_message(results: dict) -> (str, str):
    title = f"指数快讯 — {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
    md = [f"**{title}**\n"]

    for k, meta in INDICES.items():
        name = meta["name"]
        r = results.get(k)

        if not r:
            md.append(f"- **{name}**：❌ 获取失败")
            continue

        price = r["price"]
        change = r.get("change")
        pct = r.get("pct")
        src = r["source"]

        line = f"- **{name}**: `{price:.2f}`"

        if change is not None:
            line += f"　`{change:+.2f}`　`({pct:+.2f}%)`"

        line += f"　来源：`{src}`"

        md.append(line)

    md.append("\n----\n`Generated at " + now_iso() + "`")

    return title, "\n\n".join(md)


# -------------------------------------------------------
# main
# -------------------------------------------------------
def main():
    try:
        results = get_index_values()
        title, content = build_message(results)
        send_serverchan(title, content)
    except Exception:
        err = traceback.format_exc()
        logging.error(err)
        if SERVERCHAN_SCKEY:
            send_serverchan("指数脚本异常", f"```\n{err}\n```")


if __name__ == "__main__":
    main()

