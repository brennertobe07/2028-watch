"""
update_watch.py — collect the 2028 presidential field (Democrats first, GOP list
secondary), recent news mentions, and national primary polling; write
data/watch.json for the 2028 Watch dashboard.

Sources:
  * Roster     — Wikipedia "2028 {Democratic,Republican} Party presidential
                 primaries" (h4 headings under Candidates; declined list).
  * Polls      — the "Aggregator" table (270toWin / RCP / RacetotheWH ...) on the
                 Dem nationwide-polling page and the GOP primaries page; averaged.
  * News       — Google News RSS, one query per candidate for the last 7 days,
                 plus one Virginia query per Democrat for the last 30 days,
                 kept only when the headline names both the candidate and a VA
                 place/figure (config va_terms).
                 RSS caps at ~100 items per query, so counts are a relative
                 "buzz" measure, not an exact census.
                 Stories from right-leaning outlets (config right_media) are
                 tagged, and each candidate gets right_pct — the share of the
                 7-day count from those outlets. Nothing is filtered out.

Hand edits go in config.json (extra names, exclusions, search-name overrides);
the script never writes to it.

Week-over-week change comes from data/history.json (one 7-day count per
candidate per day), so the delta column fills in after ~7 days of runs.

Usage:
    python update_watch.py            # full run (~80 requests, ~2 min)
    python update_watch.py --no-news  # roster + polls only (fast test)
    python update_watch.py --publish  # full run, then commit + push data/
                                      # (what the daily scheduled task runs)
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import mean

import requests
from bs4 import BeautifulSoup

API = "https://en.wikipedia.org/w/api.php"
NEWS = "https://news.google.com/rss/search"
UA = {"User-Agent": "DPVA-2028-watch/1.0 (brenner.tobe@vademocrats.org)"}
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "watch.json"
HISTORY = ROOT / "data" / "history.json"
CONFIG = ROOT / "config.json"
SLEEP_WIKI = 0.4
SLEEP_NEWS = 1.0       # Google News throttles bursts
HEADLINES_KEPT = 15    # per candidate, newest first
VA_DAYS = 30
MIN_DEMS = 10          # --publish refuses a run with fewer Democrats than this
TODAY = date.today()

PAGES = {
    "dem": {"roster": "2028 Democratic Party presidential primaries",
            "polls": "Nationwide opinion polling for the 2028 Democratic Party presidential primaries"},
    "gop": {"roster": "2028 Republican Party presidential primaries",
            "polls": "2028 Republican Party presidential primaries"},
}
# Wikipedia h3 section title -> status label shown on the page
STATUS = {"declared": "Declared", "formed exploratory committee": "Exploratory",
          "expressed interest": "Interested", "speculated by the media": "Speculated"}
STATUS_ORDER = ["Declared", "Exploratory", "Interested", "Speculated", "Polled", "Added"]

warnings = []
session = requests.Session()
session.headers.update(UA)


# ---------------------------------------------------------------- helpers

def clean(text):
    """Strip footnote markers like [ 12 ] / [a] and normalize whitespace/dashes."""
    text = re.sub(r"\[\s*[^\]]{1,20}\s*\]", "", text)
    text = text.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def wiki(page):
    """Return parsed article HTML for a Wikipedia title, or None if missing."""
    time.sleep(SLEEP_WIKI)
    try:
        j = session.get(API, params={"action": "parse", "page": page, "prop": "text",
                                     "format": "json", "formatversion": 2,
                                     "redirects": 1}, timeout=45).json()
    except Exception as e:
        warnings.append(f"wiki fetch failed: {page}: {e}")
        return None
    if "error" in j:
        warnings.append(f"wiki page missing: {page}")
        return None
    return BeautifulSoup(j["parse"]["text"], "html.parser")


def heading_blocks(soup):
    """Yield (level, title, wrapper_div) for every h2/h3/h4 heading, in order."""
    for div in soup.select("div.mw-heading"):
        h = div.find(["h2", "h3", "h4"])
        if h:
            yield int(h.name[1]), clean(h.get_text()), div


def first_sentence(text):
    # end at a period followed by a capitalized word, skipping "U.S." / "Jr." style abbreviations
    m = re.match(r"(.+?(?<![A-Z])(?<!\b[A-Z][a-z])\.)(\s+[A-Z(]|$)", text)
    s = (m.group(1) if m else text)
    return s[:260]


# ---------------------------------------------------------------- roster

def parse_roster(soup):
    """Candidates from the h4 headings under 'Candidates', tagged by h3 section.
    Returns (candidates, declined_names)."""
    cands, declined = [], []
    h2 = h3 = None
    for level, title, div in heading_blocks(soup):
        if level == 2:
            h2 = title.lower()
            h3 = None
            continue
        if h2 != "candidates":
            continue
        if level == 3:
            h3 = title.lower()
            if h3.startswith("declined"):
                # names are the first link of each <li> until the next heading
                for el in div.find_next_siblings():
                    if "mw-heading" in (el.get("class") or []):
                        break
                    for li in el.find_all("li") if el.name in ("ul", "div") else []:
                        a = li.find("a")
                        if a:
                            declined.append(clean(a.get_text()))
            continue
        if level == 4 and h3 in STATUS:
            bio = ""
            p = div.find_next_sibling()
            if p is not None and p.name == "p":
                bio = first_sentence(clean(p.get_text()))
            cands.append({"name": title, "status": STATUS[h3], "bio": bio})
    return cands, declined


# ---------------------------------------------------------------- polls

def pct(text):
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*%", text)
    return float(m.group(1)) if m else None


def parse_aggregates(soup):
    """Average each candidate across the aggregator rows of the first table whose
    header starts with 'Aggregator'. Returns ({name: avg}, [aggregator names], updated)."""
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = [clean(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
        if not head or head[0] != "Aggregator":
            continue
        skip = {"Aggregator", "Updated", "Other", "Lead", "Undecided", "Margin"}
        cols = {i: h for i, h in enumerate(head) if h not in skip}
        vals, aggs, updated = {}, [], None
        for r in rows[1:]:
            cells = [clean(c.get_text(" ")) for c in r.find_all(["th", "td"])]
            if len(cells) < len(head):
                continue
            aggs.append(cells[0])
            updated = updated or cells[1]
            for i, name in cols.items():
                v = pct(cells[i])
                if v is not None:
                    vals.setdefault(name, []).append(v)
        return ({n: round(mean(v), 1) for n, v in vals.items()}, aggs, updated)
    warnings.append("no Aggregator table found")
    return {}, [], None


# ---------------------------------------------------------------- news

def news(query):
    """Google News RSS search -> list of {title, source, url, date} (deduped by title)."""
    time.sleep(SLEEP_NEWS)
    try:
        r = session.get(NEWS, params={"q": query, "hl": "en-US", "gl": "US",
                                      "ceid": "US:en"}, timeout=30)
        r.raise_for_status()
        items = ET.fromstring(r.content).findall(".//item")
    except Exception as e:
        warnings.append(f"news fetch failed: {query}: {e}")
        return None
    out, seen = [], set()
    for it in items:
        title = it.findtext("title") or ""
        src = it.findtext("source") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        key = re.sub(r"\W+", "", title.lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            d = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
            iso = d.strftime("%Y-%m-%dT%H:%MZ")
        except Exception:
            iso = None
        out.append({"title": title, "source": src, "url": it.findtext("link"), "date": iso})
    out.sort(key=lambda a: a["date"] or "", reverse=True)
    return out


def search_name(c, overrides):
    """Quoted search term; config can supply an OR-list (e.g. AOC)."""
    alts = overrides.get(c["name"]) or [c["name"]]
    return "(" + " OR ".join(f'"{a}"' for a in alts) + ")"


def name_regex(c, overrides):
    """Headline matcher for a candidate: last name plus any configured aliases."""
    last = re.sub(r",?\s+(Jr|Sr|II|III)\.?$", "", c["name"]).split()[-1]
    alts = {last, *(overrides.get(c["name"]) or [])}
    return re.compile(r"\b(" + "|".join(map(re.escape, alts)) + r")\b", re.I)


def is_va(item, name_re, va_re):
    """Google matches the candidate and 'Virginia' anywhere in the article body, which
    is mostly noise. Keep only items whose headline names both the candidate and a
    Virginia place/figure. (A VA outlet alone isn't enough — local TV covers national
    stories too.)"""
    title = item["title"]
    return bool(name_re.search(title)) and bool(va_re.search(title.replace("West Virginia", "")))


def tag_right(items, right_media):
    """Mark stories from right-leaning outlets (config right_media, substring match on
    the source name). Tagging only — nothing is dropped."""
    for a in items or []:
        src = a["source"].lower()
        a["right"] = any(r in src for r in right_media)


def collect_news(c, overrides, with_va, va_re=None, right_media=()):
    term = search_name(c, overrides)
    wk = news(f'{term} (2028 OR presidential OR "White House bid" OR "run for president") when:7d')
    tag_right(wk, right_media)
    c["mentions_7d"] = len(wk) if wk is not None else None
    c["right_7d"] = sum(a["right"] for a in wk) if wk is not None else None
    c["right_pct"] = round(100 * c["right_7d"] / len(wk)) if wk else None
    c["at_cap"] = wk is not None and len(wk) >= 95
    c["headlines"] = (wk or [])[:HEADLINES_KEPT]
    if with_va:
        va = news(f"{term} Virginia when:{VA_DAYS}d")
        name_re = name_regex(c, overrides)
        va = [a for a in va if is_va(a, name_re, va_re)] if va is not None else None
        tag_right(va, right_media)
        c["va_30d"] = len(va) if va is not None else None
        c["va_headlines"] = (va or [])[:8]


# ---------------------------------------------------------------- history / delta

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def apply_history(parties):
    """Record today's 7-day counts; set delta vs. the entry closest to 7 days ago."""
    hist = load_json(HISTORY, {})
    today = TODAY.isoformat()
    hist[today] = {c["name"]: c["mentions_7d"] for p in parties.values()
                   for c in p["candidates"] if c.get("mentions_7d") is not None}
    target = TODAY - timedelta(days=7)
    past = [d for d in hist if date.fromisoformat(d) <= target]
    ref = hist[max(past)] if past else {}
    for p in parties.values():
        for c in p["candidates"]:
            prev = ref.get(c["name"])
            c["delta_wk"] = (c["mentions_7d"] - prev
                             if prev is not None and c.get("mentions_7d") is not None else None)
    cutoff = (TODAY - timedelta(days=120)).isoformat()
    hist = {d: v for d, v in sorted(hist.items()) if d >= cutoff}
    HISTORY.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return max(past) if past else None


# ---------------------------------------------------------------- main

def build_party(key, cfg, do_news):
    soup = wiki(PAGES[key]["roster"])
    cands, declined = parse_roster(soup) if soup else ([], [])
    psoup = wiki(PAGES[key]["polls"]) if PAGES[key]["polls"] != PAGES[key]["roster"] else soup
    polls, aggs, updated = parse_aggregates(psoup) if psoup else ({}, [], None)

    exclude = set(cfg.get("exclude", []))
    by_name = {c["name"]: c for c in cands}
    # anyone the aggregators poll but Wikipedia doesn't list as a candidate
    for name in polls:
        if name not in by_name and name not in declined:
            by_name[name] = {"name": name, "status": "Polled", "bio": ""}
    for extra in cfg.get(f"extra_{key}", []):
        by_name.setdefault(extra["name"], {"name": extra["name"], "status": "Added",
                                           "bio": extra.get("bio", "")})
    roster = [c for n, c in by_name.items() if n not in exclude]
    for c in roster:
        c["poll_avg"] = polls.get(c["name"])
        c["declined"] = c["name"] in declined

    va_re = re.compile(r"\b(" + "|".join(map(re.escape, cfg.get("va_terms", ["Virginia"])))
                       + r")\b", re.I)
    right_media = [r.lower() for r in cfg.get("right_media", [])]
    if do_news:
        for i, c in enumerate(roster, 1):
            print(f"  [{key} {i}/{len(roster)}] {c['name']}", flush=True)
            collect_news(c, cfg.get("search_names", {}), with_va=(key == "dem"), va_re=va_re,
                         right_media=right_media)

    roster.sort(key=lambda c: (-(c.get("mentions_7d") or 0), -(c.get("poll_avg") or 0),
                               STATUS_ORDER.index(c["status"])))
    for i, c in enumerate(roster, 1):
        c["rank"] = i
    return {"candidates": roster, "declined": declined,
            "poll_aggregators": aggs, "poll_updated": updated}


def main():
    do_news = "--no-news" not in sys.argv
    cfg = load_json(CONFIG, {})
    OUT.parent.mkdir(exist_ok=True)
    parties = {}
    for key in ("dem", "gop"):
        print(f"{key}: roster + polls", flush=True)
        parties[key] = build_party(key, cfg, do_news)
    ref_day = apply_history(parties) if do_news else None
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "delta_vs": ref_day,
        "parties": parties,
        "warnings": warnings,
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    d = parties["dem"]["candidates"]
    print(f"wrote {OUT}  dems={len(d)} gop={len(parties['gop']['candidates'])} "
          f"warnings={len(warnings)}")
    for w in warnings:
        print("  WARN", w)
    if "--publish" in sys.argv:
        sys.exit(publish(data, do_news))


def publish(data, do_news):
    """Commit data/watch.json + history.json and push (GitHub Pages serves them).
    Refuses thin runs so yesterday's data stays live. Returns exit code."""
    import subprocess
    dems = data["parties"]["dem"]["candidates"]
    newsed = sum(c.get("mentions_7d") is not None for c in dems)
    if not do_news or len(dems) < MIN_DEMS or newsed < len(dems) / 2:
        print(f"NOT publishing: {len(dems)} Democrats, {newsed} with news results "
              "— keeping yesterday's data live")
        return 1

    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)

    git("add", "data/watch.json", "data/history.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("No data changes to publish.")
        return 0
    c = git("commit", "-m", f"Daily 2028 refresh {TODAY.isoformat()}")
    p = git("push", "origin", "main")
    print(c.stdout.strip().splitlines()[0] if c.stdout else c.stderr.strip())
    print("Pushed." if p.returncode == 0 else f"PUSH FAILED: {p.stderr.strip()}")
    return p.returncode


if __name__ == "__main__":
    main()
