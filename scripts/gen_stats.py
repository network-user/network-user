#!/usr/bin/env python3
"""Custom monochrome GitHub stats card generator.

Считает по ПУБЛИЧНЫМ репозиториям пользователя (owner, не форки):
  - общее число коммитов (за всё время, все авторы — обходит проблему
    атрибуции, когда коммиты сделаны под разными identity);
  - строки кода (сумма additions);
  - коммиты за последние 30 дней;
плюс топ-языки (единственный цветной блок) и 2D тепловой календарь.
Токен - встроенный GITHUB_TOKEN (только публичные данные).

Режим подсчёта: GH_COUNT=all (по умолчанию) — все коммиты в репо;
GH_COUNT=user — только коммиты, атрибутированные GH_USER.
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TOKEN = os.environ.get("GH_TOKEN")
USER = os.environ.get("GH_USER", "network-user")
OUT = os.environ.get("GH_OUT", "assets/stats.svg")
COUNT_MODE = os.environ.get("GH_COUNT", "all").lower()
if not TOKEN:
    print("GH_TOKEN is missing", file=sys.stderr)
    sys.exit(1)

REST = "https://api.github.com"
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "User-Agent": "custom-stats-generator",
}

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c", "Shell": "#89e051",
    "Dockerfile": "#384d54", "Go": "#00ADD8", "Rust": "#dea584", "C": "#555555",
    "C++": "#f34b7d", "Java": "#b07219", "Kotlin": "#A97BFF", "Ruby": "#701516",
    "PHP": "#4F5D95", "Vue": "#41b883", "Astro": "#ff5a03", "Svelte": "#ff3e00",
    "Jupyter Notebook": "#DA5B0B", "Makefile": "#427819", "PowerShell": "#012456",
    "Mako": "#7e858d", "Batchfile": "#C1F12E", "Procfile": "#7d8088",
}
LANG_FALLBACK = "#7d8088"


def _open(url, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=60)


def rest_get(path, retries=10):
    url = REST + path
    for _ in range(retries):
        try:
            r = _open(url)
            raw = r.read()
            if r.getcode() == 202:
                time.sleep(3)
                continue
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 202:
                time.sleep(3)
                continue
            if e.code in (403, 429):
                time.sleep(6)
                continue
            if e.code in (404, 204):
                return None
            print("REST %s -> HTTP %d" % (path, e.code), file=sys.stderr)
            return None
    return None


def gql(query, variables):
    r = _open(REST + "/graphql", method="POST",
              data={"query": query, "variables": variables})
    return json.loads(r.read())


def list_public_repos():
    repos, page = [], 1
    while True:
        batch = rest_get("/users/%s/repos?type=owner&per_page=100&page=%d" % (USER, page))
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


def repo_stats(owner, name):
    """(commits_all_time, additions, commits_last_30d) по репозиторию."""
    data = rest_get("/repos/%s/%s/stats/contributors" % (owner, name))
    if not isinstance(data, list):
        return 0, 0, 0
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    commits = adds = recent = 0
    for c in data:
        if COUNT_MODE == "user":
            login = (c.get("author") or {}).get("login") or ""
            if login.lower() != USER.lower():
                continue
        for w in c.get("weeks", []):
            commits += w.get("c", 0)
            adds += w.get("a", 0)
            if w.get("w", 0) >= cutoff:
                recent += w.get("c", 0)
    return commits, adds, recent


def repo_languages(owner, name):
    d = rest_get("/repos/%s/%s/languages" % (owner, name))
    return d if isinstance(d, dict) else {}


CAL_QUERY = ("query($login:String!){user(login:$login){contributionsCollection{"
             "contributionCalendar{weeks{contributionDays{contributionCount weekday}}}}}}")


def calendar_grid():
    """2D-сетка контрибуций; при любой ошибке GraphQL — пустой список (не падаем)."""
    try:
        res = gql(CAL_QUERY, {"login": USER})
        user = (res.get("data") or {}).get("user")
        if not user:
            print("calendar: GraphQL returned no user (%s)" % json.dumps(res.get("errors"))[:200],
                  file=sys.stderr)
            return []
        weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    except Exception as e:  # noqa: BLE001
        print("calendar: failed (%s)" % e, file=sys.stderr)
        return []
    grid = []
    for w in weeks:
        col = [0] * 7
        for d in w["contributionDays"]:
            col[d["weekday"]] = d["contributionCount"]
        grid.append(col)
    return grid


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
total_commits = total_loc = last30 = 0
lang_bytes = {}
for owner, name in repos:
    c, a, r = repo_stats(owner, name)
    total_commits += c
    total_loc += a
    last30 += r
    for lang, b in repo_languages(owner, name).items():
        lang_bytes[lang] = lang_bytes.get(lang, 0) + b

grid = calendar_grid()
langs = sorted(lang_bytes.items(), key=lambda x: -x[1])[:6]
lang_sum = sum(b for _, b in langs) or 1

print("MODE=%s repos=%d commits=%d loc=%d last30=%d langs=%d calendar_weeks=%d"
      % (COUNT_MODE, len(repos), total_commits, total_loc, last30, len(langs), len(grid)),
      file=sys.stderr)

# ------------------------- отрисовка SVG -------------------------
W, H = 880, 340
BG, TXT, MUT = "#131418", "#f3f3f1", "#a6a7ab"
CELL, GAP = 8, 2
STEP = CELL + GAP
cx0, cy0 = 28, 96
cols = len(grid)
mx = max((max(col) for col in grid), default=0) or 1
GRAY = ["#1b1d24", "#3a3f49", "#5c626e", "#9aa0ab", "#f3f3f1"]


def cell_color(v):
    if v <= 0:
        return GRAY[0]
    t = v / mx
    if t < 0.25:
        return GRAY[1]
    if t < 0.5:
        return GRAY[2]
    if t < 0.75:
        return GRAY[3]
    return GRAY[4]


parts = []
parts.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
             'font-family="Inter, Segoe UI, Arial, sans-serif" role="img" aria-label="GitHub stats">'
             % (W, H, W, H))
parts.append('<rect x="8" y="8" width="%d" height="%d" rx="14" fill="%s" stroke="#ffffff" '
             'stroke-opacity="0.10" stroke-width="1.5"/>' % (W - 16, H - 16, BG))
parts.append('<text x="28" y="48" font-size="20" font-weight="700" fill="%s">статистика · stats</text>' % TXT)

if cols:
    parts.append('<text x="28" y="80" font-size="13" fill="%s">контрибуции за год · contributions</text>' % MUT)
    for wi, col in enumerate(grid):
        for d, v in enumerate(col):
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" rx="2" fill="%s"/>'
                         % (cx0 + wi * STEP, cy0 + d * STEP, CELL, CELL, cell_color(v)))
    sx = cx0 + cols * STEP + 40
else:
    sx = cx0

stats = [
    ("{:,}".format(total_commits), "коммитов всего · commits (all public repos)"),
    (human(total_loc), "строк кода · lines of code"),
    ("{:,}".format(last30), "коммитов за 30 дней · last 30 days"),
]
sy = 104
for num, label in stats:
    parts.append('<text x="%d" y="%d" font-size="34" font-weight="800" fill="%s">%s</text>'
                 % (sx, sy, TXT, esc(num)))
    parts.append('<text x="%d" y="%d" font-size="13" fill="%s">%s</text>'
                 % (sx, sy + 22, MUT, esc(label)))
    sy += 70

ly = 262
parts.append('<text x="28" y="%d" font-size="13" fill="%s">языки · most used languages</text>' % (ly, MUT))
bar_x, bar_w, bar_y, bar_h = 28, W - 56, ly + 12, 14
parts.append('<clipPath id="lc"><rect x="%d" y="%d" width="%d" height="%d" rx="7"/></clipPath>'
             % (bar_x, bar_y, bar_w, bar_h))
parts.append('<g clip-path="url(#lc)">')
xoff = bar_x
for lang, b in langs:
    seg = bar_w * (b / lang_sum)
    parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>'
                 % (xoff, bar_y, seg + 0.6, bar_h, LANG_COLORS.get(lang, LANG_FALLBACK)))
    xoff += seg
parts.append('</g>')

lx, lyy = 28, bar_y + 40
for lang, b in langs:
    label = "%s %.1f%%" % (lang, 100.0 * b / lang_sum)
    parts.append('<circle cx="%d" cy="%d" r="6" fill="%s"/>'
                 % (lx + 6, lyy - 4, LANG_COLORS.get(lang, LANG_FALLBACK)))
    parts.append('<text x="%d" y="%d" font-size="13" fill="%s">%s</text>' % (lx + 18, lyy, MUT, esc(label)))
    lx += 20 + 10 * len(label)

parts.append('</svg>')

svg = "\n".join(parts)
os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="") as f:
    f.write(svg)
print("written %s (%d bytes)" % (OUT, len(svg.encode("utf-8"))), file=sys.stderr)
