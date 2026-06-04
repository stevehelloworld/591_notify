#!/usr/bin/env python3
"""
591 社區物件與內政部實價登錄通知腳本
抓取指定社區的售屋/租屋資訊，並整合內政部實價登錄的成交與租賃案件，比對已通知過的物件，將新物件推送至 Telegram。
"""

import json
import os
import sys
import time
import hashlib
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
import requests
import asyncio
import urllib3
from playwright.async_api import async_playwright

# Suppress insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── 設定 ────────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
COMMUNITY_ID = os.environ.get("COMMUNITY_ID", "102909")  # 青喆 SOHO

# 591 BFF API endpoints
SALE_API = "https://bff-market.591.com.tw/v2/web/sale/list"
RENT_API = "https://bff-market.591.com.tw/v2/web/rent/list"

# Telegram API
TG_API_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"
TG_SEND_MSG = f"{TG_API_BASE}/sendMessage"
TG_SEND_PHOTO = f"{TG_API_BASE}/sendPhoto"
TG_SEND_MEDIA_GROUP = f"{TG_API_BASE}/sendMediaGroup"

# 檔案路徑
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_ids.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# Telegram caption 上限
CAPTION_LIMIT = 1024
# 相簿最多 10 張照片
MAX_PHOTOS = 10

# 請求標頭（模擬瀏覽器）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://market.591.com.tw/{COMMUNITY_ID}",
}

# ─── 資料庫與記錄讀寫 ──────────────────────────────────────────────────
def load_seen_ids() -> dict:
    """讀取已通知過的物件 ID"""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "sale" not in data: data["sale"] = []
                if "rent" not in data: data["rent"] = []
                if "lvr_sales" not in data: data["lvr_sales"] = []
                if "lvr_rentals" not in data: data["lvr_rentals"] = []
                return data
        except Exception as e:
            print(f"[ERROR] 讀取 seen_ids.json 失敗: {e}")
    return {"sale": [], "rent": [], "lvr_sales": [], "lvr_rentals": []}

def save_seen_ids(data: dict):
    """儲存已通知過的物件 ID"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 儲存 seen_ids.json 失敗: {e}")

def load_history() -> list[dict]:
    """讀取歷史詳細紀錄"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] 讀取 history.json 失敗: {e}")
    return []

def save_history(history: list[dict]):
    """儲存歷史詳細紀錄"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 儲存 history.json 失敗: {e}")

# ─── 591 BFF API 抓取 ──────────────────────────────────────────────────
def fetch_sale_listings() -> list[dict]:
    """抓取 591 售屋列表"""
    params = {"community_id": COMMUNITY_ID, "page": 1, "per_page": 50}
    try:
        resp = requests.get(SALE_API, params=params, headers=HEADERS, verify=False, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == 1:
            return data["data"]["items"]
    except Exception as e:
        print(f"[ERROR] 抓取 591 售屋列表失敗: {e}")
    return []

def fetch_rent_listings() -> list[dict]:
    """抓取 591 租屋列表"""
    params = {"community_id": COMMUNITY_ID, "page": 1, "per_page": 50}
    try:
        resp = requests.get(RENT_API, params=params, headers=HEADERS, verify=False, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == 1:
            return data["data"]["list"]
    except Exception as e:
        print(f"[ERROR] 抓取 591 租屋列表失敗: {e}")
    return []

# ─── LVR 實價登錄 Playwright 抓取 ───────────────────────────────────────
def _fetch_json(url: str) -> list[dict]:
    """獲取行政區劃等 API 資料（繞過 SSL 驗證）"""
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[WARN] 取得 {url} 失敗: {e}")
        return []

def _lookup_city(city_name: str) -> str:
    cities = _fetch_json("https://lvr.land.moi.gov.tw/SERVICE/CITY")
    normalized = city_name.replace("台", "臺")
    for c in cities:
        if c.get("title") == normalized or c.get("title") == city_name:
            return c.get("code", "")
    return "F"

def _lookup_town(city_code: str, town_name: str) -> str:
    towns = _fetch_json(f"https://lvr.land.moi.gov.tw/SERVICE/CITY/{city_code}/")
    for t in towns:
        if t.get("title") == town_name:
            return t.get("code", "")
    return "F18"

LVR_QUERY_TYPES = {
    "biz": "pills-sale-tab",
    "rent": "pills-rent-tab",
}

async def query_lvr_real_price(
    city: str = "新北市",
    town: str = "中和區",
    building: str = "青喆SOHO",
    start_year: int = 110,
    end_year: int = 115,
    query_type: str = "biz"
) -> list[dict]:
    """使用 Playwright 查詢內政部實價登錄"""
    city_code = await asyncio.to_thread(_lookup_city, city)
    town_code = await asyncio.to_thread(_lookup_town, city_code, town) if town else ""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        
        # 繞過 webdriver 偵測
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        try:
            await page.goto("https://lvr.land.moi.gov.tw/", wait_until="networkidle")
            await page.wait_for_timeout(2000)
            
            frame = next(f for f in page.frames if "index.jsp" in f.url)
            
            tab_id = LVR_QUERY_TYPES.get(query_type, "pills-sale-tab")
            await frame.evaluate(f"() => {{ document.querySelector('#{tab_id}').click(); }}")
            await page.wait_for_timeout(500)
            
            await frame.evaluate(f"""() => {{
                var el = document.querySelector("#p_city");
                el.value = "{city_code}";
                el.dispatchEvent(new Event("change", {{bubbles: true}}));
            }}""")
            
            if town_code:
                await frame.wait_for_function('document.querySelector("#p_town").options.length > 1')
                await frame.evaluate(f"""() => {{
                    var el = document.querySelector("#p_town");
                    el.value = "{town_code}";
                    el.dispatchEvent(new Event("change", {{bubbles: true}}));
                }}""")
                
            await frame.evaluate(f"""() => {{
                document.querySelector("#p_startY").value = "{start_year}";
                document.querySelector("#p_startM").value = "1";
                document.querySelector("#p_endY").value = "{end_year}";
                document.querySelector("#p_endM").value = "12";
            }}""")
            
            if building:
                await frame.fill("#p_build", building)
                
            async with page.expect_response(
                lambda r: "SERVICE/QueryPrice" in r.url and r.status == 200,
                timeout=30000
            ) as response_info:
                await frame.evaluate("""() => {
                    var btn = document.querySelector(".form-button[go_type='list']");
                    if (btn) btn.click();
                }""")
                
            response = await response_info.value
            data = await response.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[ERROR] Playwright 查詢 LVR 失敗 ({query_type}): {e}")
            return []
        finally:
            await context.close()
            try:
                await browser.close()
            except Exception:
                pass

# ─── LVR 欄位轉換對齊 ──────────────────────────────────────────────────
_LVR_MAPPING = {
    "a": "address",
    "b": "building_type",
    "bn": "building_name",
    "bs": "main_building_ratio",
    "e": "transaction_date",
    "f": "floor",
    "j": "bedrooms",
    "k": "living_rooms",
    "l": "bathrooms",
    "v": "layout",
    "lat": "latitude",
    "lon": "longitude",
    "m": "has_management",
    "el": "has_elevator",
    "p": "unit_price",
    "tp": "total_price",
    "pu": "usage",
    "s": "area",
    "t": "transaction_object",
    "note": "note",
    "rperiod": "rental_period",
    "rtype": "rental_type",
    "rserviec": "rental_service",
    "fn": "furniture",
}

def normalize_lvr(record: dict) -> dict:
    return {friendly: record.get(raw, "") for raw, friendly in _LVR_MAPPING.items()}

# ─── 雜湊與去重邏輯 ───────────────────────────────────────────────────
def get_591_fingerprint(item: dict) -> str:
    """計算 591 物件的圖片+樓層指紋"""
    pic_urls = sorted(item.get("pictures", []))
    floor_str = str(item.get("floor_en", item.get("floor", ""))).strip()
    raw_str = f"{pic_urls}-{floor_str}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

def is_duplicate_591_listing(item: dict, history: list[dict], item_type: str) -> bool:
    """檢查是否為重複圖片與樓層的刊登物件"""
    pic_urls = sorted(item.get("pictures", []))
    floor_str = str(item.get("floor_en", item.get("floor", ""))).strip()
    if not pic_urls:
        return False
    for hist_item in history:
        if hist_item.get("source") == "591" and hist_item.get("type") == item_type:
            hist_pics = sorted(hist_item.get("photos", []))
            hist_floor = str(hist_item.get("floor", "")).strip()
            if pic_urls == hist_pics and floor_str == hist_floor:
                return True
    return False

def get_lvr_transaction_hash(item: dict, item_type: str) -> str:
    """計算實價登錄案件唯一雜湊值"""
    address = item.get("address", "").strip()
    trans_date = item.get("transaction_date", "").strip()
    floor = item.get("floor", "").strip()
    price = item.get("total_price", "").strip()
    area = item.get("area", "").strip()
    layout = item.get("layout", "").strip()
    raw_str = f"{item_type}-{address}-{trans_date}-{floor}-{price}-{area}-{layout}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

# ─── 資料格式統一化轉換 (Unified Schema) ──────────────────────────────────
def convert_591_sale_to_unified(item: dict, tw_now_str: str) -> dict:
    house_id = str(item.get("houseid", ""))
    price_val = 0
    try:
        price_val = int(str(item.get("price_v", {}).get("price", "0")).replace(",", "")) * 10000
    except ValueError:
        pass
    
    notes_list = []
    if item.get("is_discounted") == "1":
        notes_list.append(f"降價! 原價 {item.get('original_price', '')} → 降 {item.get('down_price_percent', '')}%")
    name = item.get("name", "")
    if name:
        notes_list.append(f"聯絡人: {name}")
    labels = item.get("label", [])
    if labels:
        notes_list.extend(labels)
        
    return {
        "id": f"591_sale_{house_id}",
        "source": "591",
        "type": "sale",
        "title": item.get("title", "無標題"),
        "price": f"{item.get('price_v', {}).get('price', '?')} {item.get('price_v', {}).get('unit', '萬')}",
        "price_value": price_val,
        "unit_price": item.get("price_unit", ""),
        "layout": item.get("room", ""),
        "area": item.get("area_v", {}).get("area", "?"),
        "floor": item.get("floor_en", item.get("floor", "")),
        "address": item.get("address", ""),
        "link": f"https://sale.591.com.tw/home/house/detail/2/{house_id}.html",
        "photos": item.get("pictures", []),
        "notes": " | ".join(notes_list),
        "detected_at": tw_now_str
    }

def convert_591_rent_to_unified(item: dict, tw_now_str: str) -> dict:
    rent_id = str(item.get("rent_id", ""))
    price_val = 0
    try:
        price_val = int(str(item.get("price", "0")).replace(",", ""))
    except ValueError:
        pass
        
    notes_list = []
    is_host = "屋主直租" if item.get("is_host") == 1 else "仲介"
    notes_list.append(is_host)
    deposit = item.get("deposit", "")
    if deposit:
        notes_list.append(deposit)
    tags = item.get("tags", [])
    if tags:
        notes_list.extend(tags)

    return {
        "id": f"591_rent_{rent_id}",
        "source": "591",
        "type": "rent",
        "title": item.get("title", "無標題"),
        "price": f"{item.get('price', '?')} 元/月",
        "price_value": price_val,
        "unit_price": "",
        "layout": f"{item.get('kind_str', '')} | {' / '.join(item.get('layout_info', []))}",
        "area": "",
        "floor": "",
        "address": "中和區-青喆SOHO",
        "link": f"https://rent.591.com.tw/home/{rent_id}",
        "photos": item.get("pictures", []),
        "notes": " | ".join(notes_list),
        "detected_at": tw_now_str
    }

def convert_lvr_sale_to_unified(item: dict, hash_id: str, tw_now_str: str) -> dict:
    price_val = 0
    try:
        price_val = int(str(item.get("total_price", "0")).replace(",", ""))
    except ValueError:
        pass
        
    notes_list = []
    notes_list.append(f"交易標的: {item.get('transaction_object', '')}")
    if item.get("main_building_ratio"):
        notes_list.append(f"主建物比: {item.get('main_building_ratio')}")
    if item.get("has_management"):
        notes_list.append(f"管理組織: {item.get('has_management')}")
    if item.get("has_elevator"):
        notes_list.append(f"電梯: {item.get('has_elevator')}")
    if item.get("note"):
        notes_list.append(f"備註: {item.get('note')}")
        
    return {
        "id": f"lvr_sale_{hash_id}",
        "source": "LVR",
        "type": "sale",
        "title": f"實價登錄成交 ({item.get('building_name', '青喆SOHO')})",
        "price": f"{item.get('total_price', '')} 元",
        "price_value": price_val,
        "unit_price": f"{item.get('unit_price', '')} 元/坪",
        "layout": item.get("layout", ""),
        "area": item.get("area", ""),
        "floor": item.get("floor", ""),
        "address": item.get("address", ""),
        "link": "",
        "photos": [],
        "notes": " | ".join(notes_list),
        "detected_at": tw_now_str
    }

def convert_lvr_rent_to_unified(item: dict, hash_id: str, tw_now_str: str) -> dict:
    price_val = 0
    try:
        price_val = int(str(item.get("total_price", "0")).replace(",", ""))
    except ValueError:
        pass
        
    notes_list = []
    if item.get("rental_type"):
        notes_list.append(f"租賃類型: {item.get('rental_type')}")
    if item.get("rental_period"):
        notes_list.append(f"租期: {item.get('rental_period')}")
    if item.get("rental_service"):
        notes_list.append(f"服務: {item.get('rental_service')}")
    if item.get("furniture"):
        notes_list.append(f"傢俱: {item.get('furniture')}")
    if item.get("note"):
        notes_list.append(f"備註: {item.get('note')}")
        
    return {
        "id": f"lvr_rent_{hash_id}",
        "source": "LVR",
        "type": "rent",
        "title": f"實價登錄租賃 ({item.get('building_name', '青喆SOHO')})",
        "price": f"{item.get('total_price', '')} 元/月",
        "price_value": price_val,
        "unit_price": f"{item.get('unit_price', '')} 元/坪",
        "layout": item.get("layout", ""),
        "area": item.get("area", ""),
        "floor": item.get("floor", ""),
        "address": item.get("address", ""),
        "link": "",
        "photos": [],
        "notes": " | ".join(notes_list),
        "detected_at": tw_now_str
    }

# ─── Telegram 發送函式 ─────────────────────────────────────────────
def send_telegram_message(text: str, parse_mode: str = "HTML"):
    """透過 Telegram Bot API 發送文字訊息"""
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(TG_SEND_MSG, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            print(f"[WARN] Telegram 回應異常: {result}")
    except Exception as e:
        print(f"[ERROR] 發送 Telegram 訊息失敗: {e}")

def send_telegram_photo_album(photos: list[str], caption: str = ""):
    """發送照片相簿（最多 10 張）"""
    if not photos:
        return
    photos = photos[:MAX_PHOTOS]

    if len(photos) == 1:
        payload = {
            "chat_id": TG_CHAT_ID,
            "photo": photos[0],
            "caption": caption[:CAPTION_LIMIT] if caption else "",
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(TG_SEND_PHOTO, json=payload, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"[ERROR] 發送照片失敗: {e}")
        return

    media = []
    for i, url in enumerate(photos):
        item = {"type": "photo", "media": url}
        if i == 0 and caption:
            item["caption"] = caption[:CAPTION_LIMIT]
            item["parse_mode"] = "HTML"
        media.append(item)

    payload = {"chat_id": TG_CHAT_ID, "media": media}
    try:
        resp = requests.post(TG_SEND_MEDIA_GROUP, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            print(f"[WARN] sendMediaGroup 回應異常: {result}")
    except Exception as e:
        print(f"[ERROR] 發送照片相簿失敗: {e}")

# ─── 591 通知發送 ─────────────────────────────────────────────────
def send_sale_notification(item: dict):
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
    photos = item.get("pictures", [])
    link = f"https://sale.591.com.tw/home/house/detail/2/{house_id}.html"

    discount_info = ""
    if item.get("is_discounted") == "1":
        original = item.get("original_price", "")
        percent = item.get("down_price_percent", "")
        if original and percent:
            discount_info = f"\n💰 <b>降價!</b> 原價 {original} → 降 {percent}%"

    caption = (
        f"🏠 <b>售屋新物件</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 {title}\n"
        f"💵 <b>{price} {unit}</b>（{price_per_ping}）\n"
        f"🏢 {room} ｜ 📐 {area} {area_unit} ｜ 🏗 {floor}\n"
        f"📍 {address}\n"
        f"👤 {name} ｜ 👀 {browsenum} 人瀏覽"
        f"{discount_info}"
    )
    if labels:
        caption += f"\n🏷 {labels}"
    caption += f'\n\n🔗 <a href="{link}">查看物件詳情</a>'

    if photos:
        send_telegram_photo_album(photos, caption)
    else:
        send_telegram_message(caption)

def send_rent_notification(item: dict):
    title = item.get("title", "無標題")
    price = item.get("price", "?")
    kind = item.get("kind_str", "")
    layout_info = " / ".join(item.get("layout_info", []))
    deposit = item.get("deposit", "")
    rent_id = item.get("rent_id", "")
    tags = item.get("tags", [])
    photos = item.get("pictures", [])
    is_host = "✅ 屋主直租" if item.get("is_host") == 1 else "🏢 仲介"
    link = f"https://rent.591.com.tw/home/{rent_id}"

    tags_str = ""
    if tags:
        tags_str = "\n🏷 " + " · ".join(tags)

    caption = (
        f"🔑 <b>租屋新物件</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 {title}\n"
        f"💵 <b>{price} 元/月</b>\n"
        f"🏢 {kind} ｜ 📐 {layout_info}\n"
        f"💰 {deposit}\n"
        f"👤 {is_host}"
        f"{tags_str}"
        f'\n\n🔗 <a href="{link}">查看物件詳情</a>'
    )

    if photos:
        send_telegram_photo_album(photos, caption)
    else:
        send_telegram_message(caption)

# ─── LVR 通知發送 ─────────────────────────────────────────────────
def send_lvr_sale_notification(item: dict):
    title = item.get("title", "實價登錄成交")
    address = item.get("address", "")
    price = item.get("price", "")
    unit_price = item.get("unit_price", "")
    layout = item.get("layout", "")
    area = item.get("area", "")
    floor = item.get("floor", "")
    notes = item.get("notes", "")
    
    msg = (
        f"🏛 <b>實價登錄新成交 (買賣)</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 <b>{title}</b>\n"
        f"📍 地址: {address}\n"
        f"💵 總價: <b>{price}</b> ({unit_price})\n"
        f"📐 登記坪數: {area} 坪\n"
        f"🏢 格局: {layout}\n"
        f"🏗 樓層: {floor}\n"
        f"📝 詳情: {notes.replace(' | ', '\n📝 ')}\n"
        f'\n🔗 <a href="https://lvr.land.moi.gov.tw/">內政部實價登錄網</a>'
    )
    send_telegram_message(msg)

def send_lvr_rent_notification(item: dict):
    title = item.get("title", "實價登錄租賃")
    address = item.get("address", "")
    price = item.get("price", "")
    unit_price = item.get("unit_price", "")
    layout = item.get("layout", "")
    area = item.get("area", "")
    floor = item.get("floor", "")
    notes = item.get("notes", "")
    
    msg = (
        f"🏛 <b>實價登錄新成交 (租賃)</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 <b>{title}</b>\n"
        f"📍 地址: {address}\n"
        f"💵 租金: <b>{price}</b> ({unit_price})\n"
        f"📐 租賃坪數: {area} 坪\n"
        f"🏢 格局: {layout}\n"
        f"🏗 樓層: {floor}\n"
        f"📝 詳情: {notes.replace(' | ', '\n📝 ')}\n"
        f'\n🔗 <a href="https://lvr.land.moi.gov.tw/">內政部實價登錄網</a>'
    )
    send_telegram_message(msg)

def send_summary(new_sale_count: int, new_rent_count: int, total_sale: int, total_rent: int,
                 new_lvr_sale: int, new_lvr_rent: int, tw_now_str: str):
    """發送綜合摘要通知"""
    if new_sale_count == 0 and new_rent_count == 0 and new_lvr_sale == 0 and new_lvr_rent == 0:
        print(f"[{tw_now_str}] 無新物件或實價登錄成交")
        return

    # 自動建立 GitHub Pages 連結
    github_repo = os.environ.get("GITHUB_REPOSITORY", "")
    pages_link_str = ""
    if github_repo and "/" in github_repo:
        owner, repo_name = github_repo.split("/", 1)
        pages_link = f"https://{owner.lower()}.github.io/{repo_name}/"
        pages_link_str = f'\n🌐 <a href="{pages_link}"><b>線上歷史查詢儀表板</b></a>'

    msg = (
        f"📊 <b>青喆SOHO 社區物件更新統計</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ 時間: {tw_now_str}\n"
        f"🆕 新增售屋 (591): {new_sale_count} 筆\n"
        f"🆕 新增租屋 (591): {new_rent_count} 筆\n"
        f"🏛 新實價登錄 (買賣): {new_lvr_sale} 筆\n"
        f"🏛 新實價登錄 (租賃): {new_lvr_rent} 筆\n"
        f"📈 591目前在售共 {total_sale} 筆 / 在租共 {total_rent} 筆\n"
        f'\n🔗 <a href="https://market.591.com.tw/{COMMUNITY_ID}">查看 591 社區頁面</a>'
        f'{pages_link_str}'
    )
    send_telegram_message(msg)

# ─── 主流程 ────────────────────────────────────────────────────────
async def main():
    # 檢查必要環境變數
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[ERROR] 未設定 TG_TOKEN 或 TG_CHAT_ID 環境變數")
        sys.exit(1)

    tw_tz = timezone(timedelta(hours=8))
    tw_now = datetime.now(tw_tz)
    tw_now_str = tw_now.strftime("%Y-%m-%d %H:%M")
    print(f"[{tw_now_str}] 開始抓取社區 {COMMUNITY_ID} 的物件與實價登錄資訊...")

    # 讀取持久化檔案
    seen = load_seen_ids()
    history = load_history()
    
    seen_sale_ids = set(seen.get("sale", []))
    seen_rent_ids = set(seen.get("rent", []))
    seen_lvr_sale_hashes = set(seen.get("lvr_sales", []))
    seen_lvr_rent_hashes = set(seen.get("lvr_rentals", []))

    # 1. 抓取 591 當前資料
    sale_items = fetch_sale_listings()
    rent_items = fetch_rent_listings()
    print(f"  591 售屋: 共 {len(sale_items)} 筆")
    print(f"  591 租屋: 共 {len(rent_items)} 筆")

    # 2. 抓取實價登錄資料 (110 - 115 年)
    print("  正在從內政部實價登錄抓取成交資訊 (買賣)...")
    lvr_sale_raw = await query_lvr_real_price(query_type="biz")
    print(f"  LVR 實價登錄成交: 共 {len(lvr_sale_raw)} 筆")

    print("  正在從內政部實價登錄抓取租賃資訊 (租賃)...")
    lvr_rent_raw = await query_lvr_real_price(query_type="rent")
    print(f"  LVR 實價登錄租賃: 共 {len(lvr_rent_raw)} 筆")

    # 3. 判斷是否需要進行歷史回填 (當 history.json 為空時)
    is_backfilling = (len(history) == 0)
    if is_backfilling:
        print("💡 [BACKFILL] 偵測到 history.json 為空，開始自動回填歷史資料...")
        
        # 回填實價登錄 (買賣)
        for item in lvr_sale_raw:
            norm = normalize_lvr(item)
            hash_val = get_lvr_transaction_hash(norm, "sale")
            unified = convert_lvr_sale_to_unified(norm, hash_val, tw_now_str)
            unified["detected_at"] = "歷史實價登錄紀錄"
            history.append(unified)
            seen_lvr_sale_hashes.add(hash_val)

        # 回填實價登錄 (租賃)
        for item in lvr_rent_raw:
            norm = normalize_lvr(item)
            hash_val = get_lvr_transaction_hash(norm, "rent")
            unified = convert_lvr_rent_to_unified(norm, hash_val, tw_now_str)
            unified["detected_at"] = "歷史實價登錄紀錄"
            history.append(unified)
            seen_lvr_rent_hashes.add(hash_val)

        # 回填當前 591 售屋
        for item in sale_items:
            house_id = str(item["houseid"]) if item.get("houseid") else ""
            if house_id:
                unified = convert_591_sale_to_unified(item, tw_now_str)
                unified["detected_at"] = "歷史 591 紀錄"
                history.append(unified)
                seen_sale_ids.add(house_id)

        # 回填當前 591 租屋
        for item in rent_items:
            rent_id = str(item["rent_id"]) if item.get("rent_id") else ""
            if rent_id:
                unified = convert_591_rent_to_unified(item, tw_now_str)
                unified["detected_at"] = "歷史 591 紀錄"
                history.append(unified)
                seen_rent_ids.add(rent_id)

        # 儲存回填資料，不發送通知
        seen["sale"] = list(seen_sale_ids)
        seen["rent"] = list(seen_rent_ids)
        seen["lvr_sales"] = list(seen_lvr_sale_hashes)
        seen["lvr_rentals"] = list(seen_lvr_rent_hashes)
        save_seen_ids(seen)
        save_history(history)
        print("💡 [BACKFILL] 歷史回填完成！已回填實價登錄與當前在線 591 物件。")
        return

    # 4. 常規通知流程 (非回填時)
    new_sale_count = 0
    new_rent_count = 0
    new_lvr_sale_count = 0
    new_lvr_rent_count = 0

    # ─── 處理 591 新售屋 ───
    for item in sale_items:
        house_id = str(item.get("houseid", ""))
        if not house_id: continue
        # 必須是未通知過的 ID
        if house_id not in seen_sale_ids:
            # 必須不是圖片與樓層重複的物件 (防重複洗板)
            if not is_duplicate_591_listing(item, history, "sale"):
                print(f"發現新 591 售屋物件: {house_id}")
                send_sale_notification(item)
                new_sale_count += 1
                
                # 寫入歷史紀錄
                unified = convert_591_sale_to_unified(item, tw_now_str)
                history.append(unified)
                time.sleep(2)
            else:
                print(f"過濾重複 591 售屋 (圖片+樓層相同): {house_id}")
            seen_sale_ids.add(house_id)

    # ─── 處理 591 新租屋 ───
    for item in rent_items:
        rent_id = str(item.get("rent_id", ""))
        if not rent_id: continue
        if rent_id not in seen_rent_ids:
            if not is_duplicate_591_listing(item, history, "rent"):
                print(f"發現新 591 租屋物件: {rent_id}")
                send_rent_notification(item)
                new_rent_count += 1
                
                unified = convert_591_rent_to_unified(item, tw_now_str)
                history.append(unified)
                time.sleep(2)
            else:
                print(f"過濾重複 591 租屋 (圖片+樓層相同): {rent_id}")
            seen_rent_ids.add(rent_id)

    # ─── 處理實價登錄新成交 (買賣) ───
    for item in lvr_sale_raw:
        norm = normalize_lvr(item)
        hash_val = get_lvr_transaction_hash(norm, "sale")
        if hash_val not in seen_lvr_sale_hashes:
            print(f"發現新實價登錄成交: {hash_val}")
            unified = convert_lvr_sale_to_unified(norm, hash_val, tw_now_str)
            send_lvr_sale_notification(unified)
            new_lvr_sale_count += 1
            
            history.append(unified)
            seen_lvr_sale_hashes.add(hash_val)
            time.sleep(2)

    # ─── 處理實價登錄新租賃 (租賃) ───
    for item in lvr_rent_raw:
        norm = normalize_lvr(item)
        hash_val = get_lvr_transaction_hash(norm, "rent")
        if hash_val not in seen_lvr_rent_hashes:
            print(f"發現新實價登錄租賃: {hash_val}")
            unified = convert_lvr_rent_to_unified(norm, hash_val, tw_now_str)
            send_lvr_rent_notification(unified)
            new_lvr_rent_count += 1
            
            history.append(unified)
            seen_lvr_rent_hashes.add(hash_val)
            time.sleep(2)

    # 發送統計摘要
    send_summary(new_sale_count, new_rent_count, len(sale_items), len(rent_items),
                 new_lvr_sale_count, new_lvr_rent_count, tw_now_str)

    # 更新持久化狀態 (保留當前在售/在租的 ID，防止 seen_ids 無限膨脹，但 LVR hashes 必須全部累計)
    current_sale_ids = [str(item["houseid"]) for item in sale_items if item.get("houseid")]
    current_rent_ids = [str(item["rent_id"]) for item in rent_items if item.get("rent_id")]
    
    seen["sale"] = current_sale_ids
    seen["rent"] = current_rent_ids
    seen["lvr_sales"] = list(seen_lvr_sale_hashes)
    seen["lvr_rentals"] = list(seen_lvr_rent_hashes)
    
    save_seen_ids(seen)
    save_history(history)
    print(f"[{tw_now_str}] 完成！")

if __name__ == "__main__":
    asyncio.run(main())
