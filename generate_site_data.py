import html
import json
import re
import time
import urllib.parse
from collections import Counter, defaultdict
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
_wiki_cache = {}


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
    try:
        resp = _session.get(
            TRANSLATE_URL,
            params={"client": "gtx", "sl": "ja", "tl": "zh-CN", "dt": "t", "q": text},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        translated = "".join(part[0] for part in data[0] if part and part[0])
    except Exception:
        translated = text
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
        return {"dyeId": None, "dyeNameCn": "", "dyeDisplayCn": "无指定染色", "dyeRequired": False}
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


def parse_ddg_results(text):
    links = re.findall(r'nofollow" class="result__a" href="(.*?)">(.*?)</a>', text)
    results = []
    for href, title in links:
        href = html.unescape(href)
        m = re.search(r'uddg=([^&]+)', href)
        clean = urllib.parse.unquote(m.group(1)) if m else href
        title_text = re.sub('<.*?>', '', title)
        results.append((title_text, clean))
    return results


def parse_bing_results(text):
    results = []
    for m in re.finditer(r'<li class="b_algo".*?<h2><a href="(.*?)"[^>]*>(.*?)</a></h2>', text, re.S):
        href = html.unescape(m.group(1))
        title_text = re.sub('<.*?>', '', m.group(2))
        results.append((title_text, href))
    return results


def evaluate_wiki_candidates(name_cn, candidates):
    for title_text, clean in candidates:
        if 'ff14.huijiwiki.com/wiki/' not in clean:
            continue
        if ('最终幻想XIV中文维基' in title_text or '灰机wiki' in title_text) and name_cn in title_text:
            return {
                "wikiMatched": True,
                "wikiTitle": title_text,
                "wikiPageTitle": title_text.split(' - ')[0],
                "wikiUrl": clean,
                "wikiSnippet": "",
                "wikiStatusText": "已对词条",
            }
    return None


def find_wiki(name_cn):
    if name_cn in _wiki_cache:
        return _wiki_cache[name_cn]

    queries = [
        f'{name_cn} 灰机wiki',
        f'物品:{name_cn} 灰机wiki',
        f'{name_cn} 最终幻想XIV中文维基',
    ]

    for q in queries:
        url = 'https://duckduckgo.com/html/?q=' + urllib.parse.quote(q)
        try:
            resp = _session.get(url, timeout=20)
            if resp.status_code == 200:
                hit = evaluate_wiki_candidates(name_cn, parse_ddg_results(resp.text))
                if hit:
                    _wiki_cache[name_cn] = hit
                    return hit
        except Exception:
            pass
        try:
            bing = _session.get('https://www.bing.com/search?q=' + urllib.parse.quote(q), timeout=20)
            if bing.status_code == 200:
                hit = evaluate_wiki_candidates(name_cn, parse_bing_results(bing.text))
                if hit:
                    _wiki_cache[name_cn] = hit
                    return hit
        except Exception:
            pass
        time.sleep(0.2)

    miss = {
        "wikiMatched": False,
        "wikiTitle": "",
        "wikiPageTitle": "",
        "wikiUrl": "",
        "wikiSnippet": "",
        "wikiStatusText": "待补校验",
    }
    _wiki_cache[name_cn] = miss
    return miss


def build_theme_item_map(themes_rows):
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
    return theme_item_map


def row_base(cfg, week, theme_id, theme, dye_info):
    return {
        "week": week,
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
        **dye_info,
    }


def build_current_rows(latest_row, theme_item_map, overrides):
    all_rows = []
    slot_summary = []
    week = int_or_none(latest_row[0])
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
                **row_base(cfg, week, theme_id, theme, dye_info),
                "rank": rank,
                **item,
                **wiki,
            }
            hero_items.append(row)
            all_rows.append(row)
        slot_summary.append({
            **row_base(cfg, week, theme_id, theme, dye_info),
            "candidateCount": len(item_ids),
            "heroItems": hero_items,
        })
    return slot_summary, all_rows


def build_history(data_rows, overrides):
    history = []
    slot_theme_counter = defaultdict(Counter)
    weekly_theme_counter = Counter()
    dye_counter = defaultdict(Counter)

    for raw in data_rows:
        week = int_or_none(raw[0]) if raw else None
        if not week:
            continue
        weekly_theme_id = int_or_none(raw[1]) if len(raw) > 1 else None
        weekly_theme = fetch_theme_name("FashionCheckWeeklyTheme", weekly_theme_id, overrides, "weeklyThemes") if weekly_theme_id else {"en":"","ja":"","cn":"","source":""}
        weekly_theme_counter[weekly_theme_id] += 1
        slot_entries = []
        for cfg in SLOT_CONFIGS:
            theme_id = int_or_none(raw[cfg["data_idx"]]) if len(raw) > cfg["data_idx"] else None
            if not theme_id:
                continue
            theme = fetch_theme_name("FashionCheckThemeCategory", theme_id, overrides, cfg["key"])
            dye_id = int_or_none(raw[cfg["dye_idx"]]) if len(raw) > cfg["dye_idx"] else None
            dye_info = fetch_dye(dye_id)
            slot_entries.append({
                **row_base(cfg, week, theme_id, theme, dye_info),
            })
            slot_theme_counter[cfg["slot_key"]][theme_id] += 1
            dye_counter[cfg["slot_key"]][dye_info["dyeDisplayCn"]] += 1
        history.append({
            "week": week,
            "timestamp": int_or_none(raw[13]) if len(raw) > 13 else None,
            "weeklyThemeId": weekly_theme_id,
            "weeklyThemeNameEn": weekly_theme["en"],
            "weeklyThemeNameJa": weekly_theme["ja"],
            "weeklyThemeNameCn": weekly_theme["cn"],
            "slots": slot_entries,
        })

    history.sort(key=lambda x: x["week"], reverse=True)

    weekly_theme_stats = []
    for theme_id, count in weekly_theme_counter.most_common():
        theme = fetch_theme_name("FashionCheckWeeklyTheme", theme_id, overrides, "weeklyThemes") if theme_id else {"en":"","ja":"","cn":"","source":""}
        weekly_theme_stats.append({
            "themeId": theme_id,
            "themeNameCn": theme["cn"],
            "themeNameEn": theme["en"],
            "count": count,
        })

    slot_theme_stats = {}
    for cfg in SLOT_CONFIGS:
        items = []
        for theme_id, count in slot_theme_counter[cfg["slot_key"]].most_common():
            theme = fetch_theme_name("FashionCheckThemeCategory", theme_id, overrides, cfg["key"])
            items.append({
                "slotKey": cfg["slot_key"],
                "slotCn": cfg["slot_cn"],
                "slotEn": cfg["slot_en"],
                "themeId": theme_id,
                "themeNameCn": theme["cn"],
                "themeNameEn": theme["en"],
                "count": count,
            })
        slot_theme_stats[cfg["slot_key"]] = items

    dye_stats = {}
    for cfg in SLOT_CONFIGS:
        dye_stats[cfg["slot_key"]] = [
            {"slotCn": cfg["slot_cn"], "slotEn": cfg["slot_en"], "dyeDisplayCn": dye_name, "count": count}
            for dye_name, count in dye_counter[cfg["slot_key"]].most_common()
        ]

    return history, {
        "totalWeeks": len(history),
        "weeklyThemeCounts": weekly_theme_stats,
        "slotThemeCounts": slot_theme_stats,
        "dyeCounts": dye_stats,
    }


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
    theme_item_map = build_theme_item_map(theme_rows)
    slot_summary, rows = build_current_rows(latest, theme_item_map, overrides)
    history, stats = build_history(data_rows, overrides)
    payload = {
        "schemaVersion": 3,
        "generatedAt": int(time.time()),
        "week": int_or_none(latest[0]),
        "weeklyThemeId": weekly_theme_id,
        "weeklyThemeNameEn": weekly_theme["en"],
        "weeklyThemeNameJa": weekly_theme["ja"],
        "weeklyThemeNameCn": weekly_theme["cn"],
        "weeklyThemeCnSource": weekly_theme["source"],
        "slotSummary": slot_summary,
        "rows": rows,
        "historyWeeks": history,
        "stats": stats,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(json.dumps({
        "week": payload["week"],
        "weeklyThemeNameCn": payload["weeklyThemeNameCn"],
        "rows": len(rows),
        "historyWeeks": len(history),
        "wikiMatched": sum(1 for r in rows if r.get("wikiMatched")),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
