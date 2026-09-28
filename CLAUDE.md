# 2028 Watch (2028-watch)

Daily-glance tracker of the 2028 presidential field — Democrats first (Virginia
is an early primary state), GOP list secondary. Roster and national primary
poll averages come from Wikipedia; news "buzz" and Virginia-linked coverage come
from Google News RSS. Output is `data/watch.json`, read by a single-file HTML
dashboard. Live at https://brennertobe07.github.io/2028-watch/ — refreshed
daily 06:30 by the `2028_Watch_Update` scheduled task (`update_watch.py --publish`).

**Read `docs/REFERENCE.md` before non-trivial work** — file map, data shape,
source rules, and daily workflow live there.

## Conventions
- Python: stdlib + requests + bs4 only.
- Hand-curation lives in `config.json` (extra names, exclusions, search aliases,
  VA terms). The script never writes it.
- Ranking is by 7-day news count; poll average is shown alongside, never blended.
- No coverage / no poll renders as "—", not 0.
- Virginia coverage is strict on purpose: the headline must name the candidate AND
  a VA place/figure. Don't loosen it to body matches — that was mostly noise.
- Dashboard stays single-file HTML, DPVA dark theme, no build step.
- `--publish` is the only thing that pushes; it refuses thin runs.
- Editing regexes via bash heredocs has turned `\b` into a backspace char here —
  use the Edit tool for regex changes.
