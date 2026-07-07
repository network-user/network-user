#!/usr/bin/env python3
"""Custom monochrome GitHub stats card generator.

По ПУБЛИЧНЫМ репозиториям пользователя (owner, не форки), считая КАЖДЫЙ репо
напрямую (не полагаясь на contribution-график аккаунта, который занижен из-за
атрибуции коммитов на другой identity):
  - общее число коммитов (default-ветки, через Link-заголовок пагинации);
  - календарь за год и коммиты за 30 дней - из РЕАЛЬНЫХ дат коммитов;
  - строки кода (оценка из суммы байт /languages / BYTES_PER_LINE);
  - топ-языки (единственный цветной блок).
Токен - встроенный GITHUB_TOKEN.
"""
import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta, date

TOKEN = os.environ.get("GH_TOKEN")
USER = os.environ.get("GH_USER", "network-user")
OUT = os.environ.get("GH_OUT", "assets/stats.svg")
BYTES_PER_LINE = int(os.environ.get("GH_BYTES_PER_LINE", "40"))
if not TOKEN:
    print("GH_TOKEN is missing", file=sys.stderr)
    sys.exit(1)

REST = "https://api.github.com"
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "User-Agent": "custom-stats-generator",
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c", "Shell": "#89e051",
    "Dockerfile": "#384d54", "Go": "#00ADD8", "Rust": "#dea584", "C": "#555555",
    "C++": "#f34b7d", "Java": "#b07219", "Kotlin": "#A97BFF", "Ruby": "#701516",
    "PHP": "#4F5D95", "Vue": "#41b883", "Astro": "#ff5a03", "Svelte": "#ff3e00",
    "Jupyter Notebook": "#DA5B0B", "Makefile": "#427819", "PowerShell": "#012456",
    "Batchfile": "#C1F12E", "Mako": "#7e858d",
}
LANG_FALLBACK = "#7d8088"
OTHER_COLOR = "#3a3f49"


def rest_call(path):
    try:
        r = urllib.request.urlopen(urllib.request.Request(REST + path, headers=HEADERS), timeout=60)
        return r.getcode(), r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, (e.read() if e.fp else b"")


def rest_json(path, retries=8):
    for _ in range(retries):
        code, _, body = rest_call(path)
        if code == 202:
            time.sleep(3); continue
        if code in (403, 429):
            time.sleep(6); continue
        if code in (200, 201):
            return json.loads(body) if body else None
        if code in (404, 204, 409):
            return None
        print("REST %s -> HTTP %d" % (path, code), file=sys.stderr)
        return None
    return None


def commit_count(owner, name):
    """Всего коммитов в default-ветке через Link rel=last."""
    for _ in range(6):
        code, hdr, body = rest_call("/repos/%s/%s/commits?per_page=1" % (owner, name))
        if code in (403, 429):
            time.sleep(6); continue
        if code == 409:
            return 0
        if code != 200:
            return 0
        m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', hdr.get("Link", "") or "")
        if m:
            return int(m.group(1))
        try:
            return len(json.loads(body))
        except Exception:
            return 0
    return 0


def commit_dates(owner, name, since_iso, max_pages=25):
    """Даты (YYYY-MM-DD) всех коммитов default-ветки за период since..now."""
    dates, page = [], 1
    while page <= max_pages:
        got = None
        for _ in range(4):
            code, _, body = rest_call("/repos/%s/%s/commits?since=%s&per_page=100&page=%d"
                                      % (owner, name, since_iso, page))
            if code in (403, 429):
                time.sleep(6); continue
            got = (code, body)
            break
        if not got:
            break
        code, body = got
        if code == 409 or code != 200:
            break
        arr = json.loads(body) if body else []
        if not arr:
            break
        for it in arr:
            c = it.get("commit") or {}
            dt = ((c.get("committer") or {}).get("date")) or ((c.get("author") or {}).get("date"))
            if dt:
                dates.append(dt[:10])
        if len(arr) < 100:
            break
        page += 1
    return dates


def list_public_repos():
    repos, page = [], 1
    while True:
        batch = rest_json("/users/%s/repos?type=owner&per_page=100&page=%d" % (USER, page))
        if not batch:
            break
        for r in batch:
            if r.get("fork") or r.get("private"):
                continue
            repos.append((r["owner"]["login"], r["name"]))
        if len(batch) < 100:
            break
        page += 1
    return repos


def repo_languages(owner, name):
    d = rest_json("/repos/%s/%s/languages" % (owner, name))
    return d if isinstance(d, dict) else {}


def human(n):
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return str(n)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------- сбор данных -------------------------
repos = list_public_repos()
now = datetime.now(timezone.utc)
since_iso = (now - timedelta(days=372)).strftime("%Y-%m-%dT%H:%M:%SZ")
today = now.date()

total_commits = 0
daily = {}
lang_bytes = {}
for owner, name in repos:
    total_commits += commit_count(owner, name)
    for d in commit_dates(owner, name, since_iso):
        daily[d] = daily.get(d, 0) + 1
    for lang, b in repo_languages(owner, name).items():
        lang_bytes[lang] = lang_bytes.get(lang, 0) + b

cut30 = (today - timedelta(days=30)).isoformat()
last30 = sum(v for k, v in daily.items() if k >= cut30)
year_commits = sum(daily.values())
total_bytes = sum(lang_bytes.values())
loc = round(total_bytes / BYTES_PER_LINE) if BYTES_PER_LINE else total_bytes
langs = sorted(lang_bytes.items(), key=lambda x: -x[1])[:6]

# сетка календаря по неделям (воскресенье - первый день, как на GitHub)
start = today - timedelta(days=364)
start -= timedelta(days=(start.weekday() + 1) % 7)
weeks = []
wk = start
while wk <= today:
    days = []
    for i in range(7):
        cur = wk + timedelta(days=i)
        days.append(daily.get(cur.isoformat(), 0) if cur <= today else None)
    weeks.append((wk, days))
    wk += timedelta(days=7)

month_labels, prev = [], None
for i, (w0, _) in enumerate(weeks):
    if w0.month != prev:
        month_labels.append((i, MONTHS[w0.month - 1]))
        prev = w0.month

print("repos=%d commits=%d last30=%d year=%d loc=%d bytes=%d langs=%d weeks=%d"
      % (len(repos), total_commits, last30, year_commits, loc, total_bytes, len(langs), len(weeks)),
      file=sys.stderr)

# ------------------------- отрисовка SVG -------------------------
BG, TXT, MUT, FAINT = "#131418", "#f3f3f1", "#a6a7ab", "#6b6d73"
CELL, GAP = 11, 3
STEP = CELL + GAP
gx0, gy0 = 74, 150
W = gx0 + len(weeks) * STEP + 20
H = 340
mxc = max((v for v in daily.values()), default=0) or 1
GRAY = ["#1b1d24", "#39414c", "#5c6673", "#98a1ad", "#f3f3f1"]


def cell_color(v):
    if v is None or v <= 0:
        return GRAY[0]
    t = v / mxc
    return GRAY[1] if t < 0.25 else GRAY[2] if t < 0.5 else GRAY[3] if t < 0.75 else GRAY[4]


P = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
     'font-family="Inter, Segoe UI, Arial, sans-serif" role="img" aria-label="GitHub stats">' % (W, H, W, H)]
P.append('<rect x="8" y="8" width="%d" height="%d" rx="14" fill="%s" stroke="#ffffff" '
         'stroke-opacity="0.10" stroke-width="1.5"/>' % (W - 16, H - 16, BG))

# --- верхний ряд: три числа ---
tiles = [
    ("{:,}".format(total_commits), "коммитов всего · commits"),
    ("~" + human(loc), "строк кода · lines of code"),
    ("{:,}".format(last30), "за 30 дней · last 30 days"),
]
tx = 40
step_t = (W - 80) / 3.0
for num, label in tiles:
    P.append('<text x="%d" y="60" font-size="34" font-weight="800" fill="%s">%s</text>' % (int(tx), TXT, esc(num)))
    P.append('<text x="%d" y="82" font-size="13" fill="%s">%s</text>' % (int(tx), MUT, esc(label)))
    tx += step_t

# --- календарь: подпись, месяцы, дни недели, ячейки ---
P.append('<text x="40" y="122" font-size="13" fill="%s">контрибуции за год · %d commits this year</text>'
         % (MUT, year_commits))
for ci, mlab in month_labels:
    P.append('<text x="%d" y="142" font-size="12" fill="%s">%s</text>' % (gx0 + ci * STEP, FAINT, mlab))
for r, lab in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
    P.append('<text x="%d" y="%d" font-size="11" fill="%s" text-anchor="end">%s</text>'
             % (gx0 - 8, gy0 + r * STEP + CELL - 1, FAINT, lab))
for i, (_, days) in enumerate(weeks):
    for r, v in enumerate(days):
        if v is None:
            continue
        P.append('<rect x="%d" y="%d" width="%d" height="%d" rx="2" fill="%s"/>'
                 % (gx0 + i * STEP, gy0 + r * STEP, CELL, CELL, cell_color(v)))

# --- языки (единственный цветной блок) ---
ly = gy0 + 7 * STEP + 24
P.append('<text x="40" y="%d" font-size="13" fill="%s">языки · most used languages</text>' % (ly, MUT))
bar_x, bar_w, bar_y, bar_h = 40, W - 80, ly + 12, 14
denom = total_bytes or 1
P.append('<clipPath id="lc"><rect x="%d" y="%d" width="%d" height="%d" rx="7"/></clipPath>'
         % (bar_x, bar_y, bar_w, bar_h))
P.append('<g clip-path="url(#lc)">')
xoff = bar_x
for lang, b in langs:
    seg = bar_w * (b / denom)
    P.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>'
             % (xoff, bar_y, seg + 0.6, bar_h, LANG_COLORS.get(lang, LANG_FALLBACK)))
    xoff += seg
if xoff < bar_x + bar_w:
    P.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>'
             % (xoff, bar_y, bar_x + bar_w - xoff, bar_h, OTHER_COLOR))
P.append('</g>')
lx, lyy = 40, bar_y + 38
for lang, b in langs:
    label = "%s %.1f%%" % (lang, 100.0 * b / denom)
    P.append('<circle cx="%d" cy="%d" r="6" fill="%s"/>' % (lx + 6, lyy - 4, LANG_COLORS.get(lang, LANG_FALLBACK)))
    P.append('<text x="%d" y="%d" font-size="13" fill="%s">%s</text>' % (lx + 18, lyy, MUT, esc(label)))
    lx += 20 + 10 * len(label)

P.append('</svg>')
svg = "\n".join(P)
os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="") as f:
    f.write(svg)
print("written %s (%d bytes, W=%d)" % (OUT, len(svg.encode("utf-8")), W), file=sys.stderr)
