#!/usr/bin/env python3
# coding: utf-8
"""
DigVPS 更新日志抓取 + 变化推送 ServerChan（新版 SCT）
支持：格式化美观输出，容器可运行，缓存避免重复推送
"""

import os
import re
import json
import hashlib
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://digvps.com/update-log"
CACHE_FILE = "/cache/last_hash.txt"
MAX_ITEMS = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATE_LINE_RE = re.compile(r"^\s*(\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*$")


# ======================
# HTTP & HTML 解析
# ======================

def fetch_html(url, timeout=10):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DigVPS-Scraper/5.0)"
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def find_main_container(soup):
    """尝试找到主内容区域，若找不到则 fallback 到 body。"""
    for sel in ("article", "main", "div.post-content", "div#content", "div.content"):
        c = soup.select_one(sel)
        if c:
            return c
    return soup.body or soup


def extract_updates(html, max_items=MAX_ITEMS):
    """按 '日期行 → 内容段落' 模式提取最近 N 条更新。"""
    soup = BeautifulSoup(html, "html.parser")
    main = find_main_container(soup)

    # 将主内容区域的每个子节点的文本抽取为一行
    lines = []
    for child in main.children:
        text = (child.get_text(strip=True) if hasattr(child, "get_text") else str(child).strip())
        if text:
            text = " ".join(text.split())  # collapse 空格
            lines.append((child, text))

    updates = []
    i = 0
    n = len(lines)

    while i < n and len(updates) < max_items:
        node, text = lines[i]

        # 如果此行是日期
        if DATE_LINE_RE.match(text):
            date = DATE_LINE_RE.match(text).group(1)

            # 收集下面连续的内容（直到遇到下一个日期）
            content_parts = []
            j = i + 1
            while j < n:
                nxt_text = lines[j][1]
                if DATE_LINE_RE.match(nxt_text):
                    break
                content_parts.append(nxt_text)
                j += 1

            if content_parts:
                full = date + "\n" + "\n".join(content_parts)
            else:
                full = date

            updates.append(full)
            i = j
        else:
            i += 1

    return updates


# ======================
# 推送格式美化
# ======================

def format_updates(updates):
    """
    将 ["12月11日\nxxx", "12月10日\nxxx"] 格式化为更美观的 markdown。
    """
    formatted = []

    for item in updates:
        lines = item.split("\n")
        date = lines[0]
        content = " ".join(lines[1:]).strip()

        block = (
            f"### 🗓 {date}\n"
            f"{content}\n"
        )
        formatted.append(block)

    return "\n".join(formatted)


# ======================
# 缓存判断（避免重复推送）
# ======================

def calc_hash(items):
    return hashlib.sha256(json.dumps(items, ensure_ascii=False).encode()).hexdigest()


def load_last_hash():
    p = Path(CACHE_FILE)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def save_last_hash(h):
    p = Path(CACHE_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(h, encoding="utf-8")


# ======================
# ServerChan 推送（新版 SCT）
# ======================

def push_serverchan(title, desp):
    sckey = os.getenv("SERVERCHAN_SCKEY")
    if not sckey:
        logging.error("未设置 SERVERCHAN_SCKEY")
        return False

    api = f"https://sctapi.ftqq.com/{sckey}.send"

    try:
        r = requests.post(api, data={"title": title, "desp": desp}, timeout=10)
        try:
            j = r.json()
            logging.info("ServerChan 返回：%s", j)
            return j.get("code", 0) == 0 or r.status_code == 200
        except Exception:
            return r.status_code == 200
    except Exception as e:
        logging.error("推送失败：%s", e)
        return False


# ======================
# 主函数
# ======================

def main():
    try:
        html = fetch_html(URL)
    except Exception as e:
        logging.error("抓取失败：%s", e)
        return

    updates = extract_updates(html)
    if not updates:
        logging.error("未解析到任何更新内容，请检查页面结构变化")
        return

    logging.info("成功解析到 %d 条更新", len(updates))

    new_hash = calc_hash(updates)
    old_hash = load_last_hash()

    if new_hash == old_hash:
        logging.info("内容未变化，不推送")
        return

    body = format_updates(updates)
    body += f"\n\n👉 来源：{URL}"

    ok = push_serverchan("DigVPS 更新日志（有更新）", body)

    if ok:
        save_last_hash(new_hash)
        logging.info("推送成功并更新缓存")
    else:
        logging.error("推送失败")


if __name__ == "__main__":
    main()

