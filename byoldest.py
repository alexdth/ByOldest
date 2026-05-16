#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ByOldest — pywebview Interface
Sort a YouTube channel's videos from oldest to newest.

Copyright (c) 2026 Kero. All rights reserved.
Free for personal, non-commercial use only.
Commercial use by any party other than the author is strictly prohibited.
See LICENSE file for full terms.

Dependencies: pip install google-api-python-client keyring cryptography pywebview
Optional:     pip install plyer   (Windows native notifications)
"""

APP_VERSION = "1.0.0"

import sys
import os
import json
import time
import threading
import hashlib

# ── Config directory: %APPDATA%\ByOldest\ (Windows) or ~/.config/ByOldest/ ──
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
CONFIG_DIR      = os.path.join(_APPDATA, "ByOldest")
CONFIG_PATH     = os.path.join(CONFIG_DIR, "byOldest_config.dat")
CONFIG_PATH_OLD = os.path.join(CONFIG_DIR, "byOldest_config.json")
HISTORY_DIR     = os.path.join(CONFIG_DIR, "history")
HISTORY_MAX = 10

KEYRING_SERVICE = "ByOldest"
KEYRING_USER    = "youtube_api_key"

# ── Cryptography ──────────────────────────────────────────────────────────────
try:
    from cryptography.fernet import Fernet, InvalidToken
    import base64 as _b64
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

def _machine_id() -> str:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return guid
    except Exception:
        pass
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as fh:
                mid = fh.read().strip()
                if mid:
                    return mid
        except Exception:
            pass
    import socket
    return os.environ.get("USERNAME", "") + "@" + socket.gethostname()

def _derive_fernet_key() -> bytes:
    salt = b"ByOldest-v1"
    dk = hashlib.pbkdf2_hmac("sha256", _machine_id().encode(), salt, iterations=200_000, dklen=32)
    return _b64.urlsafe_b64encode(dk)

def _encrypt(data: bytes) -> bytes:
    from cryptography.fernet import Fernet
    return Fernet(_derive_fernet_key()).encrypt(data)

def _decrypt(data: bytes) -> bytes:
    from cryptography.fernet import Fernet
    return Fernet(_derive_fernet_key()).decrypt(data)


# ── Dependency check ──────────────────────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("ERROR: pip install google-api-python-client")
    sys.exit(1)

if not _CRYPTO_OK:
    print("ERROR: pip install cryptography")
    sys.exit(1)

try:
    import keyring as _keyring
    _KEYRING_OK = True
except ImportError:
    _keyring = None
    _KEYRING_OK = False

try:
    from plyer import notification as _plyer_notif
    _PLYER_OK = True
except ImportError:
    _PLYER_OK = False

try:
    import webview
except ImportError:
    print("ERROR: pip install pywebview")
    sys.exit(1)


def _notify(title: str, message: str):
    if not _PLYER_OK:
        return
    try:
        _plyer_notif.notify(title=title, message=message, app_name="ByOldest", timeout=5)
    except Exception:
        pass

def _load_api_key() -> str:
    if _KEYRING_OK:
        try:
            val = _keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if val:
                return val
        except Exception:
            pass
    return ""

def _save_api_key(key: str):
    if _KEYRING_OK and key:
        try:
            _keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def _read_config_raw() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH_OLD) and not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH_OLD, "r", encoding="utf-8") as fh:
                legacy = json.load(fh)
            if _CRYPTO_OK:
                payload = json.dumps(legacy, ensure_ascii=False).encode()
                with open(CONFIG_PATH, "wb") as fh:
                    fh.write(_encrypt(payload))
                os.remove(CONFIG_PATH_OLD)
                return legacy
        except Exception:
            pass
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "rb") as fh:
            raw = fh.read()
        if _CRYPTO_OK:
            try:
                return json.loads(_decrypt(raw))
            except Exception:
                return {}
    except Exception:
        return {}
    # _CRYPTO_OK is guaranteed True at this point (checked at import time)
    return {}

def load_config() -> dict:
    cfg = _read_config_raw()
    cfg.setdefault("channel_history", [])
    cfg.setdefault("lang", "en")
    cfg.setdefault("mode", "standard")
    cfg.setdefault("open_after", True)
    cfg["api_key"] = _load_api_key()
    return cfg

def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Work on a copy so the caller's dict is never mutated
    api_key = cfg.get("api_key", "")
    if api_key:
        _save_api_key(api_key)
    if not _CRYPTO_OK:
        return
    try:
        safe = {k: v for k, v in cfg.items() if k != "api_key"}
        payload = json.dumps(safe, ensure_ascii=False).encode()
        with open(CONFIG_PATH, "wb") as fh:
            fh.write(_encrypt(payload))
    except Exception:
        pass

def add_to_history(cfg: dict, channel: str, query: str, mode: str, html_path: str):
    hist = cfg.get("channel_history", [])
    hist = [
        e for e in hist
        if not (
            (isinstance(e, dict) and e.get("channel") == channel and e.get("query") == query)
            or (isinstance(e, str) and e == channel and not query)
        )
    ]
    entry = {"channel": channel, "query": query, "mode": mode, "html_path": html_path}
    hist.insert(0, entry)
    cfg["channel_history"] = hist[:HISTORY_MAX]

def make_output_path(channel_name: str, query: str) -> str:
    import re, datetime
    def sanitize(s):
        s = re.sub(r'[\\/:*?"<>|]', '', s.strip())
        return re.sub(r'\s+', '_', s)[:40]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ch_part = sanitize(channel_name) or "channel"
    q_part  = ("_" + sanitize(query)) if query else ""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    return os.path.join(HISTORY_DIR, f"{ch_part}{q_part}_{ts}.html")


# ══════════════════════════════════════════════════════════════════════════════
#  TRANSLATIONS
# ══════════════════════════════════════════════════════════════════════════════

STRINGS = {
    "fr": {
        "app_subtitle":        "Trier les vidéos d'une chaîne — de la plus ancienne à la plus récente",
        "lang_btn":            "🌐 English",
        "label_api":           "Clé API Google YouTube",
        "label_channel":       "ID ou URL de chaîne",
        "label_query":         "Mot-clé (vide = toutes les vidéos)",
        "label_mode":          "Méthode",
        "label_open_after":    "Ouvrir le HTML après génération",
        "ph_channel":          "UCxxxxx ou https://youtube.com/@NomChaine",
        "ph_query":            "ex : movie",
        "btn_run":             "Lancer la recherche",
        "btn_running":         "Recherche en cours…",
        "mode_standard":       "Standard — titre uniquement (~1 unité/page)",
        "mode_precise":        "Précise — index complet (100 unités/page ⚠️)",
        "quota_used":          "Quota : ~{n} unités",
        "progress_label":      "{done} / {total} vidéos",
        "progress_fetching":   "Récupération…",
        "validating_channel":  "🔎 Vérification de la chaîne…",
        "channel_valid":       "✅ Chaîne valide : « {name} »",
        "err_invalid_channel": "❌ Chaîne introuvable.",
        "err_invalid_api_key": "❌ Clé API invalide (HTTP 400/403).",
        "err_no_api":          "❌ Clé API manquante.",
        "err_no_channel":      "❌ ID ou URL de chaîne manquant.",
        "resolving_handle":    "🔍 Résolution du handle…",
        "handle_found":        "   → ID : {cid}",
        "err_resolve":         "Impossible de résoudre l'ID. Utilise UCxxxxx.",
        "searching":           "🔍 Recherche « {query} » sur {channel_id}…",
        "searching_all":       "(toutes les vidéos)",
        "page":                "   Page {page}…",
        "found":               "✅ {n} vidéo(s) trouvée(s).",
        "diagnosing":          "⚠️ Aucun résultat. Diagnostic…",
        "done":                "🎉 Terminé ! Fichier HTML généré.",
        "err_generic":         "❌ Erreur : {e}",
        "html_saved":          "📄 Fichier : {path}",
        "retry":               "   ⚠️ Erreur réseau, tentative {n}/3…",
        "rate_limit":          "   ⏳ Rate limit, pause {s}s…",
        "diag_quota":          "❌ Quota dépassé (HTTP 403) : {reason}",
        "diag_api_err":        "❌ Erreur API (HTTP {status}) : {reason}",
        "diag_unexpected":     "❌ Erreur diagnostic : {e}",
        "diag_not_found":      "❌ Chaîne « {channel_id} » introuvable.",
        "diag_channel_found":  "   Chaîne : « {name} » ({n} vidéo(s))",
        "diag_no_videos":      "❌ Aucune vidéo publique.",
        "diag_no_match":       "❌ {n} vidéo(s) mais aucune ne correspond à « {query} ».",
        "diag_no_index":       "❌ {n} vidéo(s) mais l'API n'en retourne aucune.",
        "diag_no_index2":      "   (Les vidéos récentes peuvent ne pas être indexées.)",
        "html_all_videos":     "Toutes les vidéos",
        "html_channel":        "Chaîne",
        "html_sorted":         "Triées de la plus ancienne à la plus récente",
        "html_footer":         "Généré avec ByOldest",
        "html_videos":         "vidéos",
        "html_results":        "résultats",
        "html_lang":           "fr",
        "tab_search":          "Recherche",
        "tab_history":         "Historique",
        "history_title":       "Historique des recherches",
        "history_empty":       "Aucun historique.",
        "history_rerun":       "Relancer",
        "history_open_html":   "Voir les résultats",
        "history_clear_all":   "Tout effacer",
        "history_no_html":     "Fichier HTML introuvable.",
        "copied":              "✅ Chemin copié !",
        "notif_title":         "ByOldest",
        "notif_done":          "{n} vidéo(s) pour « {channel} ».",
        "copy_path":           "Copier le chemin",
        "open_html":           "Ouvrir le HTML",
        "mode_label":          "MODE",
        "channel_label":       "CHAÎNE",
        "query_label":         "MOT-CLÉ",
        "api_label":           "CLÉ API",
        "settings_title":      "Paramètres",
        "html_filter":         "Filtrer…",
        "html_grid":           "Grille",
        "html_list":           "Liste",
        "html_first":          "première",
        "html_last":           "dernière",
        "html_noresult":       "Aucun résultat.",
    },
    "en": {
        "app_subtitle":        "Sort a channel's videos — oldest to newest",
        "lang_btn":            "🌐 Français",
        "label_api":           "Google YouTube API Key",
        "label_channel":       "Channel ID or URL",
        "label_query":         "Keyword (empty = all videos)",
        "label_mode":          "Method",
        "label_open_after":    "Open HTML after generation",
        "ph_channel":          "UCxxxxx or https://youtube.com/@ChannelName",
        "ph_query":            "e.g. movie",
        "btn_run":             "Run search",
        "btn_running":         "Searching…",
        "mode_standard":       "Standard — title only (~1 unit/page)",
        "mode_precise":        "Precise — full index (100 units/page ⚠️)",
        "quota_used":          "Quota: ~{n} units",
        "progress_label":      "{done} / {total} videos",
        "progress_fetching":   "Fetching…",
        "validating_channel":  "🔎 Validating channel…",
        "channel_valid":       "✅ Channel found: « {name} »",
        "err_invalid_channel": "❌ Channel not found.",
        "err_invalid_api_key": "❌ Invalid API key (HTTP 400/403).",
        "err_no_api":          "❌ API key is missing.",
        "err_no_channel":      "❌ Channel ID or URL is missing.",
        "resolving_handle":    "🔍 Resolving handle…",
        "handle_found":        "   → ID: {cid}",
        "err_resolve":         "Cannot resolve channel ID. Use UCxxxxx.",
        "searching":           "🔍 Searching « {query} » on {channel_id}…",
        "searching_all":       "(all videos)",
        "page":                "   Page {page}…",
        "found":               "✅ {n} video(s) found.",
        "diagnosing":          "⚠️ No results. Diagnosing…",
        "done":                "🎉 Done! HTML file generated.",
        "err_generic":         "❌ Error: {e}",
        "html_saved":          "📄 File: {path}",
        "retry":               "   ⚠️ Network error, retry {n}/3…",
        "rate_limit":          "   ⏳ Rate limit, waiting {s}s…",
        "diag_quota":          "❌ Quota exceeded (HTTP 403): {reason}",
        "diag_api_err":        "❌ API error (HTTP {status}): {reason}",
        "diag_unexpected":     "❌ Diagnostics error: {e}",
        "diag_not_found":      "❌ Channel « {channel_id} » not found.",
        "diag_channel_found":  "   Channel: « {name} » ({n} video(s))",
        "diag_no_videos":      "❌ No public videos.",
        "diag_no_match":       "❌ {n} video(s) but none match « {query} ».",
        "diag_no_index":       "❌ {n} video(s) but the API returns none.",
        "diag_no_index2":      "   (Very recent videos may not be indexed yet.)",
        "html_all_videos":     "All videos",
        "html_channel":        "Channel",
        "html_sorted":         "Sorted oldest to newest",
        "html_footer":         "Generated with ByOldest",
        "html_videos":         "videos",
        "html_results":        "results",
        "html_lang":           "en",
        "tab_search":          "Search",
        "tab_history":         "History",
        "history_title":       "Search History",
        "history_empty":       "No history yet.",
        "history_rerun":       "Re-run",
        "history_open_html":   "View results",
        "history_clear_all":   "Clear all",
        "history_no_html":     "HTML file not found.",
        "copied":              "✅ Path copied!",
        "notif_title":         "ByOldest",
        "notif_done":          "{n} video(s) for « {channel} ».",
        "copy_path":           "Copy path",
        "open_html":           "Open HTML",
        "mode_label":          "MODE",
        "channel_label":       "CHANNEL",
        "query_label":         "KEYWORD",
        "api_label":           "API KEY",
        "settings_title":      "Settings",
        "html_filter":         "Filter…",
        "html_grid":           "Grid",
        "html_list":           "List",
        "html_first":          "first",
        "html_last":           "last",
        "html_noresult":       "No results.",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def api_call(request, log, t: dict, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 429:
                wait = 5 * attempt
                log(t["rate_limit"].format(s=wait))
                time.sleep(wait)
            elif e.resp.status in (500, 503) and attempt < max_retries:
                log(t["retry"].format(n=attempt))
                time.sleep(2 * attempt)
            else:
                raise
        except Exception:
            if attempt < max_retries:
                log(t["retry"].format(n=attempt))
                time.sleep(2 * attempt)
            else:
                raise
    raise RuntimeError("Max retries exceeded")


# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESS LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def resolve_and_validate_channel(api_key, channel_input, log, t, quota):
    yt = build("youtube", "v3", developerKey=api_key)
    c  = channel_input.strip()

    if "youtube.com/channel/" in c:
        channel_id = c.split("youtube.com/channel/")[-1].split("/")[0].split("?")[0]
        log(t["validating_channel"])
        try:
            r = api_call(yt.channels().list(part="snippet", id=channel_id), log, t)
        except HttpError as e:
            if e.resp.status in (400, 403):
                raise ValueError(t["err_invalid_api_key"])
            raise
        quota[0] += 1
        if not r.get("items"):
            raise ValueError(t["err_invalid_channel"])
        name = r["items"][0]["snippet"]["title"]
        log(t["channel_valid"].format(name=name))
        return channel_id, name

    if c.startswith("http://") or c.startswith("https://") or c.startswith("@"):
        log(t["resolving_handle"])
        handle = c.rstrip("/").split("?")[0].split("/")[-1].lstrip("@")
        try:
            r = api_call(yt.channels().list(part="snippet", forHandle=handle), log, t)
        except HttpError as e:
            if e.resp.status in (400, 403):
                raise ValueError(t["err_invalid_api_key"])
            raise
        quota[0] += 1
        if not r.get("items"):
            raise ValueError(t["err_resolve"])
        item = r["items"][0]
        cid  = item["id"]
        name = item["snippet"]["title"]
        log(t["handle_found"].format(cid=cid))
        log(t["channel_valid"].format(name=name))
        return cid, name

    log(t["validating_channel"])
    try:
        r = api_call(yt.channels().list(part="snippet", id=c), log, t)
    except HttpError as e:
        if e.resp.status in (400, 403):
            raise ValueError(t["err_invalid_api_key"])
        raise
    quota[0] += 1
    if not r.get("items"):
        raise ValueError(t["err_invalid_channel"])
    name = r["items"][0]["snippet"]["title"]
    log(t["channel_valid"].format(name=name))
    return c, name


def fetch_videos(api_key, channel_id, query, log, t, quota, progress_cb=None):
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page = [], None
    page = 0
    total_est = None

    while True:
        page += 1
        log(t["page"].format(page=page))
        res = api_call(
            youtube.search().list(
                part="snippet", channelId=channel_id, q=query,
                type="video", maxResults=50, order="date", pageToken=next_page
            ), log, t
        )
        quota[0] += 100
        if total_est is None:
            total_est = res.get("pageInfo", {}).get("totalResults", 0)
        for item in res.get("items", []):
            vid    = item["id"]["videoId"]
            snip   = item["snippet"]
            thumbs = snip["thumbnails"]
            thumb  = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default"))["url"]
            videos.append({
                "title": snip["title"], "date": snip["publishedAt"][:10],
                "published": snip["publishedAt"],
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": thumb, "channel": snip.get("channelTitle", "")
            })
        if progress_cb and total_est:
            progress_cb(min(len(videos) / total_est, 1.0), len(videos), total_est)
        next_page = res.get("nextPageToken")
        if not next_page:
            break

    if progress_cb:
        progress_cb(1.0, len(videos), len(videos))
    videos.sort(key=lambda v: v["published"])
    return videos


def fetch_videos_efficient(api_key, channel_id, query, log, t, quota, progress_cb=None, channel_data=None):
    youtube = build("youtube", "v3", developerKey=api_key)
    if channel_data and "contentDetails" in channel_data:
        ch_item = channel_data
    else:
        ch_res = api_call(
            youtube.channels().list(part="snippet,contentDetails,statistics", id=channel_id), log, t
        )
        quota[0] += 1
        if not ch_res.get("items"):
            raise ValueError(t["err_resolve"])
        ch_item = ch_res["items"][0]

    uploads_playlist = ch_item["contentDetails"]["relatedPlaylists"]["uploads"]
    total_est = int(ch_item.get("statistics", {}).get("videoCount", 0))
    videos, next_page = [], None
    page = 0
    fetched = 0
    query_lower = query.lower() if query else ""

    while True:
        page += 1
        log(t["page"].format(page=page))
        res = api_call(
            youtube.playlistItems().list(
                part="snippet", playlistId=uploads_playlist,
                maxResults=50, pageToken=next_page
            ), log, t
        )
        quota[0] += 1
        for item in res.get("items", []):
            snip  = item["snippet"]
            title = snip.get("title", "")
            fetched += 1
            if title in ("Deleted video", "Private video"):
                continue
            if query_lower and query_lower not in title.lower():
                continue
            rid = snip.get("resourceId", {})
            vid = rid.get("videoId", "")
            if not vid:
                continue
            thumbs = snip.get("thumbnails", {})
            thumb  = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            pub    = snip.get("publishedAt", "")
            videos.append({
                "title": title, "date": pub[:10], "published": pub,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": thumb, "channel": snip.get("channelTitle", "")
            })
        if progress_cb and total_est:
            progress_cb(min(fetched / total_est, 1.0), fetched, total_est)
        next_page = res.get("nextPageToken")
        if not next_page:
            break

    if progress_cb:
        progress_cb(1.0, fetched, fetched)
    videos.sort(key=lambda v: v["published"])
    return videos


def diagnose_empty_results(api_key, channel_id, query, log, t):
    youtube = build("youtube", "v3", developerKey=api_key)
    try:
        r = api_call(youtube.channels().list(part="snippet,statistics", id=channel_id), log, t)
    except HttpError as e:
        if e.resp.status == 403:
            log(t["diag_quota"].format(reason=e.reason))
        else:
            log(t["diag_api_err"].format(status=e.resp.status, reason=e.reason))
        return
    except Exception as e:
        log(t["diag_unexpected"].format(e=e))
        return

    if not r.get("items"):
        log(t["diag_not_found"].format(channel_id=channel_id))
        return
    ch = r["items"][0]
    ch_name   = ch["snippet"]["title"]
    nb_videos = int(ch["statistics"].get("videoCount", -1))
    log(t["diag_channel_found"].format(name=ch_name, n=nb_videos))
    if nb_videos == 0:
        log(t["diag_no_videos"].format(name=ch_name))
        return
    if query:
        log(t["diag_no_match"].format(name=ch_name, n=nb_videos, query=query))
    else:
        log(t["diag_no_index"].format(name=ch_name, n=nb_videos))
        log(t["diag_no_index2"])


def generate_html(videos, query, channel_id, output_path, log, t):
    titre_page    = query if query else t["html_all_videos"]
    channel_label = videos[0]["channel"] if videos else channel_id
    year_first    = videos[0]["date"][:4]  if videos else ""
    year_last     = videos[-1]["date"][:4] if videos else ""

    js_videos = json.dumps([
        {"n": i, "title": v["title"], "date": v["date"], "url": v["url"], "thumb": v["thumb"]}
        for i, v in enumerate(videos, 1)
    ], ensure_ascii=False)

    lbl_filter   = t["html_filter"]
    lbl_grid     = t["html_grid"]
    lbl_list     = t["html_list"]
    lbl_first    = t["html_first"]
    lbl_last     = t["html_last"]
    lbl_noresult = t["html_noresult"]

    html = f"""<!DOCTYPE html>
<html lang="{t['html_lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre_page} — {channel_label}</title>
<style>
:root{{
  --bg:#07101f;--surf:#0d1a2e;--surf2:#102038;--border:#111f33;
  --accent:#00c8d7;--text:#f0f4f8;--muted:#4a6a80;
  --radius:10px;--radius-sm:6px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
a{{color:inherit;text-decoration:none}}
.topbar{{background:var(--surf);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;position:sticky;top:0;z-index:10}}
.brand{{display:flex;align-items:center;gap:10px;min-width:0}}
.logo{{width:36px;height:36px;flex-shrink:0}}
.brand-name{{font-size:13px;font-weight:600}}
.brand-sub{{font-size:11px;color:var(--muted);margin-top:1px}}
.stats{{display:flex;gap:8px;flex-shrink:0}}
.stat{{background:var(--surf2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:5px 12px;text-align:center;min-width:58px}}
.stat-n{{font-size:16px;font-weight:600;line-height:1}}
.stat-n.accent{{color:var(--accent)}}
.stat-l{{font-size:10px;color:var(--muted);margin-top:2px}}
.toolbar{{max-width:1400px;margin:0 auto;padding:16px 24px;display:flex;align-items:center;gap:10px}}
.search-wrap{{flex:1;position:relative}}
.search-wrap svg{{position:absolute;left:10px;top:50%;transform:translateY(-50%);opacity:.4;pointer-events:none}}
#filter-input{{width:100%;background:var(--surf);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:13px;padding:8px 12px 8px 34px;outline:none;transition:border-color .15s}}
#filter-input:focus{{border-color:var(--accent)}}
#filter-input::placeholder{{color:var(--muted)}}
.counter{{font-size:12px;color:var(--muted);white-space:nowrap;min-width:60px;text-align:right}}
.view-btns{{display:flex;gap:4px}}
.vbtn{{background:var(--surf);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--muted);padding:7px 12px;cursor:pointer;font-size:12px;font-weight:600;transition:all .15s;display:flex;align-items:center;gap:5px}}
.vbtn.on{{background:var(--accent);border-color:var(--accent);color:#07101f}}
.container{{max-width:1400px;margin:0 auto;padding:0 24px 32px}}
#grid-view{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.card{{background:var(--surf);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;transition:border-color .15s,transform .1s;cursor:pointer}}
.card:hover{{border-color:#444;transform:translateY(-2px)}}
.card-thumb{{position:relative;aspect-ratio:16/9;overflow:hidden;background:#111}}
.card-thumb img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform .3s}}
.card:hover .card-thumb img{{transform:scale(1.04)}}
.num-badge{{position:absolute;top:7px;left:7px;background:rgba(0,0,0,.75);color:#fff;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;backdrop-filter:blur(4px)}}
.card-body{{padding:10px 12px 12px}}
.card-title{{font-size:12px;font-weight:500;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin-bottom:6px}}
.card-date{{font-size:11px;color:var(--muted)}}
#list-view{{display:none;flex-direction:column;gap:4px}}
.row{{display:flex;align-items:center;gap:12px;background:var(--surf);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 14px;transition:border-color .12s;cursor:pointer}}
.row:hover{{border-color:#444}}
.row-num{{font-size:12px;color:var(--muted);width:32px;text-align:right;flex-shrink:0}}
.row-thumb{{width:96px;height:54px;border-radius:4px;overflow:hidden;flex-shrink:0;background:#111}}
.row-thumb img{{width:100%;height:100%;object-fit:cover;display:block}}
.row-title{{flex:1;font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.row-date{{font-size:12px;color:var(--muted);flex-shrink:0;white-space:nowrap}}
#empty{{display:none;text-align:center;padding:60px 0;color:var(--muted);font-size:14px}}
footer{{text-align:center;color:var(--muted);font-size:11px;padding:20px 0 32px;border-top:1px solid var(--border)}}
footer strong{{color:var(--accent)}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <div class="logo"><svg viewBox="0 0 52 52" xmlns="http://www.w3.org/2000/svg"><rect width="52" height="52" rx="11" fill="#0d1a2e"/><text x="4" y="36" font-family="Arial Black, Arial, sans-serif" font-weight="900" font-size="30" fill="#e8f0f8">B</text><text x="26" y="36" font-family="Arial Black, Arial, sans-serif" font-weight="900" font-size="30" fill="#00c8d7">O</text><line x1="4" y1="44" x2="48" y2="44" stroke="#00c8d7" stroke-width="3" stroke-linecap="round"/></svg></div>
    <div class="brand-info">
      <div class="brand-name">{channel_label}</div>
      <div class="brand-sub">{titre_page} &nbsp;·&nbsp; {t['html_sorted']}</div>
    </div>
  </div>
  <div class="stats">
    <div class="stat"><div class="stat-n accent" id="stat-count">{len(videos)}</div><div class="stat-l">{t['html_videos']}</div></div>
    <div class="stat"><div class="stat-n">{year_first}</div><div class="stat-l">{lbl_first}</div></div>
    <div class="stat"><div class="stat-n">{year_last}</div><div class="stat-l">{lbl_last}</div></div>
  </div>
</div>
<div class="toolbar">
  <div class="search-wrap">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input id="filter-input" type="search" placeholder="{lbl_filter}" autocomplete="off" spellcheck="false">
  </div>
  <span class="counter" id="counter">{len(videos)} {t['html_results']}</span>
  <div class="view-btns">
    <button class="vbtn on" id="btn-grid" onclick="setView('grid')">{lbl_grid}</button>
    <button class="vbtn" id="btn-list" onclick="setView('list')">{lbl_list}</button>
  </div>
</div>
<div class="container">
  <div id="grid-view"></div>
  <div id="list-view"></div>
  <div id="empty">{lbl_noresult}</div>
</div>
<footer>{t['html_footer']} &nbsp;·&nbsp; <strong>{len(videos)}</strong> {t['html_results']}</footer>
<script>
const DATA = {js_videos};
let currentView = 'grid';
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
function renderGrid(items){{
  document.getElementById('grid-view').innerHTML = items.map(v=>`
    <div class="card" onclick="window.open('${{v.url}}','_blank')">
      <div class="card-thumb"><img src="${{v.thumb}}" loading="lazy" alt=""><span class="num-badge">#${{v.n}}</span></div>
      <div class="card-body"><div class="card-title">${{esc(v.title)}}</div><div class="card-date">${{v.date}}</div></div>
    </div>`).join('');
}}
function renderList(items){{
  document.getElementById('list-view').innerHTML = items.map(v=>`
    <div class="row" onclick="window.open('${{v.url}}','_blank')">
      <span class="row-num">#${{v.n}}</span>
      <div class="row-thumb"><img src="${{v.thumb}}" loading="lazy" alt=""></div>
      <span class="row-title">${{esc(v.title)}}</span>
      <span class="row-date">${{v.date}}</span>
    </div>`).join('');
}}
function setView(v){{
  currentView=v;
  document.getElementById('grid-view').style.display=v==='grid'?'grid':'none';
  document.getElementById('list-view').style.display=v==='list'?'flex':'none';
  document.getElementById('btn-grid').className='vbtn'+(v==='grid'?' on':'');
  document.getElementById('btn-list').className='vbtn'+(v==='list'?' on':'');
  applyFilter();
}}
function applyFilter(){{
  const q=document.getElementById('filter-input').value.toLowerCase().trim();
  const items=q?DATA.filter(v=>v.title.toLowerCase().includes(q)):DATA;
  renderGrid(items);renderList(items);
  document.getElementById('counter').textContent=items.length+' {t['html_results']}';
  document.getElementById('empty').style.display=items.length?'none':'block';
  document.getElementById('grid-view').style.display=(currentView==='grid'&&items.length)?'grid':'none';
  document.getElementById('list-view').style.display=(currentView==='list'&&items.length)?'flex':'none';
}}
document.getElementById('filter-input').addEventListener('input',applyFilter);
applyFilter();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    log(t["html_saved"].format(path=output_path))


# ══════════════════════════════════════════════════════════════════════════════
#  PYWEBVIEW API  (the bridge between JS and Python)
# ══════════════════════════════════════════════════════════════════════════════

class ByOldestApi:
    """All methods callable from JavaScript via pywebview.api.*"""

    def __init__(self):
        self._cfg     = load_config()
        self._lang    = self._cfg.get("lang", "en")
        self._running = False
        self._last_output_path = None
        self._window  = None   # set after window creation

    def _t(self, key: str, **kwargs) -> str:
        s = STRINGS[self._lang].get(key, key)
        return s.format(**kwargs) if kwargs else s

    def _js(self, js: str):
        """Evaluate JS in the webview (thread-safe)."""
        if self._window:
            self._window.evaluate_js(js)

    def _log(self, msg: str):
        # json.dumps produces a quoted, fully-escaped JS string literal —
        # immune to template-literal injection (${...}, backticks, backslashes…)
        self._js(f"appendLog({json.dumps(msg)})")

    def _set_progress(self, value: float, done: int = 0, total: int = 0):
        label = ""
        if total > 0:
            label = self._t("progress_label", done=done, total=total)
        elif value > 0:
            label = self._t("progress_fetching")
        pct = int(value * 100)
        self._js(f"setProgress({pct}, {json.dumps(label)})")

    # ── Called by JS on startup ───────────────────────────────────────────────
    def get_initial_state(self):
        """Return config + translations for the current language."""
        t = STRINGS[self._lang]
        history = self._cfg.get("channel_history", [])
        return {
            "lang":        self._lang,
            "api_key":     self._cfg.get("api_key", ""),
            "mode":        self._cfg.get("mode", "standard"),
            "strings":     t,
            "history":     history,
            "version":     APP_VERSION,
        }

    def toggle_lang(self):
        self._lang = "en" if self._lang == "fr" else "fr"
        self._cfg["lang"] = self._lang
        save_config(self._cfg)
        # Return fresh state so JS can re-render everything
        return self.get_initial_state()

    def save_settings(self, api_key: str, mode: str):
        self._cfg["api_key"]    = api_key.strip()
        self._cfg["mode"]       = mode
        save_config(self._cfg)
        return True

    def get_history(self):
        return self._cfg.get("channel_history", [])

    def clear_history(self):
        self._cfg["channel_history"] = []
        save_config(self._cfg)
        return []

    def delete_history_entry(self, channel: str, query: str):
        hist = self._cfg.get("channel_history", [])
        self._cfg["channel_history"] = [
            e for e in hist
            if not (isinstance(e, dict) and e.get("channel") == channel and e.get("query") == query)
        ]
        save_config(self._cfg)
        return self._cfg["channel_history"]

    def open_html_file(self, html_path: str):
        if html_path and os.path.exists(html_path):
            import webbrowser
            webbrowser.open(os.path.abspath(html_path))
            return True
        return False

    def copy_to_clipboard(self, text: str):
        try:
            import subprocess
            # Try pyperclip first, fall back to platform methods
            try:
                import pyperclip
                pyperclip.copy(text)
                return True
            except ImportError:
                pass
            if sys.platform == "win32":
                subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
            return True
        except Exception:
            return False

    # ── Main search ───────────────────────────────────────────────────────────
    def start_search(self, api_key: str, channel: str, query: str, mode: str):
        if self._running:
            return False

        api_key = api_key.strip()
        channel = channel.strip()
        query   = query.strip()

        if not api_key:
            self._log(self._t("err_no_api"))
            return False
        if not channel:
            self._log(self._t("err_no_channel"))
            return False
        if mode not in ("standard", "precise"):
            mode = "standard"

        self._cfg["api_key"] = api_key
        self._cfg["mode"]    = mode
        save_config(self._cfg)

        self._running = True
        self._js("setRunning(true)")
        self._js("clearLog()")
        self._set_progress(0)

        threading.Thread(
            target=self._run_search,
            args=(api_key, channel, query, mode),
            daemon=True
        ).start()
        return True

    def _run_search(self, api_key, channel, query, mode):
        t     = STRINGS[self._lang]
        quota = [0]

        def on_progress(v, done=0, total=0):
            self._set_progress(v, done, total)
            self._js(f"setQuota({quota[0]})")

        try:
            channel_id, channel_name = resolve_and_validate_channel(
                api_key, channel, self._log, t, quota
            )
            self._js(f"setQuota({quota[0]})")

            self._log(t["searching"].format(
                query=query or t["searching_all"], channel_id=channel_id
            ))

            if mode == "precise":
                videos = fetch_videos(api_key, channel_id, query, self._log, t, quota, on_progress)
            else:
                videos = fetch_videos_efficient(api_key, channel_id, query, self._log, t, quota, on_progress)

            self._js(f"setQuota({quota[0]})")
            self._log(t["found"].format(n=len(videos)))

            if not videos:
                self._log(t["diagnosing"])
                diagnose_empty_results(api_key, channel_id, query, self._log, t)
                return

            output = make_output_path(channel_name, query)
            generate_html(videos, query, channel_id, output, self._log, t)
            self._log(t["done"])

            add_to_history(self._cfg, channel, query, mode, output)
            self._cfg["api_key"] = api_key
            save_config(self._cfg)

            self._last_output_path = output

            # Pass result to JS — json.dumps ensures safe string serialization
            self._js(f"onSearchDone({json.dumps(output)}, {json.dumps(channel_name)}, {len(videos)})")

            _notify(t["notif_title"], t["notif_done"].format(n=len(videos), channel=channel_name))

            if self._cfg.get("open_after", True):
                try:
                    import webbrowser
                    webbrowser.open(os.path.abspath(output))
                except Exception:
                    pass

        except Exception as e:
            self._log(t["err_generic"].format(e=e))
        finally:
            self._js(f"setQuota({quota[0]})")
            self._set_progress(1.0)
            self._running = False
            self._js("setRunning(false)")


# ══════════════════════════════════════════════════════════════════════════════
#  HTML UI
# ══════════════════════════════════════════════════════════════════════════════

UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ByOldest</title>
<style>
  :root {
    --bg:      #07101f;
    --surf:    #0d1a2e;
    --surf2:   #102038;
    --border:  #1a2d45;
    --accent:  #00c8d7;
    --accent2: #00e5b0;
    --red:     #ff3a3a;
    --text:    #e8f0f8;
    --muted:   #4a6a80;
    --input:   #060f1c;
    --radius:  10px;
    --font-ui:   'Segoe UI', system-ui, sans-serif;
    --font-mono: 'Consolas', 'Courier New', monospace;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font-ui);
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    -webkit-app-region: no-drag;
    user-select: none;
  }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 99px; }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    padding: 14px 20px;
    background: var(--surf);
    border-bottom: 1px solid var(--border);
    gap: 12px;
    flex-shrink: 0;
  }

  .logo-mark {
    width: 52px; height: 52px;
    flex-shrink: 0;
  }
  .logo-mark svg {
    width: 52px; height: 52px;
    display: block;
  }

  .header-text { flex: 1; min-width: 0; }
  .header-title { font-size: 16px; font-weight: 800; letter-spacing: -0.5px; line-height: 1; }
  .header-sub   { font-size: 11px; color: var(--muted); margin-top: 2px; font-family: var(--font-mono); font-weight: 400; }

  .header-actions { display: flex; gap: 8px; align-items: center; }

  .btn-icon {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--muted);
    padding: 6px 10px;
    font-size: 12px;
    font-family: var(--font-ui);
    font-weight: 600;
    cursor: pointer;
    transition: all .15s;
    display: flex; align-items: center; gap: 5px;
    white-space: nowrap;
  }
  .btn-icon:hover { color: var(--text); border-color: var(--accent); }

  .quota-badge {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
    padding: 4px 10px;
    background: var(--input);
    border: 1px solid var(--border);
    border-radius: 6px;
    white-space: nowrap;
  }

  /* ── Tab bar ── */
  .tabs {
    display: flex;
    background: var(--surf);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    padding: 0 20px;
    gap: 4px;
  }

  .tab {
    padding: 10px 16px 9px;
    font-size: 12px;
    font-weight: 700;
    color: var(--muted);
    cursor: pointer;
    border: none;
    background: transparent;
    border-bottom: 2px solid transparent;
    transition: all .15s;
    font-family: var(--font-ui);
    letter-spacing: 0.3px;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ── Main layout ── */
  .main {
    flex: 1;
    display: flex;
    overflow: hidden;
  }

  /* ── Search panel ── */
  .search-panel {
    width: 340px;
    flex-shrink: 0;
    background: var(--surf);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .search-panel-top {
    flex-shrink: 0;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .panel-section {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
  }

  .field-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 7px;
    font-family: var(--font-mono);
  }

  .input-wrap {
    position: relative;
  }

  input[type="text"], input[type="password"] {
    width: 100%;
    background: var(--input);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 13px;
    font-family: var(--font-mono);
    padding: 9px 12px;
    outline: none;
    transition: border-color .15s;
    -webkit-appearance: none;
  }
  input[type="text"]:focus, input[type="password"]:focus {
    border-color: var(--accent);
  }
  input::placeholder { color: var(--muted); }

  .eye-btn {
    position: absolute;
    right: 10px; top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    padding: 2px;
    font-size: 14px;
    line-height: 1;
    transition: color .15s;
  }
  .eye-btn:hover { color: var(--text); }

  /* ── Mode selector ── */
  .mode-selector {
    display: flex;
    gap: 6px;
  }
  .mode-btn {
    flex: 1;
    padding: 8px 6px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--input);
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-ui);
    cursor: pointer;
    text-align: center;
    transition: all .15s;
    line-height: 1.3;
  }
  .mode-btn .mode-name { display: block; font-size: 12px; margin-bottom: 2px; }
  .mode-btn .mode-desc { display: block; font-size: 10px; font-weight: 400; font-family: var(--font-mono); opacity: .6; }
  .mode-btn:hover { border-color: #2a4060; color: var(--text); }
  .mode-btn.active { border-color: var(--accent); background: rgba(0,200,215,.08); color: var(--accent); }
  .mode-btn.active .mode-desc { opacity: 1; }

  /* ── Toggle ── */
  .toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .toggle-label { font-size: 13px; font-weight: 600; }
  .toggle {
    position: relative;
    width: 36px; height: 20px;
    flex-shrink: 0;
  }
  .toggle input { opacity: 0; width: 0; height: 0; }
  .toggle-slider {
    position: absolute;
    inset: 0;
    background: var(--border);
    border-radius: 20px;
    cursor: pointer;
    transition: background .2s;
  }
  .toggle-slider::before {
    content: '';
    position: absolute;
    width: 14px; height: 14px;
    left: 3px; top: 3px;
    background: white;
    border-radius: 50%;
    transition: transform .2s;
  }
  input:checked + .toggle-slider { background: var(--accent); }
  input:checked + .toggle-slider::before { transform: translateX(16px); }

  /* ── Run button ── */
  .run-btn {
    margin: 16px 20px;
    padding: 13px;
    background: var(--accent);
    border: none;
    border-radius: 10px;
    color: var(--bg);
    font-size: 14px;
    font-weight: 800;
    font-family: var(--font-ui);
    cursor: pointer;
    letter-spacing: 0.2px;
    transition: all .15s;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .run-btn:hover:not(:disabled) {
    background: #00e0ef;
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(0,200,215,.3);
  }
  .run-btn:disabled {
    background: var(--surf2);
    color: var(--muted);
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
  }
  .run-btn .spinner {
    width: 14px; height: 14px;
    border: 2px solid rgba(0,0,0,.3);
    border-top-color: var(--bg);
    border-radius: 50%;
    animation: spin .7s linear infinite;
    display: none;
  }
  .run-btn.loading .spinner { display: block; }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Progress ── */
  .progress-wrap {
    padding: 0 20px 16px;
  }
  .progress-track {
    height: 4px;
    background: var(--border);
    border-radius: 99px;
    overflow: hidden;
    margin-bottom: 6px;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    border-radius: 99px;
    width: 0%;
    transition: width .3s ease;
  }
  .progress-label {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--muted);
    text-align: right;
  }

  /* ── Log ── */
  .log-area {
    height: 100%;
    padding: 16px 20px;
    overflow-y: auto;
    font-family: var(--font-mono);
  }
  .log-line {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.8;
    color: var(--muted);
    word-break: break-all;
  }
  .log-line.success { color: #3ddd8a; }
  .log-line.error   { color: var(--red); }
  .log-line.info    { color: var(--text); }

  /* ── Result banner ── */
  .result-banner {
    margin: 0 20px 16px;
    padding: 12px 14px;
    background: rgba(0,200,215,.07);
    border: 1px solid rgba(0,200,215,.2);
    border-radius: 10px;
    display: none;
  }
  .result-banner.visible { display: block; }
  .result-banner-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 8px;
  }
  .result-banner-actions { display: flex; gap: 6px; }
  .result-btn {
    flex: 1;
    padding: 7px 10px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: var(--surf2);
    color: var(--text);
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-ui);
    cursor: pointer;
    transition: all .15s;
    text-align: center;
  }
  .result-btn:hover { border-color: var(--accent); color: var(--accent); }
  .result-btn.primary {
    background: var(--accent);
    color: var(--bg);
    border-color: var(--accent);
  }
  .result-btn.primary:hover { background: #00e0ef; }

  /* ── Content area (right) ── */
  .content-area {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  /* ── Tab panels ── */
  .tab-panel {
    display: none;
    flex: 1;
    overflow-y: auto;
    padding: 20px;
  }
  .tab-panel.active { display: flex; flex-direction: column; }
  #panel-search.active { padding: 0; }

  /* ── History ── */
  .history-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  .history-title { font-size: 14px; font-weight: 800; }
  .history-clear {
    font-size: 11px;
    color: var(--muted);
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
    cursor: pointer;
    font-family: var(--font-ui);
    font-weight: 600;
    transition: all .15s;
  }
  .history-clear:hover { color: var(--red); border-color: var(--red); }

  .history-list { display: flex; flex-direction: column; gap: 8px; }

  .history-card {
    background: var(--surf);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    transition: border-color .15s;
  }
  .history-card:hover { border-color: #2a4060; }

  .hcard-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }
  .hcard-channel {
    font-size: 13px;
    font-weight: 700;
    color: var(--text);
    word-break: break-all;
  }
  .hcard-meta {
    display: flex;
    gap: 5px;
    flex-shrink: 0;
    align-items: center;
  }
  .hcard-badge {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 2px 7px;
    border-radius: 4px;
    background: var(--surf2);
    border: 1px solid var(--border);
    color: var(--muted);
  }
  .hcard-badge.precise { border-color: rgba(0,200,215,.3); color: var(--accent); }
  .hcard-query {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--font-mono);
    margin-bottom: 10px;
  }
  .hcard-actions { display: flex; gap: 6px; }
  .hcard-btn {
    padding: 5px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surf2);
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-ui);
    cursor: pointer;
    transition: all .15s;
  }
  .hcard-btn:hover { color: var(--text); border-color: #2a4060; }
  .hcard-btn.view { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .hcard-btn.view:hover { background: #00e0ef; }
  .hcard-btn.del { margin-left: auto; }
  .hcard-btn.del:hover { color: var(--red); border-color: var(--red); }

  .history-empty {
    text-align: center;
    padding: 60px 20px;
    color: var(--muted);
    font-size: 13px;
  }
  .history-empty .empty-icon { font-size: 32px; margin-bottom: 10px; }

  /* ── Settings panel ── */
  .settings-panel {
    padding: 20px;
    max-width: 480px;
  }
  .settings-title { font-size: 14px; font-weight: 800; margin-bottom: 16px; }

  /* ── Responsive: hide right panel on narrow ── */
  @media (max-width: 700px) {
    .search-panel { width: 100%; border-right: none; }
    .content-area { display: none; }
  }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo-mark">
    <svg viewBox="0 0 52 52" xmlns="http://www.w3.org/2000/svg">
      <rect width="52" height="52" rx="11" fill="#0d1a2e"/>
      <text x="4" y="36" font-family="Arial Black, Arial, sans-serif" font-weight="900" font-size="30" fill="#e8f0f8">B</text>
      <text x="26" y="36" font-family="Arial Black, Arial, sans-serif" font-weight="900" font-size="30" fill="#00c8d7">O</text>
      <line x1="4" y1="44" x2="48" y2="44" stroke="#00c8d7" stroke-width="3" stroke-linecap="round"/>
    </svg>
  </div>
  <div class="header-text">
    <div class="header-title">ByOldest</div>
    <div class="header-sub" id="header-sub">Sort a channel's videos — oldest to newest</div>
  </div>
  <div class="header-actions">
    <div class="quota-badge" id="quota-badge">Quota: 0</div>
    <button class="btn-icon" onclick="toggleLang()" id="lang-btn">🌐 Français</button>
  </div>
</div>

<!-- Tabs -->
<div class="tabs">
  <button class="tab active" onclick="switchTab('search')"   id="tab-search">Search</button>
  <button class="tab"        onclick="switchTab('history')"  id="tab-history">History</button>
</div>

<!-- Main -->
<div class="main">

  <!-- Left: search controls -->
  <div class="search-panel">

    <div class="search-panel-top">

    <div class="panel-section">
      <div class="field-label" id="lbl-api">API KEY</div>
      <div class="input-wrap">
        <input type="password" id="api-input" placeholder="AIza…" autocomplete="off" spellcheck="false">
        <button class="eye-btn" onclick="toggleEye()" id="eye-btn" title="Show/hide">👁</button>
      </div>
    </div>

    <div class="panel-section">
      <div class="field-label" id="lbl-channel">CHANNEL</div>
      <input type="text" id="channel-input" placeholder="UCxxxxx or https://youtube.com/@Name" autocomplete="off" spellcheck="false">
    </div>

    <div class="panel-section">
      <div class="field-label" id="lbl-query">KEYWORD</div>
      <input type="text" id="query-input" placeholder="e.g. movie  (empty = all videos)" autocomplete="off" spellcheck="false">
    </div>

    <div class="panel-section">
      <div class="field-label" id="lbl-mode">MODE</div>
      <div class="mode-selector">
        <button class="mode-btn active" id="btn-standard" onclick="setMode('standard')">
          <span class="mode-name">Standard</span>
          <span class="mode-desc">~1 unit/page</span>
        </button>
        <button class="mode-btn" id="btn-precise" onclick="setMode('precise')">
          <span class="mode-name">Precise ⚠️</span>
          <span class="mode-desc">100 units/page</span>
        </button>
      </div>
    </div>

    <button class="run-btn" id="run-btn" onclick="runSearch()">
      <div class="spinner"></div>
      <span id="run-label">▶  Run search</span>
    </button>

    <!-- Progress -->
    <div class="progress-wrap">
      <div class="progress-track">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <div class="progress-label" id="progress-label"></div>
    </div>

    <!-- Result banner -->
    <div class="result-banner" id="result-banner">
      <div class="result-banner-title" id="result-banner-title">🎉 Done!</div>
      <div class="result-banner-actions">
        <button class="result-btn primary" id="btn-open-html" onclick="openResult()">Open HTML</button>
        <button class="result-btn" id="btn-copy-path" onclick="copyPath()">Copy path</button>
      </div>
    </div>

    </div><!-- end search-panel-top -->

  </div>

  <!-- Right: content -->
  <div class="content-area">

    <div class="tab-panel active" id="panel-search">
      <div class="log-area" id="log-area"></div>
    </div>

    <div class="tab-panel" id="panel-history">
      <div class="history-header">
        <div class="history-title" id="lbl-history-title">Search History</div>
        <button class="history-clear" onclick="clearHistory()" id="btn-clear-all">Clear all</button>
      </div>
      <div class="history-list" id="history-list"></div>
    </div>

  </div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let S = {};  // strings
let currentMode = 'standard';
let lastOutputPath = '';
let currentTab = 'search';

// ── Init ──────────────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', async () => {
  const state = await pywebview.api.get_initial_state();
  applyState(state);
});

function applyState(state) {
  S = state.strings;
  currentMode = state.mode;

  document.getElementById('api-input').value     = state.api_key || '';

  setMode(state.mode, false);
  applyStrings(S, state.lang);
  renderHistory(state.history);
}

function applyStrings(t, lang) {
  document.getElementById('header-sub').textContent     = t.app_subtitle;
  document.getElementById('lang-btn').textContent       = t.lang_btn;
  document.getElementById('lbl-api').textContent        = t.api_label     || 'API KEY';
  document.getElementById('lbl-channel').textContent    = t.channel_label || 'CHANNEL';
  document.getElementById('lbl-query').textContent      = t.query_label   || 'KEYWORD';
  document.getElementById('lbl-mode').textContent       = t.mode_label    || 'MODE';
  document.getElementById('run-label').textContent      = '▶  ' + t.btn_run;
  document.getElementById('tab-search').textContent     = t.tab_search;
  document.getElementById('tab-history').textContent    = t.tab_history;
  document.getElementById('lbl-history-title').textContent = t.history_title;
  document.getElementById('btn-clear-all').textContent  = t.history_clear_all;
  document.getElementById('btn-open-html').textContent  = t.open_html || 'Open HTML';
  document.getElementById('btn-copy-path').textContent  = t.copy_path || 'Copy path';
  document.getElementById('channel-input').placeholder  = t.ph_channel;
  document.getElementById('query-input').placeholder    = t.ph_query + '  (' + (t.label_query || '') + ')';
  document.querySelector('#btn-standard .mode-name').textContent = 'Standard';
  document.querySelector('#btn-precise .mode-name').textContent  = 'Precise ⚠️';
  document.lang = lang;
}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'history') refreshHistory();
}

// ── Language ───────────────────────────────────────────────────────────────
async function toggleLang() {
  const state = await pywebview.api.toggle_lang();
  applyState(state);
}

// ── Mode ──────────────────────────────────────────────────────────────────
function setMode(mode, save = true) {
  currentMode = mode;
  document.getElementById('btn-standard').classList.toggle('active', mode === 'standard');
  document.getElementById('btn-precise').classList.toggle('active',  mode === 'precise');
  if (save) saveSettings();
}

// ── Settings autosave ──────────────────────────────────────────────────────
function saveSettings() {
  const api = document.getElementById('api-input').value;
  pywebview.api.save_settings(api, currentMode);
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('api-input').addEventListener('blur', saveSettings);
});

// ── Eye toggle ────────────────────────────────────────────────────────────
function toggleEye() {
  const el = document.getElementById('api-input');
  el.type = el.type === 'password' ? 'text' : 'password';
}

// ── Log ───────────────────────────────────────────────────────────────────
function clearLog() {
  document.getElementById('log-area').innerHTML = '';
}

function appendLog(msg) {
  const area = document.getElementById('log-area');
  const line = document.createElement('div');
  line.className = 'log-line';
  if (msg.startsWith('✅') || msg.startsWith('🎉')) line.classList.add('success');
  else if (msg.startsWith('❌')) line.classList.add('error');
  else if (msg.startsWith('🔍') || msg.startsWith('🔎') || msg.startsWith('⚠️')) line.classList.add('info');
  line.textContent = msg;
  area.appendChild(line);
  area.scrollTop = area.scrollHeight;
}

// ── Progress ──────────────────────────────────────────────────────────────
function setProgress(pct, label) {
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-label').textContent = label || '';
}

// ── Quota ─────────────────────────────────────────────────────────────────
function setQuota(n) {
  const t = S.quota_used || 'Quota: ~{n} units';
  document.getElementById('quota-badge').textContent = t.replace('{n}', n);
}

// ── Running state ─────────────────────────────────────────────────────────
function setRunning(running) {
  const btn = document.getElementById('run-btn');
  const lbl = document.getElementById('run-label');
  btn.disabled = running;
  btn.classList.toggle('loading', running);
  lbl.textContent = running ? (S.btn_running || 'Searching…') : ('▶  ' + (S.btn_run || 'Run search'));
  if (!running) {
    document.getElementById('result-banner').classList.remove('visible');
  }
}

// ── Search done callback ──────────────────────────────────────────────────
function onSearchDone(path, channelName, count) {
  lastOutputPath = path;
  const banner = document.getElementById('result-banner');
  const title  = document.getElementById('result-banner-title');
  title.textContent = '🎉 ' + count + ' video' + (count > 1 ? 's' : '') + ' — ' + channelName;
  banner.classList.add('visible');
  refreshHistory();
  if (currentTab === 'history') renderHistoryFromApi();
}

async function openResult() {
  if (lastOutputPath) await pywebview.api.open_html_file(lastOutputPath);
}

async function copyPath() {
  if (lastOutputPath) {
    await pywebview.api.copy_to_clipboard(lastOutputPath);
    const btn = document.getElementById('btn-copy-path');
    const orig = btn.textContent;
    btn.textContent = S.copied || '✅ Copied!';
    setTimeout(() => btn.textContent = orig, 2000);
  }
}

// ── Run search ────────────────────────────────────────────────────────────
async function runSearch() {
  const api     = document.getElementById('api-input').value.trim();
  const channel = document.getElementById('channel-input').value.trim();
  const query   = document.getElementById('query-input').value.trim();

  document.getElementById('result-banner').classList.remove('visible');
  saveSettings();

  await pywebview.api.start_search(api, channel, query, currentMode);
}

// Enter key on inputs triggers search
['channel-input', 'query-input'].forEach(id => {
  document.getElementById(id)?.addEventListener('keydown', e => {
    if (e.key === 'Enter') runSearch();
  });
});

// ── History ───────────────────────────────────────────────────────────────
async function refreshHistory() {
  const hist = await pywebview.api.get_history();
  renderHistory(hist);
}

function renderHistory(hist) {
  const list = document.getElementById('history-list');
  if (!hist || !hist.length) {
    const emptyMsg = S.history_empty || 'No history yet.';
    list.innerHTML = `<div class="history-empty"><div class="empty-icon">🕘</div>${emptyMsg}</div>`;
    return;
  }
  list.innerHTML = hist.map(e => {
    const ch    = typeof e === 'string' ? e : (e.channel || '');
    const q     = typeof e === 'object' ? (e.query || '') : '';
    const mode  = typeof e === 'object' ? (e.mode  || 'standard') : 'standard';
    const path  = typeof e === 'object' ? (e.html_path || '') : '';
    const isPrecise = mode === 'precise';
    const queryStr  = q ? q : (S.label_query || 'all videos');
    const modeBadge = isPrecise
      ? `<span class="hcard-badge precise">Precise</span>`
      : `<span class="hcard-badge">Standard</span>`;
    const viewBtn = path
      ? `<button class="hcard-btn view" onclick='openHistoryHtml(${JSON.stringify(path)})'>${S.history_open_html || 'View'}</button>`
      : '';
    return `
      <div class="history-card">
        <div class="hcard-top">
          <div class="hcard-channel">${esc(ch)}</div>
          <div class="hcard-meta">${modeBadge}</div>
        </div>
        <div class="hcard-query">${esc(queryStr)}</div>
        <div class="hcard-actions">
          ${viewBtn}
          <button class="hcard-btn" onclick='rerun(${JSON.stringify(ch)}, ${JSON.stringify(q)}, ${JSON.stringify(mode)})'>${S.history_rerun || 'Re-run'}</button>
          <button class="hcard-btn del" onclick='deleteEntry(${JSON.stringify(ch)}, ${JSON.stringify(q)})'>✕</button>
        </div>
      </div>`;
  }).join('');
}

async function renderHistoryFromApi() {
  const hist = await pywebview.api.get_history();
  renderHistory(hist);
}

async function openHistoryHtml(path) {
  const ok = await pywebview.api.open_html_file(path);
  if (!ok) appendLog(S.history_no_html || 'HTML file not found.');
}

async function deleteEntry(channel, query) {
  const hist = await pywebview.api.delete_history_entry(channel, query);
  renderHistory(hist);
}

async function clearHistory() {
  const hist = await pywebview.api.clear_history();
  renderHistory(hist);
}

function rerun(channel, query, mode) {
  document.getElementById('channel-input').value = channel;
  document.getElementById('query-input').value   = query;
  setMode(mode);
  switchTab('search');
  runSearch();
}

// ── Utils ─────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    api = ByOldestApi()

    window = webview.create_window(
        title      = f"ByOldest  v{APP_VERSION}",
        html       = UI_HTML,
        js_api     = api,
        width      = 980,
        height     = 700,
        min_size   = (640, 560),
        background_color = "#07101f",
    )
    api._window = window

    webview.start(debug=False)
