# 591 社區物件通知機器人 🏠

自動抓取 [591](https://market.591.com.tw/) 指定社區的售屋/租屋資訊，有新物件時推送到 Telegram。

## 功能

- ✅ 自動抓取售屋 & 租屋列表（透過 591 BFF API）
- ✅ 比對已通知過的物件 ID，只推送**新物件**
- ✅ 格式化 Telegram 訊息（含價格、坪數、格局、樓層、仲介等）
- ✅ 降價物件標記提示
- ✅ GitHub Actions 排程自動執行（每 30 分鐘）
- ✅ 透過 Git 持久化已通知紀錄，避免重複通知

## 設定步驟

### 1. 建立 Telegram Bot

1. 在 Telegram 找到 [@BotFather](https://t.me/BotFather)
2. 傳送 `/newbot`，照指示建立 Bot，取得 **Bot Token**
3. 將 Bot 加入你想接收通知的群組或頻道
4. 取得 **Chat ID**（可傳送訊息後用 `https://api.telegram.org/bot<TOKEN>/getUpdates` 查看）

### 2. 設定 GitHub Secrets

在你的 GitHub repo → **Settings** → **Secrets and variables** → **Actions** 中新增：

| Secret 名稱 | 說明 | 範例 |
|---|---|---|
| `TG_TOKEN` | Telegram Bot Token | `123456:ABC-DEF...` |
| `TG_CHAT_ID` | 接收通知的 Chat ID | `-1001234567890` |
| `COMMUNITY_ID` | 591 社區 ID（選填，預設 `102909`） | `102909` |

### 3. 啟用 GitHub Actions

推送到 GitHub 後，Actions 會自動依 cron 排程執行。  
也可以到 **Actions** 頁面手動 **Run workflow** 測試。

## 排程說明

預設每 30 分鐘執行一次，僅在台灣時間 **08:00 – 23:30** 之間。  
可在 `.github/workflows/notify.yml` 中修改 cron 表達式。

## 本地測試

```bash
# 設定環境變數
export TG_TOKEN="你的_TELEGRAM_BOT_TOKEN"
export TG_CHAT_ID="你的_CHAT_ID"
export COMMUNITY_ID="102909"

# 安裝依賴 & 執行
pip install -r requirements.txt
python notify.py
```

## 目前監控社區

- **青喆 SOHO** — [社區頁面](https://market.591.com.tw/102909)（新北市中和區，成交均價 48.0 萬/坪）

## 訊息範例

```
🏠 售屋新物件
━━━━━━━━━━━━━━━
📌 青喆SOHO｜南山中學｜邊間全新三房電梯宅
💵 總價: 2,798 萬 (78.8萬/坪)
🏢 格局: 3房2廳
📐 坪數: 35.53 坪
🏗 樓層: 5F/14F
📍 地址: 中和區-青喆SOHO
👤 仲介: 陳毅
👀 瀏覽: 12 人
🏷 標籤: 有陽台

🔗 查看物件
```
