import html
import json
import re
import time
import urllib.parse
from pathlib import Path

import jwt
import requests

ROOT = Path(__file__).resolve().parent
DATA_BIN = ROOT.parent / "FashionReport" / "FashionReport" / "Data.bin"
DATA_BIN_URL = "https://raw.githubusercontent.com/TheRedheadedWitch/FashionReport/main/FashionReport/Data.bin"
OUTPUT = ROOT / "site-data.json"
THEME_OVERRIDE_PATH = ROOT / "theme_mappings.json"
SHEET_ID = "1RWNR3MeKq49wfGVEBGIhDMtrJL40uhbtzuZtIpUbVw8"
ICON_BASE = "https://cafemaker.wakingsands.com"
USER_AGENT = "Mozilla/5.0 (Hermes FF14 Fashion CN builder)"
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
XIVAPI_V2 = "https://v2.xivapi.com/api/sheet"

SLOT_CONFIGS = [
    {"slot_id": 3, "slot_key": "head", "slot_cn": "头部", "slot_en": "Head", "data_idx": 3, "dye_idx": 15, "key": "slotThemes"},
    {"slot_id": 4, "slot_key": "body", "slot_cn": "身体", "slot_en": "Body", "data_idx": 4, "dye_idx": 16, "key": "slotThemes"},
    {"slot_id": 7, "slot_key": "legs", "slot_cn": "腿部", "slot_en": "Legs", "data_idx": 6, "dye_idx": 18, "key": "slotThemes"},
    {"slot_id": 8, "slot_key": "feet", "slot_cn": "脚部", "slot_en": "Feet", "data_idx": 7, "dye_idx": 19, "key": "slotThemes"},
]

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_theme_cache = {}
_item_cache = {}
_dye_cache = {}
_translate_cache = {}


def get_json(url, **kwargs):
    resp = _session.get(url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def load_google_credentials():
    if DATA_BIN.exists():
        text = DATA_BIN.read_text("utf-8")
    else:
        resp = _session.get(DATA_BIN_URL, timeout=30)
        resp.raise_for_status()
        text = resp.text
    fixed = "".join(chr((~ord(c)) & 0xFFFF) for c in text)
    return json.loads(fixed[fixed.find("{"):])


def get_google_access_token(creds):
    now = int(time.time())
    payload = {
        "iss": creds["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud": creds["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }
    assertion = jwt.encode(payload, creds["private_key"], algorithm="RS256")
    resp = _session.post(
        creds["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_sheet(access_token, sheet_range):
    resp = _session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sheet_range}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("values", [])


def int_or_none(v):
    try:
        if v in (None, ""):
            return None
        return int(v)
    except Exception:
        return None


def load_theme_overrides():
    if THEME_OVERRIDE_PATH.exists():
        return json.loads(THEME_OVERRIDE_PATH.read_text("utf-8"))
    return {"weeklyThemes": {}, "slotThemes": {}}


def translate_ja_to_cn(text):
    if not text:
        return ""
    if text in _translate_cache:
        return _translate_cache[text]
    resp = _session.get(
        TRANSLATE_URL,
        params={"client": "gtx", "sl": "ja", "tl": "zh-CN", "dt": "t", "q": text},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    translated = "".join(part[0] for part in data[0] if part and part[0])
    _translate_cache[text] = translated
    return translated


def fetch_theme_name(sheet_name, row_id, overrides, bucket):
    cache_key = (sheet_name, row_id, bucket)
    if cache_key in _theme_cache:
        return _theme_cache[cache_key]
    en_data = get_json(f"{XIVAPI_V2}/{sheet_name}/{row_id}", params={"fields": "Name"})
    ja_data = get_json(f"{XIVAPI_V2}/{sheet_name}/{row_id}", params={"fields": "Name", "language": "ja"})
    name_en = (en_data.get("fields") or {}).get("Name") or ""
    name_ja = (ja_data.get("fields") or {}).get("Name") or ""
    name_cn_auto = translate_ja_to_cn(name_ja) if name_ja else name_en
    override = overrides.get(bucket, {}).get(str(row_id), {})
    result = {
        "en": override.get("en") or name_en,
        "ja": override.get("ja") or name_ja,
        "cn": override.get("cn") or name_cn_auto,
        "source": "override" if override.get("cn") else "jp_translate",
    }
    _theme_cache[cache_key] = result
    return result


def fetch_item(item_id):
    if item_id in _item_cache:
        return _item_cache[item_id]
    data = get_json(f"{ICON_BASE}/item/{item_id}?columns=Name,Icon,LevelItem")
    icon = data.get("Icon") or ""
    result = {
        "itemId": item_id,
        "nameCn": data.get("Name") or f"物品 {item_id}",
        "iconUrl": f"{ICON_BASE}{icon}" if icon and icon.startswith("/") else icon,
        "itemLevel": data.get("LevelItem") or 0,
    }
    _item_cache[item_id] = result
    return result


def fetch_dye(dye_id):
    if not dye_id:
        return {
            "dyeId": None,
            "dyeNameCn": "",
            "dyeDisplayCn": "无指定染色",
            "dyeRequired": False,
        }
    if dye_id in _dye_cache:
        return _dye_cache[dye_id]
    data = get_json(f"{ICON_BASE}/stain/{dye_id}?columns=Name")
    dye_name = data.get("Name") or ""
    result = {
        "dyeId": dye_id,
        "dyeNameCn": dye_name,
        "dyeDisplayCn": dye_name or "无指定染色",
        "dyeRequired": bool(dye_name),
    }
    _dye_cache[dye_id] = result
    return result


def find_wiki(name_cn):
    q = urllib.parse.quote(f"{name_cn} 灰机wiki")
    resp = _session.get(f"https://duckduckgo.com/html/?q={q}", timeout=20)
    resp.raise_for_status()
    links = re.findall(r'nofollow\" class=\"result__a\" href=\"(.*?)\">(.*?)</a>', resp.text)
    for href, title in links[:8]:
        href = html.unescape(href)
        m = re.search(r"uddg=([^&]+)", href)
        clean = urllib.parse.unquote(m.group(1)) if m else href
        title_text = re.sub("<.*?>", "", title)
        if "最终幻想XIV中文维基" in title_text and name_cn in title_text:
            return {
                "wikiMatched": True,
                "wikiTitle": title_text,
                "wikiPageTitle": title_text.split(" - ")[0],
                "wikiUrl": clean,
                "wikiSnippet": "",
                "wikiStatusText": "已对词条",
            }
    return {
        "wikiMatched": False,
        "wikiTitle": "",
        "wikiPageTitle": "",
        "wikiUrl": "",
        "wikiSnippet": "",
        "wikiStatusText": "待补校验",
    }


def build_rows(latest_row, themes_rows, overrides):
    theme_item_map = {}
    for row in themes_rows:
        if len(row) < 3:
            continue
        theme_id = int_or_none(row[0])
        item_id = int_or_none(row[1])
        slot_id = int_or_none(row[2])
        if not (theme_id and item_id and slot_id):
            continue
        theme_item_map.setdefault((theme_id, slot_id), []).append(item_id)

    all_rows = []
    slot_summary = []
    for cfg in SLOT_CONFIGS:
        theme_id = int_or_none(latest_row[cfg["data_idx"]]) if len(latest_row) > cfg["data_idx"] else None
        if not theme_id:
            continue
        theme = fetch_theme_name("FashionCheckThemeCategory", theme_id, overrides, cfg["key"])
        dye_id = int_or_none(latest_row[cfg["dye_idx"]]) if len(latest_row) > cfg["dye_idx"] else None
        dye_info = fetch_dye(dye_id)
        item_ids = theme_item_map.get((theme_id, cfg["slot_id"]), [])
        hero_items = []
        for rank, item_id in enumerate(item_ids, start=1):
            item = fetch_item(item_id)
            wiki = find_wiki(item["nameCn"])
            row = {
                "week": int_or_none(latest_row[0]),
                "slot": cfg["slot_cn"],
                "slotCn": cfg["slot_cn"],
                "slotEn": cfg["slot_en"],
                "slotKey": cfg["slot_key"],
                "slotId": cfg["slot_id"],
                "themeId": theme_id,
                "themeNameEn": theme["en"],
                "themeNameJa": theme["ja"],
                "themeNameCn": theme["cn"],
                "themeCnSource": theme["source"],
                "rank": rank,
                **item,
                **wiki,
                **dye_info,
            }
            hero_items.append(row)
            all_rows.append(row)
        slot_summary.append({
            "slot": cfg["slot_cn"],
            "slotCn": cfg["slot_cn"],
            "slotEn": cfg["slot_en"],
            "slotKey": cfg["slot_key"],
            "slotId": cfg["slot_id"],
            "themeId": theme_id,
            "themeNameEn": theme["en"],
            "themeNameJa": theme["ja"],
            "themeNameCn": theme["cn"],
            "themeCnSource": theme["source"],
            "candidateCount": len(item_ids),
            **dye_info,
            "heroItems": hero_items,
        })
    return slot_summary, all_rows


def main():
    creds = load_google_credentials()
    token = get_google_access_token(creds)
    data_rows = fetch_sheet(token, "Data!A:T")
    theme_rows = fetch_sheet(token, "Theme!A:C")
    if not data_rows:
        raise RuntimeError("Data sheet empty")
    latest = data_rows[-1]
    overrides = load_theme_overrides()
    weekly_theme_id = int_or_none(latest[1]) if len(latest) > 1 else None
    weekly_theme = fetch_theme_name("FashionCheckWeeklyTheme", weekly_theme_id, overrides, "weeklyThemes") if weekly_theme_id else {"en": "", "ja": "", "cn": "", "source": ""}
    slot_summary, rows = build_rows(latest, theme_rows, overrides)
    payload = {
        "schemaVersion": 2,
        "generatedAt": int(time.time()),
        "week": int_or_none(latest[0]),
        "weeklyThemeId": weekly_theme_id,
        "weeklyThemeNameEn": weekly_theme["en"],
        "weeklyThemeNameJa": weekly_theme["ja"],
        "weeklyThemeNameCn": weekly_theme["cn"],
        "weeklyThemeCnSource": weekly_theme["source"],
        "slotSummary": slot_summary,
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(json.dumps({"week": payload["week"], "weeklyThemeNameCn": payload["weeklyThemeNameCn"], "slots": len(slot_summary), "rows": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
