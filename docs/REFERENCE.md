# 2028 Watch (2028-watch) — Reference

## 1. What it is

A daily view of who is positioning for 2028, built for an early-primary-state
party: which Democrats are getting coverage, where they stand in national
primary polling, and whether any of them are showing up in Virginia. A secondary
tab lists the Republican field.

Not a model. The roster and poll numbers republish what Wikipedia editors have
compiled; the news counts measure relative "buzz" from Google News.

- **Live:** https://brennertobe07.github.io/2028-watch/
- **Repo:** `brennertobe07/2028-watch` (public — only public news/polling data)
- **Audience:** internal for now, same as poll-tracker (decided 2026-09-28)
- **Cross-links:** header button to National Poll Tracker; poll-tracker links back here
- **Status (2026-09-28):** v1 accepted and parked. Possible next ideas, not started:
  Claude summary of each candidate's week, state-level (early-state) poll tables,
  candidate visit/event tracking.
- **Local preview:** `python -m http.server 8765` in the repo, open http://localhost:8765
  (opening index.html from disk fails — the page fetches `data/watch.json`)

## 2. File map

```
2028-watch/
├── CLAUDE.md             project index (short)
├── docs/REFERENCE.md     this file
├── update_watch.py       collector: Wikipedia + Google News → data/watch.json (+ --publish)
├── config.json           hand-edited: extra names, exclusions, search aliases, VA terms, right_media outlets
├── index.html            dashboard (single file, DPVA dark theme, no build step)
└── data/
    ├── watch.json        generated data (committed; dashboard reads it)
    ├── history.json      one 7-day news count per candidate per day, 120-day window (committed)
    └── last_run.log      scheduled-task output (gitignored)
```

## 3. Sources

| Data | Source | Rule |
|---|---|---|
| Roster + status | Wikipedia "2028 {Democratic,Republican} Party presidential primaries" | every h4 under the Candidates h2, status from its h3: Declared / Formed exploratory committee / Expressed interest / Speculated by the media |
| Declined list | same pages | first link in each `<li>` under "Declined to run" |
| Bio line | same pages | first sentence of the paragraph after each h4 |
| Dem poll average | "Nationwide opinion polling for the 2028 Democratic Party presidential primaries" | first table whose header starts "Aggregator"; mean across aggregator rows |
| GOP poll average | GOP primaries page | same "Aggregator" rule |
| News · 7d | Google News RSS | `("Name" OR alias) (2028 OR presidential OR "White House bid" OR "run for president") when:7d`, deduped by headline |
| R-media % | the News · 7d results | share of that candidate's 7-day stories whose source name contains a `right_media` entry (case-insensitive substring; list covers both outlet names and domains). Tagging only — nothing is dropped from counts or ranking |
| Virginia · 30d (Dems only) | Google News RSS | `("Name" OR alias) Virginia when:30d`, then kept **only if the headline names the candidate (last name or alias) AND a `va_terms` entry** ("West Virginia" ignored) |

Roster additions: anyone in the aggregator table who isn't on the Wikipedia list
(and hasn't declined) is added as status "Polled"; `config.json` `extra_dem` /
`extra_gop` adds names as status "Added" (shown as "Tracked"). Cory Booker is in
`extra_dem` (polled individually but not in aggregators or Wikipedia's list as of
2026-09-28).

Why the Virginia filter is strict: Google matches both terms anywhere in the
article body. Before the headline rule, results were dominated by unrelated VA
crime stories, West Virginia sports, and Senate letters Kaine co-signed. Kaine and
Mark Warner are deliberately not in `va_terms` for that reason.

Why R-media exists (added 2026-09-28): ranking counts volume, so an attack
campaign from right-wing outlets (e.g. Fox running a week on AOC and "the
socialist wing") would lift a Democrat on hostile attention alone. Right-media
attention is itself useful intel, so it is flagged rather than filtered. The page
shows the % in amber at 40%+ (with at least 5 stories) and marks those headlines
with an "R" tag. At launch the Dem top tier sat at 15–27%, and AOC was not an
outlier. Local Fox affiliates ("FOX 5 DC") deliberately don't match.

Google News RSS returns ~100 items per query. `at_cap: true` marks a candidate
who hit it (the page shows "75+"), meaning the true count is higher.

## 4. Data shape (`data/watch.json`)

```
generated         "YYYY-MM-DD HH:MM" (local)
delta_vs          history date the week-over-week change compares against (null until 7 days exist)
warnings          fetch/parse problems (empty on a clean run)
parties.dem|gop
  poll_aggregators, poll_updated, declined[]
  candidates[]: name, status, bio, rank, poll_avg, declined,
                mentions_7d, at_cap, delta_wk, headlines[15],
                right_7d, right_pct (int 0–100; null if no news),
                va_30d, va_headlines[8]          (dem only)
  headline: {title, source, url (Google News redirect), date ISO UTC, right bool}
```

Rank = sort by mentions_7d desc, then poll_avg desc, then status order.

## 5. Daily workflow

Automated: Windows Task Scheduler task **`2028_Watch_Update`**, daily 06:30, runs

```
python update_watch.py --publish   (in C:\repos\intel\2028-watch, output → data\last_run.log)
```

which rebuilds the data (~80 requests, ~2 min), commits `data/watch.json` +
`data/history.json` ("Daily 2028 refresh YYYY-MM-DD"), and pushes to `main`;
GitHub Pages redeploys in ~1 minute.

Safety: `--publish` refuses to push (exit 1) if there are fewer than 10 Democrats
or fewer than half returned news results — yesterday's data stays live. The page
header shows "data over a day old" if `generated` is more than 36 h old.

The task runs only while Brenner is logged on (Interactive, no stored password),
same as poll-tracker.

Manual:
```
cd C:\repos\intel\2028-watch
python update_watch.py              # full run, no push
python update_watch.py --no-news    # roster + polls only (~5 s)
python update_watch.py --publish    # same as the scheduled task
```

Curating: edit `config.json` (add a name, exclude one, add an alias like "AOC",
add a VA place), then run the script.

If the page looks stale: check `data\last_run.log` and the task's Last Run
Result. If Wikipedia restructures the primaries page, `warnings` / a short roster
will show it — the roster parser depends on the Candidates → h3 → h4 heading layout.
