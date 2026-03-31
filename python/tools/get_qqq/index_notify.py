#!/usr/bin/env python3
"""
修改说明：
- 保留免费源 Yahoo、Stooq
- 新增 Investing 免费源
- 新增 Google Finance / CNBC / Nasdaq 官方接口作为优先海外源
- 自动对比昨日涨跌（change/pct 缺失时尝试补）
- 不加入任何收费源
"""

from __future__ import annotations
import os, sys, time, json, random, logging, traceback
import re
from typing import Optional, Dict, Any
from urllib.parse import quote
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
PREFERRED_ORDER = os.getenv("PREFERRED_ORDER", "google,cnbc,nasdaq,stooq,yahoo,investing").split(",")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on", "y"}


DRY_RUN = _env_bool("DRY_RUN", False)

INDICES = {
    "nasdaq100": {
        "name": "Nasdaq-100",
        "yahoo": "^NDX",
        "stooq": "^NDX",
        "alt_symbol": "NDX",
        "google_quote": "NDX:INDEXNASDAQ",
        "google_exchange": "INDEXNASDAQ",
        "cnbc": ".NDX",
        "nasdaq": "NDX",
    },
    "sp500": {
        "name": "S&P 500",
        "yahoo": "^GSPC",
        "stooq": "^SPX",
        "alt_symbol": "SPX",
        "google_quote": ".INX:INDEXSP",
        "google_exchange": "INDEXSP",
        "cnbc": ".SPX",
        "nasdaq": None,
    },
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
if PROXY_URL:
    session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def _to_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None

        if isinstance(v, (int, float)):
            return float(v)

        s = str(v).strip()
        if not s:
            return None

        neg = False
        if s.startswith("(") and s.endswith(")"):
            neg = True
            s = s[1:-1]

        s = s.replace(",", "").replace("$", "").replace("%", "").replace("\u00a0", "").strip()
        if s in {"--", "-", "N/A", "n/a", "null", "None"}:
            return None

        n = float(s)
        return -n if neg else n
    except Exception:
        return None


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
# Yahoo Finance
# -------------------------------------------------------
def _fetch_from_yahoo_quote(symbols: list[str]) -> Dict[str, dict]:
    q = ",".join(symbols)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={q}"
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    out = {}
    for qd in data.get("quoteResponse", {}).get("result", []):
        sym = qd.get("symbol")
        if not sym:
            continue

        price = _to_float(qd.get("regularMarketPrice"))
        prev = _to_float(qd.get("regularMarketPreviousClose"))

        # quote 接口有时会缺 regularMarketPrice，回退到 previousClose
        if price is None:
            price = _to_float(qd.get("previousClose"))

        change = _to_float(qd.get("regularMarketChange"))
        pct = _to_float(qd.get("regularMarketChangePercent"))

        if change is None and price is not None and prev is not None:
            change = price - prev
        if pct is None and change is not None and prev:
            pct = change / prev * 100

        ts = qd.get("regularMarketTime")

        out[sym] = {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": datetime.fromtimestamp(ts).astimezone().isoformat() if ts else now_iso(),
            "source": "yahoo",
            "symbol": sym,
            "raw": qd,
        }
    return out


def _fetch_from_yahoo_chart(symbol: str) -> Optional[dict]:
    # 2026-03: quote 接口对部分环境返回 401/429，chart 仍可用
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?interval=1d&range=5d"
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    result = (data.get("chart", {}) or {}).get("result") or []
    if not result:
        return None

    item = result[0]
    meta = item.get("meta", {})

    closes = (((item.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    closes = [c for c in closes if c is not None]

    price = _to_float(meta.get("regularMarketPrice"))
    if price is None and closes:
        price = _to_float(closes[-1])

    prev = _to_float(meta.get("chartPreviousClose"))
    if prev is None:
        prev = _to_float(meta.get("previousClose"))
    if prev is None and len(closes) >= 2:
        prev = _to_float(closes[-2])

    change = _to_float(meta.get("regularMarketChange"))
    pct = _to_float(meta.get("regularMarketChangePercent"))

    if change is None and price is not None and prev is not None:
        change = price - prev
    if pct is None and change is not None and prev:
        pct = change / prev * 100

    ts = meta.get("regularMarketTime")
    if not ts:
        timestamps = item.get("timestamp") or []
        ts = timestamps[-1] if timestamps else None

    return {
        "price": price,
        "prev": prev,
        "change": change,
        "pct": pct,
        "time": datetime.fromtimestamp(ts).astimezone().isoformat() if ts else now_iso(),
        "source": "yahoo",
        "symbol": symbol,
        "raw": item,
    }


def fetch_from_yahoo(symbols: list[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}

    try:
        out.update(_fetch_from_yahoo_quote(symbols))
    except Exception as e:
        logging.warning("Yahoo quote failed: %s", e)

    missing = [s for s in symbols if s not in out]
    for sym in missing:
        try:
            v = _fetch_from_yahoo_chart(sym)
            if v and v.get("price") is not None:
                out[sym] = v
        except Exception as e:
            logging.warning("Yahoo chart failed for %s: %s", sym, e)

    return out


# -------------------------------------------------------
# Nasdaq 官方接口（大型交易所）
# -------------------------------------------------------
def fetch_from_nasdaq(symbol: str) -> Optional[dict]:
    url = f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=index"
    headers = {
        "Referer": "https://www.nasdaq.com/",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = session.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()

        data = (j or {}).get("data")
        if not data:
            return None

        p = data.get("primaryData") or {}
        ks = data.get("keyStats") or {}
        price = _to_float(p.get("lastSalePrice"))
        if price is None:
            return None

        change = _to_float(p.get("netChange"))
        pct = _to_float(p.get("percentageChange"))
        prev = None
        prev_info = (ks.get("previousclose") or {}).get("value")
        if prev_info is not None:
            prev = _to_float(prev_info)
        if prev is None and change is not None:
            prev = price - change

        return {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": now_iso(),
            "source": "nasdaq",
            "symbol": symbol,
            "raw": j,
        }
    except Exception:
        return None


# -------------------------------------------------------
# Google Finance 页面解析（备用免费源）
# -------------------------------------------------------
def fetch_from_google(quote_code: str, exchange: str) -> Optional[dict]:
    url = f"https://www.google.com/finance/quote/{quote_code}"
    headers = {
        "Referer": "https://www.google.com/",
        "Accept": "text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = session.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.text

        # 主报价卡里有 data-exchange / data-last-price / 时间戳
        pat = rf'data-exchange="{re.escape(exchange)}"[^>]*data-last-price="([^"]+)"[^>]*data-last-normal-market-timestamp="([^"]+)"'
        m = re.search(pat, raw, re.S)
        if not m:
            return None

        price = _to_float(m.group(1))
        ts = int(m.group(2)) if m.group(2).isdigit() else None
        if price is None:
            return None

        prev = None
        pm = re.search(r'>Previous close</div>.*?<div class="P6K39c">([^<]+)</div>', raw, re.S)
        if pm:
            prev = _to_float(pm.group(1))

        change = (price - prev) if (prev is not None) else None
        pct = (change / prev * 100) if (change is not None and prev) else None

        return {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": datetime.fromtimestamp(ts).astimezone().isoformat() if ts else now_iso(),
            "source": "google",
            "symbol": quote_code,
            "raw": raw[:1200],
        }
    except Exception:
        return None


# -------------------------------------------------------
# CNBC 页面内置 quote 数据（海外主流财经媒体）
# -------------------------------------------------------
def fetch_from_cnbc(symbol: str) -> Optional[dict]:
    url = f"https://www.cnbc.com/quotes/{symbol}"
    headers = {"Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = session.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.text

        # 页面里有 `"quote":{"data":[{"symbol":".NDX", ... }]}`
        anchor = f'"quote":{{"data":[{{"symbol":"{symbol}"'
        i = raw.find(anchor)
        if i < 0:
            return None

        seg = raw[i:i + 12000]
        p_m = re.search(r'"last":"([^"]+)"', seg)
        if not p_m:
            return None
        price = _to_float(p_m.group(1))
        if price is None:
            return None

        c_m = re.search(r'"change":"([^"]+)"', seg)
        pct_m = re.search(r'"change_pct":"([^"]+)"', seg)
        prev_m = re.search(r'"previous_day_closing":"([^"]+)"', seg)
        t_m = re.search(r'"last_time":"([^"]+)"', seg)

        change = _to_float(c_m.group(1)) if c_m else None
        pct = _to_float(pct_m.group(1)) if pct_m else None
        prev = _to_float(prev_m.group(1)) if prev_m else None
        if prev is None and change is not None:
            prev = price - change

        ts = t_m.group(1) if t_m else ""
        if ts and re.match(r"\d{4}-\d{2}-\d{2}$", ts):
            ts = f"{ts} 00:00:00"
        if not ts:
            ts = now_iso()

        return {
            "price": price,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": ts,
            "source": "cnbc",
            "symbol": symbol,
            "raw": seg[:1000],
        }
    except Exception:
        return None


# -------------------------------------------------------
# Stooq 免费 CSV
# -------------------------------------------------------
def fetch_from_stooq(symbol: str) -> Optional[dict]:
    # 旧历史接口：有时返回空内容
    daily_url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = session.get(daily_url, timeout=TIMEOUT)
        lines = r.text.strip().splitlines()
        if len(lines) >= 3:
            # last and second last rows
            last = lines[-1].split(",")
            prev = lines[-2].split(",")

            close = _to_float(last[4])
            prev_close = _to_float(prev[4])
            if close is None:
                return None

            change = (close - prev_close) if (prev_close is not None) else None
            pct = (change / prev_close * 100) if (change is not None and prev_close) else None

            return {
                "price": close,
                "prev": prev_close,
                "change": change,
                "pct": pct,
                "time": last[0] if last else now_iso(),
                "source": "stooq",
                "symbol": symbol,
                "raw": last,
            }
    except Exception:
        pass

    # 新实时接口：返回一行 CSV，例如
    # ^NDX,20260327,230000,23463.75,23472.89,23088.99,23132.77,1136667401,
    quote_url = f"https://stooq.com/q/l/?s={symbol}"
    try:
        r = session.get(quote_url, timeout=TIMEOUT)
        line = r.text.strip()
        if not line:
            return None

        cols = line.split(",")
        if len(cols) < 7:
            return None

        price = _to_float(cols[6])
        if price is None:
            return None

        ts_date = cols[1] if len(cols) > 1 else ""
        ts_time = cols[2] if len(cols) > 2 else ""
        ts = f"{ts_date} {ts_time}".strip() or now_iso()

        return {
            "price": price,
            "prev": None,
            "change": None,
            "pct": None,
            "time": ts,
            "source": "stooq",
            "symbol": symbol,
            "raw": line,
        }
    except Exception:
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
            "raw": j,
        }
    except Exception:
        return None


# -------------------------------------------------------
# 调度器：按 PREFERRED_ORDER 依次尝试
# -------------------------------------------------------
def get_index_values() -> dict:
    results = {}

    for src in PREFERRED_ORDER:
        src = src.strip().lower()

        if src == "yahoo":
            missing_keys = [k for k in INDICES if k not in results]
            symbols = [INDICES[k]["yahoo"] for k in missing_keys if INDICES[k].get("yahoo")]
            if not symbols:
                continue

            try:
                out = fetch_from_yahoo(symbols)
            except Exception as e:
                logging.warning("batch fetch from yahoo failed: %s", e)
                out = {}

            for k in missing_keys:
                sym = INDICES[k].get("yahoo")
                if sym and sym in out and out[sym].get("price") is not None:
                    results[k] = out[sym]
            continue

        for k, meta in INDICES.items():
            if k in results:
                continue

            if src == "nasdaq" and meta.get("nasdaq"):
                r = fetch_from_nasdaq(meta["nasdaq"])
                if r:
                    results[k] = r

            elif src == "google" and meta.get("google_quote") and meta.get("google_exchange"):
                r = fetch_from_google(meta["google_quote"], meta["google_exchange"])
                if r:
                    results[k] = r

            elif src == "cnbc" and meta.get("cnbc"):
                r = fetch_from_cnbc(meta["cnbc"])
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

        if change is not None and pct is not None:
            line += f"　`{change:+.2f}`　`({pct:+.2f}%)`"

        line += f"　来源：`{src}`"

        md.append(line)

    md.append("\n----\n`Generated at " + now_iso() + "`")

    return title, "\n\n".join(md)


# -------------------------------------------------------
# main
# -------------------------------------------------------
def run_once(force_dry_run: Optional[bool] = None) -> bool:
    dry_run = DRY_RUN if force_dry_run is None else force_dry_run

    results = get_index_values()
    title, content = build_message(results)

    # 测试模式：只输出，不推送，避免消耗 Server酱额度
    if dry_run:
        logging.info("DRY_RUN enabled, skip ServerChan push. title=%s", title)
        print(content)
        return True

    return send_serverchan(title, content)


def main():
    try:
        arg_dry = any(a in {"--dry-run", "--no-push"} for a in sys.argv[1:])
        run_once(force_dry_run=arg_dry if arg_dry else None)
    except Exception:
        err = traceback.format_exc()
        logging.error(err)
        # dry-run 模式异常也不推送
        arg_dry = any(a in {"--dry-run", "--no-push"} for a in sys.argv[1:])
        dry_run = DRY_RUN or arg_dry
        if SERVERCHAN_SCKEY and not dry_run:
            send_serverchan("指数脚本异常", f"```\n{err}\n```")


if __name__ == "__main__":
    main()
