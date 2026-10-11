#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PanWatch Simplified Chinese to Traditional Chinese (Taiwan standard) Converter.
Uses opencc s2twp plus Taiwan finance, stock trading, and software UI custom dictionary.
"""

import os
import re
import sys
import opencc

s2twp = opencc.OpenCC('s2twp.json')

# Custom Taiwan finance, stock trading, and software UI dictionary
# Applied after OpenCC s2twp
CUSTOM_REPLACEMENTS = [
    # 帳字轉換 (Taiwan standard uses 巾部: 帳號, 帳戶, 帳單, 轉帳, 記帳, 查帳, 對帳, 帳面)
    ('\u8cec', '\u5e33'),  # 賬 -> 帳

    # 許可權 -> 權限
    ('許可權', '權限'),

    # 證券與交易術語 (Taiwan Stock & Trading Terminology)
    ('止損價', '停損價'),
    ('止盈價', '停利價'),
    ('止損點', '停損點'),
    ('止盈點', '停利點'),
    ('止損', '停損'),
    ('止盈', '停利'),
    ('換手率', '周轉率'),
    ('換手', '周轉'),
    ('總收益率', '總報酬率'),
    ('區間收益率', '區間報酬率'),
    ('年化收益率', '年化報酬率'),
    ('收益率', '報酬率'),
    ('賺賠比', '賺賠比'),
    ('盈虧比', '賺賠比'),
    ('盈虧', '損益'),
    ('浮動盈虧', '未實現損益'),
    ('浮動損益', '未實現損益'),
    ('未實現獲利', '未實現獲利'),
    ('未實現損失', '未實現損失'),
    ('加倉', '加碼'),
    ('減倉', '減碼'),
    ('清倉', '出清'),
    ('最大回撤', '最大回檔'),
    ('回撤', '回檔'),
    ('縮量回調', '縮量回檔'),
    ('研報', '研究報告'),
    ('覆盤', '覆盤'),
    ('復盤', '覆盤'),
    ('自選股', '自選股'),
    ('模擬盤', '模擬交易'),
    ('模擬交易', '模擬交易'),

    # AI 與軟體術語 (Taiwan Tech & UI Terminology)
    ('智能體', '智慧體'),
    ('智慧代理', '智慧代理'),
    ('智能代理', '智慧代理'),
    ('人工智能', '人工智慧'),
    ('提示詞', '提示詞'),
    ('實時', '即時'),
    ('鏈接', '連結'),
    ('退出登錄', '登出'),
    ('登錄', '登入'),
    ('登陸', '登入'),
    ('註銷', '註銷'),
    ('默認', '預設'),
    ('設置', '設定'),
    ('資料庫', '資料庫'),
    ('數據庫', '資料庫'),
    ('資料來源', '資料來源'),
    ('數據源', '資料來源'),
    ('中繼資料', '中繼資料'),
    ('元數據', '中繼資料'),
    ('數據', '資料'),
    ('記憶體', '記憶體'),
    ('內存', '記憶體'),
    ('磁碟', '磁碟'),
    ('硬碟', '硬碟'),
    ('硬盤', '硬碟'),
    ('非同步', '非同步'),
    ('異步', '非同步'),
    ('回應', '回應'),
    ('響應', '回應'),
    ('快取', '快取'),
    ('緩存', '快取'),
    ('重新整理', '重新整理'),
    ('刷新', '重新整理'),
    ('彈跳視窗', '彈跳視窗'),
    ('彈窗', '彈跳視窗'),
    ('對話方塊', '對話方塊'),
    ('對話框', '對話方塊'),
    ('專案', '專案'),
    ('源代碼', '原始碼'),
    ('源碼', '原始碼'),
    ('原始碼', '原始碼'),
    ('程式碼庫', '程式碼庫'),
    ('代碼庫', '程式碼庫'),
    ('代碼', '程式碼'),
    ('使用者端', '使用者端'),
    ('用戶端', '使用者端'),
    ('客戶端', '使用者端'),
    ('伺服器端', '伺服器端'),
    ('服務端', '伺服器端'),
    ('伺服器', '伺服器'),
    ('服務器', '伺服器'),
    ('使用者', '使用者'),
    ('用戶', '使用者'),
    ('現有使用者', '現有使用者'),
    ('現有用戶', '現有使用者'),
    ('通知管道', '通知管道'),
    ('通知渠道', '通知管道'),
    ('管道', '管道'),
    ('渠道', '管道'),
    ('欄位', '欄位'),
    ('字段', '欄位'),
    ('字串', '字串'),
    ('字符串', '字串'),
    ('陣列', '陣列'),
    ('數組', '陣列'),
    ('函式', '函式'),
    ('函數', '函式'),
    ('單元測試', '單元測試'),
    ('覆蓋率', '覆蓋率'),
    ('腳本', '腳本'),
    ('最佳化', '最佳化'),
    ('優化', '最佳化'),
    ('外掛程式', '外掛程式'),
    ('外掛', '外掛'),
    ('插件', '外掛'),
    ('元件', '元件'),
    ('組件', '元件'),
    ('網路', '網路'),
    ('網絡', '網路'),
    ('執行緒', '執行緒'),
    ('線程', '執行緒'),
    ('行程', '行程'),
    ('進程', '行程'),
    ('佇列', '佇列'),
    ('隊列', '佇列'),
    ('主控台', '主控台'),
    ('控制台', '主控台'),
    ('回呼', '回呼'),
    ('回調', '回呼'),
    ('回饋', '回饋'),
    ('反饋', '回饋'),
    ('迴歸測試', '迴歸測試'),
    ('回歸測試', '迴歸測試'),
    ('詳細資訊', '詳細資訊'),
    ('詳細信息', '詳細資訊'),
    ('詳情', '詳細資訊'),
    ('信息', '訊息'),
    ('支援', '支援'),
    ('支持', '支援'),
    ('介面', '介面'),
    ('接口', '介面'),
    ('透過', '透過'),
    ('應用程式', '應用程式'),
]

ZH_PATTERN = re.compile(r'[\u4e00-\u9fff]')

def convert_text(text: str) -> str:
    if not ZH_PATTERN.search(text):
        return text
    # 1. Base OpenCC s2twp conversion
    converted = s2twp.convert(text)
    # 2. Taiwan custom dictionary replacements
    for orig, target in CUSTOM_REPLACEMENTS:
        converted = converted.replace(orig, target)
    return converted

def process_file(filepath: str) -> bool:
    try:
        with open(filepath, 'r', encoding='utf-8') as fh:
            content = fh.read()
    except Exception as e:
        print(f"Skipping {filepath} (cannot read): {e}")
        return False

    if not ZH_PATTERN.search(content):
        return False

    new_content = convert_text(content)
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as fh:
            fh.write(new_content)
        return True
    return False

def main():
    include_exts = (
        '.py', '.ts', '.tsx', '.html', '.json', '.yaml', '.yml',
        '.md', '.txt', '.sh', '.toml', '.ps1'
    )
    skip_dirs = {
        '.git', '.venv', 'node_modules', 'dist', '__pycache__',
        '.brain', '.pytest_cache', 'coverage'
    }

    count = 0
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith(include_exts):
                path = os.path.join(root, f)
                # Skip package locks or auto-generated json if any
                if f in ('pnpm-lock.yaml', 'package-lock.json', 'convert_to_zh_tw.py'):
                    continue
                if process_file(path):
                    count += 1
                    print(f"Converted: {path}")

    print(f"\nTotal converted files: {count}")

if __name__ == '__main__':
    main()
