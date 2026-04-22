#!/usr/bin/env python3
"""
591 社區物件通知腳本
抓取指定社區的售屋/租屋資訊，比對已通知過的物件，將新物件推送至 Telegram。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

# ─── 設定 ────────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
COMMUNITY_ID = os.environ.get("COMMUNITY_ID", "102909")  # 青喆 SOHO

# 591 BFF API endpoints
SALE_API = "https://bff-market.591.com.tw/v2/web/sale/list"
RENT_API = "https://bff-market.591.com.tw/v2/web/rent/list"

# Telegram API
TG_SEND_MSG = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

# 已通知物件 ID 紀錄檔案路徑
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_ids.json")

# 請求標頭（模擬瀏覽器）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": f"https://market.591.com.tw/{COMMUNITY_ID}",
}


# ─── 工具函式 ────────────────────────────────────────────────────────
def load_seen_ids() -> dict:
    """讀取已通知過的物件 ID"""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sale": [], "rent": []}


def save_seen_ids(data: dict):
    """儲存已通知過的物件 ID"""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_sale_listings() -> list[dict]:
    """抓取售屋列表"""
    params = {
        "community_id": COMMUNITY_ID,
        "page": 1,
        "per_page": 50,
    }
    try:
        resp = requests.get(SALE_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == 1:
            return data["data"]["items"]
    except Exception as e:
        print(f"[ERROR] 抓取售屋列表失敗: {e}")
    return []


def fetch_rent_listings() -> list[dict]:
    """抓取租屋列表"""
    params = {
        "community_id": COMMUNITY_ID,
        "page": 1,
        "per_page": 50,
    }
    try:
        resp = requests.get(RENT_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == 1:
            return data["data"]["list"]
    except Exception as e:
        print(f"[ERROR] 抓取租屋列表失敗: {e}")
    return []


def format_sale_message(item: dict) -> str:
    """將售屋物件格式化為 Telegram 訊息"""
    title = item.get("title", "無標題")
    price = item.get("price_v", {}).get("price", "?")
    unit = item.get("price_v", {}).get("unit", "萬")
    price_per_ping = item.get("price_unit", "")
    room = item.get("room", "")
    area = item.get("area_v", {}).get("area", "?")
    area_unit = item.get("area_v", {}).get("unit", "坪")
    floor = item.get("floor_en", item.get("floor", ""))
    address = item.get("address", "")
    house_id = item.get("houseid", "")
    name = item.get("name", "")
    labels = ", ".join(item.get("label", []))
    browsenum = item.get("browsenum", "")
    link = f"https://sale.591.com.tw/home/house/detail/2/{house_id}.html"

    # 降價資訊
    discount_info = ""
    if item.get("is_discounted") == "1":
        original = item.get("original_price", "")
        percent = item.get("down_price_percent", "")
        if original and percent:
            discount_info = f"\n💰 降價: 原價 {original} → 降 {percent}%"

    msg = (
        f"🏠 *售屋新物件*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 {title}\n"
        f"💵 總價: *{price} {unit}* ({price_per_ping})\n"
        f"🏢 格局: {room}\n"
        f"📐 坪數: {area} {area_unit}\n"
        f"🏗 樓層: {floor}\n"
        f"📍 地址: {address}\n"
        f"👤 仲介: {name}\n"
        f"👀 瀏覽: {browsenum} 人"
        f"{discount_info}\n"
    )
    if labels:
        msg += f"🏷 標籤: {labels}\n"
    msg += f"\n🔗 [查看物件]({link})"

    return msg


def format_rent_message(item: dict) -> str:
    """將租屋物件格式化為 Telegram 訊息"""
    title = item.get("title", "無標題")
    price = item.get("price", "?")
    kind = item.get("kind_str", "")
    layout_info = " / ".join(item.get("layout_info", []))
    deposit = item.get("deposit", "")
    rent_id = item.get("rent_id", "")
    tags = ", ".join(item.get("tags", []))
    is_host = "✅ 屋主直租" if item.get("is_host") == 1 else "仲介"
    link = f"https://rent.591.com.tw/home/{rent_id}"

    msg = (
        f"🔑 *租屋新物件*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 {title}\n"
        f"💵 月租: *{price} 元/月*\n"
        f"🏢 類型: {kind}\n"
        f"📐 格局: {layout_info}\n"
        f"💰 押金: {deposit}\n"
        f"👤 {is_host}\n"
    )
    if tags:
        msg += f"🏷 標籤: {tags}\n"
    msg += f"\n🔗 [查看物件]({link})"

    return msg


def send_telegram_message(text: str):
    """透過 Telegram Bot API 發送訊息"""
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(TG_SEND_MSG, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            print(f"[WARN] Telegram 回應異常: {result}")
    except Exception as e:
        print(f"[ERROR] 發送 Telegram 訊息失敗: {e}")


def send_summary(new_sale_count: int, new_rent_count: int, total_sale: int, total_rent: int):
    """發送摘要通知"""
    tw_tz = timezone(timedelta(hours=8))
    now = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M")

    if new_sale_count == 0 and new_rent_count == 0:
        # 不發送「無新物件」的訊息，減少打擾
        print(f"[{now}] 無新物件")
        return

    msg = (
        f"📊 *591 青喆SOHO 社區物件更新*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n"
        f"🆕 新售屋: {new_sale_count} 筆\n"
        f"🆕 新租屋: {new_rent_count} 筆\n"
        f"📈 目前在售共 {total_sale} 筆 / 在租共 {total_rent} 筆\n"
        f"\n🔗 [查看社區頁面](https://market.591.com.tw/{COMMUNITY_ID})"
    )
    send_telegram_message(msg)


# ─── 主流程 ────────────────────────────────────────────────────────
def main():
    # 檢查必要環境變數
    if not TG_TOKEN:
        print("[ERROR] 未設定 TG_TOKEN 環境變數")
        sys.exit(1)
    if not TG_CHAT_ID:
        print("[ERROR] 未設定 TG_CHAT_ID 環境變數")
        sys.exit(1)

    tw_tz = timezone(timedelta(hours=8))
    now = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] 開始抓取社區 {COMMUNITY_ID} 的物件資訊...")

    # 讀取已通知過的 ID
    seen = load_seen_ids()
    seen_sale_ids = set(seen.get("sale", []))
    seen_rent_ids = set(seen.get("rent", []))

    # 抓取售屋列表
    sale_items = fetch_sale_listings()
    print(f"  售屋: 共 {len(sale_items)} 筆")

    # 抓取租屋列表
    rent_items = fetch_rent_listings()
    print(f"  租屋: 共 {len(rent_items)} 筆")

    # 找出新物件
    new_sale = [
        item for item in sale_items
        if item.get("houseid") and str(item["houseid"]) not in seen_sale_ids
    ]
    new_rent = [
        item for item in rent_items
        if item.get("rent_id") and str(item["rent_id"]) not in seen_rent_ids
    ]

    print(f"  新售屋: {len(new_sale)} 筆")
    print(f"  新租屋: {len(new_rent)} 筆")

    # 發送新售屋通知
    for item in new_sale:
        msg = format_sale_message(item)
        send_telegram_message(msg)
        time.sleep(1)  # 避免觸發 Telegram rate limit

    # 發送新租屋通知
    for item in new_rent:
        msg = format_rent_message(item)
        send_telegram_message(msg)
        time.sleep(1)

    # 發送摘要
    send_summary(len(new_sale), len(new_rent), len(sale_items), len(rent_items))

    # 更新已通知 ID（保留所有目前在線的 ID，避免無限增長）
    current_sale_ids = [str(item["houseid"]) for item in sale_items if item.get("houseid")]
    current_rent_ids = [str(item["rent_id"]) for item in rent_items if item.get("rent_id")]

    seen["sale"] = current_sale_ids
    seen["rent"] = current_rent_ids
    save_seen_ids(seen)

    print(f"[{now}] 完成！")


if __name__ == "__main__":
    main()
