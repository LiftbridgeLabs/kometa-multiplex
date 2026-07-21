"""Export AURA's saved MediUX set selections into Kometa metadata files.

Reads AURA.db (the single curated AURA instance) and generates:
    metadata/mediux-auto-movies.yml   (matched by TMDb ID)
    metadata/mediux-auto-tv.yml       (matched by TVDb ID)

Kometa then applies the same art to every library on every server, so one
AURA instance is enough. Run on a schedule shortly before Kometa's daily run
(see the aura-kometa-sync service in kometa-compose.yml).

Usage:
    python aura-to-kometa.py --db /aura/AURA.db --out /config/metadata
    python aura-to-kometa.py --db ... --out ... --no-titlecards   # smaller files
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import defaultdict

ASSET_URL = "https://api.mediux.pro/assets/{}"


def load_tmdb_key(out_dir, explicit):
    """--tmdb-key beats TMDB_APIKEY env beats the .env next to /config."""
    if explicit:
        return explicit
    if os.environ.get("TMDB_APIKEY"):
        return os.environ["TMDB_APIKEY"]
    env_path = os.path.join(os.path.dirname(os.path.abspath(out_dir)), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("TMDB_APIKEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def tvdb_via_tmdb(tmdb_id, api_key, cache):
    """Fallback for shows whose file path has no {tvdb-NNN} tag (e.g. anime):
    ask TMDb's external_ids endpoint. Results are cached on disk so each show
    costs one API call ever."""
    k = str(tmdb_id)
    if k in cache:
        return cache[k]
    url = (f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids"
           f"?api_key={api_key}")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            v = json.load(r).get("tvdb_id")
    except Exception:
        return None
    if v:
        cache[k] = int(v)
        return int(v)
    return None


def q(cur, sql, args=()):
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(db_path, out_dir, titlecards=True, tmdb_key=None):
    tmdb_key = load_tmdb_key(out_dir, tmdb_key)
    cache_path = os.path.join(out_dir, ".tvdb-map.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            tvdb_cache = json.load(fh)
    except (OSError, ValueError):
        tvdb_cache = {}

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    saved = q(cur, """
        SELECT si.tmdb_id, si.poster_set_id, si.poster_selected,
               si.backdrop_selected, si.season_poster_selected,
               si.special_season_poster_selected, si.titlecard_selected,
               si.last_downloaded, mi.type, mi.title, mi.year, mi.id AS mi_id
        FROM SavedItems si
        JOIN MediaItems mi ON mi.tmdb_id = si.tmdb_id
                          AND mi.library_title = si.library_title
        ORDER BY si.last_downloaded""")

    # tvdb id per media item, parsed from the series folder name {tvdb-123}
    tvdb = {}
    for r in q(cur, "SELECT media_item_id, location FROM Series"):
        m = re.search(r"\{tvdb-(\d+)\}", r["location"] or "")
        if m:
            tvdb[r["media_item_id"]] = int(m.group(1))

    # merge selections per title; later saves win on conflicts (ORDER BY above)
    movies, shows, skipped = {}, {}, []
    for s in saved:
        imgs = q(cur, """
            SELECT image_id, image_type, image_season_number, image_episode_number
            FROM ImageFiles
            WHERE poster_set_id = ? AND item_tmdb_id = ?""",
            (s["poster_set_id"], s["tmdb_id"]))
        by_type = defaultdict(list)
        for i in imgs:
            by_type[i["image_type"]].append(i)

        if s["type"] == "movie":
            entry = movies.setdefault(int(s["tmdb_id"]), {"_title": s["title"]})
            if s["poster_selected"] and by_type["poster"]:
                entry["url_poster"] = ASSET_URL.format(by_type["poster"][0]["image_id"])
            if s["backdrop_selected"] and by_type["backdrop"]:
                entry["url_background"] = ASSET_URL.format(by_type["backdrop"][0]["image_id"])
            continue

        key = tvdb.get(s["mi_id"])
        if key is None and tmdb_key:
            key = tvdb_via_tmdb(s["tmdb_id"], tmdb_key, tvdb_cache)
        if key is None:
            skipped.append(f"{s['title']} ({s['year']}) tmdb:{s['tmdb_id']} — no tvdb id in path or on TMDb")
            continue
        entry = shows.setdefault(key, {"_title": s["title"], "seasons": {}})
        if s["poster_selected"] and by_type["poster"]:
            entry["url_poster"] = ASSET_URL.format(by_type["poster"][0]["image_id"])
        if s["backdrop_selected"] and by_type["backdrop"]:
            entry["url_background"] = ASSET_URL.format(by_type["backdrop"][0]["image_id"])
        for i in by_type["season_poster"]:
            n = i["image_season_number"]
            if n is None:
                continue
            want = s["special_season_poster_selected"] if n == 0 else s["season_poster_selected"]
            if want:
                entry["seasons"].setdefault(n, {})["url_poster"] = ASSET_URL.format(i["image_id"])
        if titlecards and s["titlecard_selected"]:
            for i in by_type["titlecard"]:
                sn, en = i["image_season_number"], i["image_episode_number"]
                if sn is None or en is None:
                    continue
                season = entry["seasons"].setdefault(sn, {})
                season.setdefault("episodes", {})[en] = {
                    "url_poster": ASSET_URL.format(i["image_id"])}
    con.close()

    def emit(fh, key, entry, is_show):
        fh.write(f"  {key}: # {entry.pop('_title')}\n")
        for attr in ("url_poster", "url_background"):
            if attr in entry:
                fh.write(f"    {attr}: {entry[attr]}\n")
        if is_show and entry.get("seasons"):
            fh.write("    seasons:\n")
            for sn in sorted(entry["seasons"]):
                season = entry["seasons"][sn]
                fh.write(f"      {sn}:\n")
                if "url_poster" in season:
                    fh.write(f"        url_poster: {season['url_poster']}\n")
                if season.get("episodes"):
                    fh.write("        episodes:\n")
                    for en in sorted(season["episodes"]):
                        fh.write(f"          {en}:\n")
                        fh.write(f"            url_poster: {season['episodes'][en]['url_poster']}\n")

    header = ("# AUTO-GENERATED by aura-to-kometa.py — do not edit by hand.\n"
              "# Regenerated on schedule from AURA.db (saved MediUX sets).\n"
              "metadata:\n")

    with open(f"{out_dir}/mediux-auto-movies.yml", "w", encoding="utf-8") as fh:
        fh.write(header)
        for key in sorted(movies):
            emit(fh, key, movies[key], False)
    with open(f"{out_dir}/mediux-auto-tv.yml", "w", encoding="utf-8") as fh:
        fh.write(header)
        for key in sorted(shows):
            emit(fh, key, shows[key], True)

    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(tvdb_cache, fh)
    except OSError:
        pass

    print(f"movies: {len(movies)}  shows: {len(shows)}  skipped: {len(skipped)}")
    for s in sorted(set(skipped)):
        print("  skipped:", s)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-titlecards", action="store_true")
    p.add_argument("--tmdb-key", help="TMDb API key for tvdb-id fallback "
                   "(default: TMDB_APIKEY env, then TMDB_APIKEY= in /config/.env)")
    a = p.parse_args()
    sys.exit(export(a.db, a.out, titlecards=not a.no_titlecards, tmdb_key=a.tmdb_key))
