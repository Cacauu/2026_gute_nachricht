#!/usr/bin/env python3
"""Scrape taz "die gute nachricht" archive results into a categorized CSV.

The script intentionally uses only Python's standard library so it can run in a
plain Python installation. Topic detection is a lightweight German keyword
classifier over the article title.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_START_URL = (
    "https://taz.de/!s=gute+nachricht&ExportStatus=Intern&SuchRahmen=Print"
    "&Seite=17&isWochenende=1/"
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; gute-nachricht-scraper/1.0; "
    "+https://taz.de/)"
)


TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "climate_environment": (
        "klima",
        "solar",
        "wind",
        "energie",
        "erneuer",
        "co2",
        "emission",
        "natur",
        "wald",
        "moor",
        "meer",
        "ozean",
        "wasser",
        "hochwasser",
        "dürre",
        "hitze",
        "artenschutz",
        "biodivers",
        "tiere",
        "robben",
        "vögel",
        "fisch",
        "pflanzen",
        "baum",
        "bäume",
        "recycling",
        "kreislauf",
        "reparatur",
        "müll",
        "plastik",
        "hochsee",
        "ökolog",
        "umwelt",
        "fossil",
        "kohle",
        "braunkohle",
        "ozon",
        "wärmepump",
        "kraftwerk",
        "photovoltaik",
        "abholzung",
        "feinstaub",
        "luft",
        "strom",
        "gasfeld",
        "badegewässer",
        "nachhaltig",
        "beheizt",
        "storch",
        "störch",
        "luchs",
        "nashorn",
        "nashörner",
        "kākāpō",
        "gecko",
        "wal",
        "wale",
    ),
    "health": (
        "gesund",
        "medizin",
        "impf",
        "krebs",
        "antibiotika",
        "suizid",
        "mutterschutz",
        "fehlgeburt",
        "pflege",
        "psych",
        "therapie",
        "kranken",
        "virus",
        "pandemie",
        "hiv",
        "aids",
        "malaria",
        "lepra",
        "trachom",
        "fruchtbarkeit",
        "schlaf",
        "patient",
        "ärzt",
        "ärzte",
        "eizelle",
        "organ",
        "lungenkrebs",
        "herzstillstand",
        "rauchen",
        "zigarette",
        "vape",
        "alkohol",
        "bier",
        "demenz",
    ),
    "society_rights": (
        "menschenrecht",
        "frauen",
        "mädchen",
        "gleichstellung",
        "queer",
        "lgbt",
        "rassismus",
        "diskrimin",
        "geflücht",
        "flücht",
        "migrant",
        "protest",
        "politisch",
        "demokratie",
        "rechts",
        "inklusion",
        "armut",
        "wohnung",
        "obdach",
        "soziale",
        "gerechtigkeit",
        "fried",
        "gewalt",
        "gender",
        "femizid",
        "divers",
        "parlament",
        "weiblich",
        "indigen",
        "schutz",
    ),
    "education": (
        "schule",
        "bildung",
        "lehramt",
        "klassenzimmer",
        "uni",
        "universität",
        "student",
        "studierende",
        "kita",
        "kind",
        "grundschül",
        "schul",
        "lernen",
        "lesen",
        "schreiben",
        "lehrer",
        "lehrerin",
        "abschluss",
        "azubi",
        "auszubild",
        "erzieher",
        "professur",
    ),
    "economy_work": (
        "arbeit",
        "arbeits",
        "jobs",
        "lohn",
        "mindestlohn",
        "gehalt",
        "gewerkschaft",
        "börse",
        "wirtschaft",
        "unternehmen",
        "mittelstand",
        "steuer",
        "geld",
        "rente",
        "beschäftig",
        "industrie",
        "produktion",
        "job",
        "beruf",
        "berufswahl",
        "grundeinkommen",
        "überschuldet",
        "vier-tage",
        "überstunden",
        "lieferketten",
    ),
    "technology_science": (
        "ki",
        "künstliche intelligenz",
        "computer",
        "quanten",
        "daten",
        "dna",
        "forschung",
        "studie",
        "wissenschaft",
        "technolog",
        "roboter",
        "exoskelett",
        "software",
        "internet",
        "weltraum",
        "planet",
        "satellit",
        "batteriespeicher",
    ),
    "mobility_transport": (
        "verkehr",
        "auto",
        "autofahrt",
        "e-bike",
        "fahrrad",
        "rad",
        "bahn",
        "bus",
        "busse",
        "zug",
        "flug",
        "geflogen",
        "mobilität",
        "pendel",
        "ticket",
        "taxi",
        "fuß",
        "carsharing",
        "lastenrad",
        "benzin",
        "diesel",
    ),
    "culture_sports": (
        "sport",
        "dfb",
        "fußball",
        "kicken",
        "musik",
        "film",
        "theater",
        "buch",
        "bücher",
        "kunst",
        "künstler",
        "kultur",
        "museum",
    ),
    "food_agriculture": (
        "lebensmittel",
        "vegan",
        "mahlzeit",
        "supermarkt",
        "käse",
        "gegessen",
        "ente",
        "ernährung",
        "landwirtschaft",
        "biolandbau",
        "biobauer",
        "biobäuer",
        "acker",
        "bauern",
        "tierhaltung",
        "fleisch",
        "milch",
        "getreide",
        "obst",
        "gemüse",
    ),
}


@dataclass
class Article:
    date: str = ""
    title: str = ""
    author: str = ""
    link: str = ""
    topline: str = ""
    category: str = ""
    text: str = ""


class TazSearchParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.articles: list[Article] = []
        self.max_page = 1
        self._in_article = False
        self._article_depth = 0
        self._current: Article | None = None
        self._capture: str | None = None
        self._capture_parts: list[str] = []
        self._seen_issue_label = False
        self._in_pagination = False
        self._pagination_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        classes = set(attr.get("class", "").split())

        if tag == "nav" and attr.get("role") == "navigation" and attr.get("aria-label") == "pagination":
            self._in_pagination = True
            self._pagination_depth = 1
            return

        if self._in_pagination:
            self._pagination_depth += 1
            if tag == "a":
                label = attr.get("aria-label", "")
                match = re.search(r"Goto page\s+(\d+)", label)
                if match:
                    self.max_page = max(self.max_page, int(match.group(1)))
            return

        if tag == "article" and {"article-teaser", "is-archive-print-teaser"} <= classes:
            self._in_article = True
            self._article_depth = 1
            self._current = Article()
            self._seen_issue_label = False
            return

        if not self._in_article:
            return

        self._article_depth += 1

        if tag == "a" and self._current and not self._current.link and "teaser-link" in classes:
            self._current.link = urljoin(self.base_url, html.unescape(attr.get("href", "")))
        elif tag == "span" and "typo-r-topline" in classes:
            self._start_capture("topline")
        elif tag == "span" and ("headline" in classes or "typo-r-head-small" in classes):
            self._start_capture("title")
        elif tag == "span" and "typo-r-name" in classes:
            self._start_capture("author")
        elif (
            tag == "span"
            and "typo-link-grey-onpage" in classes
            and self._seen_issue_label
            and self._current
            and not self._current.date
        ):
            self._start_capture("date")

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag == "span":
            value = clean_text(" ".join(self._capture_parts))
            if self._current:
                setattr(self._current, self._capture, value)
            self._capture = None
            self._capture_parts = []

        if self._in_pagination:
            self._pagination_depth -= 1
            if self._pagination_depth <= 0:
                self._in_pagination = False
            return

        if self._in_article:
            self._article_depth -= 1
            if tag == "article" or self._article_depth <= 0:
                if self._current and self._is_good_news(self._current):
                    self.articles.append(self._current)
                self._in_article = False
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._capture_parts.append(data)
        if self._in_article and "Ausgabe vom" in data:
            self._seen_issue_label = True

    def _start_capture(self, name: str) -> None:
        self._capture = name
        self._capture_parts = []

    @staticmethod
    def _is_good_news(article: Article) -> bool:
        return (
            article.link.startswith("https://taz.de/die-gute-nachricht/")
            or clean_text(article.topline).lower() == "die gute nachricht"
        )


class TazArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text = ""
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if tag == "script" and attr.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self._read_json_ld("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_ld_parts.append(data)

    def _read_json_ld(self, raw_json: str) -> None:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        for item in iter_json_ld_items(payload):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "NewsArticle" not in types:
                continue
            article_body = item.get("articleBody")
            if isinstance(article_body, str):
                self.text = clean_text(article_body)
                return


def iter_json_ld_items(payload: object) -> Iterable[dict[str, object]]:
    if isinstance(payload, dict):
        if "@graph" in payload:
            yield from iter_json_ld_items(payload["@graph"])
        else:
            yield payload
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_json_ld_items(item)


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def fetch(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def page_url(start_url: str, page_number: int) -> str:
    if page_number == 1:
        return start_url
    parts = urlsplit(start_url)
    query = f"search_page={page_number - 1}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def classify_topic(title: str) -> str:
    normalized = title.lower().replace("-", " ")
    scores: dict[str, int] = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword_matches(keyword, normalized))
        if score:
            scores[topic] = score

    if not scores:
        return "other"

    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]


def keyword_matches(keyword: str, text: str) -> bool:
    if " " in keyword:
        return keyword in text
    if len(keyword) <= 3:
        return re.search(rf"(?<![a-zäöüß]){re.escape(keyword)}(?![a-zäöüß])", text) is not None
    return keyword in text


def article_text_from_html(raw_html: str) -> str:
    parser = TazArticleTextParser()
    parser.feed(raw_html)
    return parser.text


def enrich_article_text(articles: Iterable[Article], delay: float) -> None:
    articles = [article for article in articles if article.link]
    total = len(articles)
    for index, article in enumerate(articles, start=1):
        print(f"Fetching article text {index}/{total}: {article.link}", file=sys.stderr)
        if delay and index > 1:
            time.sleep(delay)
        try:
            article.text = article_text_from_html(fetch(article.link))
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"Article text fetch failed for {article.link}: {exc}", file=sys.stderr)


def dedupe_articles(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = article.link or f"{article.date}|{article.title}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


def scrape(start_url: str, delay: float = 0.5, max_pages: int | None = None) -> list[Article]:
    first_html = fetch(start_url)
    first_parser = TazSearchParser(start_url)
    first_parser.feed(first_html)
    total_pages = first_parser.max_page
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    print(f"Found {total_pages} result page(s).", file=sys.stderr)
    articles = list(first_parser.articles)

    for page_number in range(2, total_pages + 1):
        url = page_url(start_url, page_number)
        print(f"Fetching page {page_number}/{total_pages}: {url}", file=sys.stderr)
        if delay:
            time.sleep(delay)
        parser = TazSearchParser(url)
        parser.feed(fetch(url))
        articles.extend(parser.articles)

    unique = dedupe_articles(articles)
    enrich_article_text(unique, delay=delay)
    for article in unique:
        article.category = classify_topic(article.title)
    return unique


def write_csv(path: str, articles: Iterable[Article]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "title", "author", "link", "category", "text"])
        writer.writeheader()
        for article in articles:
            writer.writerow(
                {
                    "date": clean_date(article.date),
                    "title": article.title,
                    "author": article.author,
                    "link": article.link,
                    "category": article.category,
                    "text": article.text,
                }
            )


def article_to_dict(article: Article) -> dict[str, str]:
    return {
        "date": clean_date(article.date),
        "title": article.title,
        "author": article.author,
        "link": article.link,
        "category": article.category,
        "text": article.text,
    }


def write_html(path: str, articles: Iterable[Article]) -> None:
    article_list = list(articles)
    rows_json = json.dumps(
        [article_to_dict(article) for article in article_list],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    rendered = (
        HTML_TEMPLATE.replace("__ROWS_JSON__", rows_json)
        .replace("__INITIAL_ROWS__", render_initial_rows(article_list))
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(rendered)


def render_initial_rows(articles: Iterable[Article]) -> str:
    """Render article rows so the dashboard remains useful without JavaScript."""
    rows = []
    for article in articles:
        date = html.escape(clean_date(article.date))
        title = html.escape(article.title)
        author = html.escape(article.author or "Ohne Angabe")
        link = html.escape(article.link, quote=True)
        category = html.escape(article.category or "other")
        rows.append(
            f'<tr><td class="muted">{date}</td><td class="title-cell">'
            f'<a href="{link}" target="_blank" rel="noopener">{title}</a></td>'
            f'<td>{author}</td><td><span class="tag">{category}</span></td></tr>'
        )
    return "".join(rows)


def clean_date(value: str) -> str:
    match = re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", value)
    return match.group(0) if match else clean_text(value).rstrip(",")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-url", default=DEFAULT_START_URL)
    parser.add_argument("--output", default="taz_gute_nachricht.csv")
    parser.add_argument("--html-output", default="index.html")
    parser.add_argument("--snapshot-dir", default="snapshots", help="Directory for full timestamped HTML snapshots (UTC).")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between page requests in seconds.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional cap for test runs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        articles = scrape(args.start_url, delay=args.delay, max_pages=args.max_pages)
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return 1

    if not articles or any(not article.title.strip() for article in articles):
        print("Scrape failed: no articles or missing article titles; refusing to overwrite outputs.", file=sys.stderr)
        return 1

    write_csv(args.output, articles)
    write_html(args.html_output, articles)
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    snapshot_path = snapshot_dir / f"index-{stamp}.html"
    with snapshot_path.open("xb") as handle:
        handle.write(Path(args.html_output).read_bytes())
    print(f"Wrote {len(articles)} rows to {args.output}", file=sys.stderr)
    print(f"Wrote self-contained dashboard to {args.html_output}", file=sys.stderr)
    print(f"Saved full HTML snapshot to {snapshot_path}", file=sys.stderr)
    return 0


HTML_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>taz gute Nachrichten</title>
  <style>
    :root{--bg:#f7f7f4;--ink:#202124;--muted:#63645f;--line:#d8d7ce;--panel:#fff;--accent:#c62828;--accent-soft:#fde8e6;--shadow:0 10px 28px rgba(32,33,36,.08)}
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}
    header{background:#111;color:#fff;border-bottom:5px solid var(--accent)} .wrap{width:min(1180px,calc(100% - 32px));margin:0 auto}
    .topbar{display:flex;align-items:end;justify-content:space-between;gap:24px;padding:28px 0 22px} h1{margin:0;font-size:32px;line-height:1.1;letter-spacing:0}
    .subtitle{margin:8px 0 0;color:#d5d5d0;font-size:15px}.count{min-width:120px;text-align:right;font-size:14px;color:#d5d5d0}.count strong{display:block;color:#fff;font-size:28px;line-height:1}
    main{padding:24px 0 40px}.controls{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:16px;align-items:center;margin-bottom:18px}
    .search,.button{height:44px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}.search{width:100%;padding:0 14px}.search:focus{border-color:var(--accent);outline:3px solid var(--accent-soft)}
    .button{padding:0 14px;cursor:pointer}.button:hover{border-color:var(--accent);color:var(--accent)}.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px}
    .chip{border:1px solid var(--line);border-radius:999px;padding:7px 11px;background:#fff;color:var(--ink);font:inherit;font-size:14px;cursor:pointer}.chip[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:#991b1b;font-weight:650}
    .grid{display:grid;grid-template-columns:repeat(12,1fr);gap:18px;align-items:start}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow)}.panel h2{margin:0;padding:16px 18px 0;font-size:18px;letter-spacing:0}
    .authors,.category-chart,.recent{grid-column:span 4}.is-filtered .authors{grid-column:1/-1}.is-filtered .category-chart,.is-filtered .recent{display:none}.table-panel{grid-column:1/-1;overflow:hidden}.bars{padding:14px 18px 18px;display:grid;gap:10px}
    .bar-row{display:grid;grid-template-columns:minmax(120px,1fr) minmax(90px,42%) 38px;gap:10px;align-items:center;font-size:14px}.bar-label{min-width:0;overflow-wrap:anywhere}.bar-track{height:9px;border-radius:999px;background:#ecebe5;overflow:hidden}.bar-fill{width:var(--w);height:100%;border-radius:inherit;background:var(--bar,var(--accent))}.bar-value{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
    .recent-block{border-top:1px solid var(--line);padding:14px 18px 16px}.recent-block:first-of-type{border-top:0}.recent-title{margin:0 0 10px;color:var(--muted);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
    table{width:100%;border-collapse:collapse;background:#fff}th,td{padding:11px 12px;border-top:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}th{position:sticky;top:0;z-index:1;background:#f1f0ea;color:#33342f;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
    td:first-child,th:first-child{padding-left:18px}td:last-child,th:last-child{padding-right:18px}.title-cell{min-width:260px;font-weight:650}.title-cell a{color:var(--ink);text-decoration-color:rgba(198,40,40,.4);text-underline-offset:3px}.title-cell a:hover{color:var(--accent)}.muted{color:var(--muted)}.tag{display:inline-flex;max-width:100%;border-radius:999px;padding:3px 8px;background:#efeee8;color:#353630;font-size:12px;font-weight:650;white-space:nowrap}.empty{padding:30px 18px;color:var(--muted);text-align:center}
    @media(max-width:920px){.topbar,.controls{grid-template-columns:1fr;display:grid}.count{text-align:left}.authors,.category-chart,.recent{grid-column:1/-1}.table-wrap{overflow-x:auto}}@media(max-width:560px){.wrap{width:min(100% - 20px,1180px)}h1{font-size:26px}.chip{font-size:13px}th,td{padding:9px 10px}}
  </style>
</head>
<body>
  <header><div class="wrap topbar"><div><h1>Gute Nachrichten aus taz zukunft</h1><p class="subtitle">Durchsuchbare Übersicht mit Themenfiltern und schnellen Statistiken.</p></div><div class="count" aria-live="polite"><strong id="visibleCount">0</strong><span id="totalCount">Einträge</span></div></div></header>
  <main class="wrap">
    <section class="controls" aria-label="Suche und Aktionen"><input id="search" class="search" type="search" placeholder="Suchen nach Titel, Volltext, Autorin oder Datum"><button id="reset" class="button" type="button">Zurücksetzen</button></section>
    <nav id="filters" class="filters" aria-label="Kategorien"></nav>
    <section class="grid" aria-label="Statistische Übersicht">
      <article class="panel authors"><h2>Aktivste Autor:innen</h2><div id="authors" class="bars"></div></article>
      <article class="panel category-chart"><h2>Einträge je Kategorie</h2><div id="categories" class="bars"></div></article>
      <article class="panel recent"><h2>Häufigste Themen</h2><div class="recent-block"><p class="recent-title">Letzte 3 Monate</p><div id="topics3" class="bars"></div></div><div class="recent-block"><p class="recent-title">Letzte 12 Monate</p><div id="topics12" class="bars"></div></div></article>
      <article class="panel table-panel"><div class="table-wrap"><table><thead><tr><th>Datum</th><th>Titel</th><th>Autor:in</th><th>Kategorie</th></tr></thead><tbody id="rows">__INITIAL_ROWS__</tbody></table><div id="empty" class="empty" hidden>Keine Einträge passen zu den aktuellen Filtern.</div></div></article>
    </section>
  </main>
  <script>
    const EMBEDDED_ROWS = __ROWS_JSON__;
    const CATEGORY_LABELS={all:"Alle",climate_environment:"Klima & Umwelt",health:"Gesundheit",society_rights:"Gesellschaft & Rechte",education:"Bildung",economy_work:"Wirtschaft & Arbeit",technology_science:"Technologie & Wissenschaft",mobility_transport:"Mobilität & Verkehr",culture_sports:"Kultur & Sport",food_agriculture:"Ernährung & Landwirtschaft",other:"Sonstiges"};
    const CATEGORY_COLORS={climate_environment:"#23845c",health:"#c62828",society_rights:"#6f4ba3",education:"#2767ad",economy_work:"#a86c00",technology_science:"#4b6f78",mobility_transport:"#00838f",culture_sports:"#8e3d64",food_agriculture:"#6f7c21",other:"#73736b"};
    const state={allRows:[],visibleRows:[],category:"all",search:""};const collator=new Intl.Collator("de",{sensitivity:"base"});const dateFormatter=new Intl.DateTimeFormat("de-DE",{day:"2-digit",month:"2-digit",year:"numeric"});
    init();
    function init(){state.allRows=EMBEDDED_ROWS.map(normalizeRow).filter(row=>row.title);state.allRows.sort((a,b)=>b.dateObj-a.dateObj||collator.compare(a.title,b.title));renderFilters();bindControls();update()}
    function bindControls(){document.getElementById("search").addEventListener("input",event=>{state.search=event.target.value.trim().toLowerCase();update()});document.getElementById("reset").addEventListener("click",()=>{state.category="all";state.search="";document.getElementById("search").value="";update()})}
    function renderFilters(){const counts=countBy(state.allRows,row=>row.category);const categories=Object.keys(CATEGORY_LABELS).filter(category=>category==="all"||counts.get(category));document.getElementById("filters").innerHTML=categories.map(category=>{const count=category==="all"?state.allRows.length:counts.get(category);return `<button class="chip" type="button" data-category="${category}" aria-pressed="${category===state.category}">${CATEGORY_LABELS[category]} (${count})</button>`}).join("");document.querySelectorAll(".chip").forEach(button=>{button.addEventListener("click",()=>{state.category=button.dataset.category;update()})})}
    function update(){state.visibleRows=state.allRows.filter(row=>{const categoryMatches=state.category==="all"||row.category===state.category;const haystack=`${row.date} ${row.title} ${row.author} ${labelFor(row.category)} ${row.text}`.toLowerCase();return categoryMatches&&(!state.search||haystack.includes(state.search))});document.querySelectorAll(".chip").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.category===state.category)));document.querySelector(".grid").classList.toggle("is-filtered",state.category!=="all");document.getElementById("visibleCount").textContent=state.visibleRows.length;document.getElementById("totalCount").textContent=`von ${state.allRows.length} Einträgen`;renderStats();renderTable()}
    function renderStats(){renderBars("authors",topCounts(state.visibleRows,row=>row.author||"Ohne Angabe",8),"#c62828");renderBars("categories",topCounts(state.visibleRows,row=>labelFor(row.category),10),(_,label)=>colorForLabel(label));const latestDate=state.allRows.reduce((latest,row)=>row.dateObj>latest?row.dateObj:latest,new Date(0));renderBars("topics3",topCounts(rowsSince(state.visibleRows,latestDate,3),row=>labelFor(row.category),5),(_,label)=>colorForLabel(label));renderBars("topics12",topCounts(rowsSince(state.visibleRows,latestDate,12),row=>labelFor(row.category),5),(_,label)=>colorForLabel(label))}
    function renderTable(){const tbody=document.getElementById("rows");tbody.innerHTML=state.visibleRows.map(row=>`<tr><td class="muted">${dateFormatter.format(row.dateObj)}</td><td class="title-cell"><a href="${escapeAttr(row.link)}" target="_blank" rel="noopener">${escapeHtml(row.title)}</a></td><td>${escapeHtml(row.author||"Ohne Angabe")}</td><td><span class="tag">${labelFor(row.category)}</span></td></tr>`).join("");document.getElementById("empty").hidden=state.visibleRows.length>0}
    function renderBars(id,items,color){const container=document.getElementById(id);if(!items.length){container.innerHTML=`<div class="muted">Keine Daten</div>`;return}const max=Math.max(...items.map(item=>item.count));container.innerHTML=items.map(item=>{const width=Math.max(5,Math.round(item.count/max*100));const barColor=typeof color==="function"?color(item.key,item.label):color;return `<div class="bar-row"><div class="bar-label" title="${escapeAttr(item.label)}">${escapeHtml(item.label)}</div><div class="bar-track"><div class="bar-fill" style="--w:${width}%;--bar:${barColor}"></div></div><div class="bar-value">${item.count}</div></div>`}).join("")}
    function topCounts(rows,keyFn,limit){return[...countBy(rows,keyFn)].map(([label,count])=>({key:label,label,count})).sort((a,b)=>b.count-a.count||collator.compare(a.label,b.label)).slice(0,limit)}
    function countBy(rows,keyFn){const map=new Map();rows.forEach(row=>{const key=keyFn(row);map.set(key,(map.get(key)||0)+1)});return map}
    function rowsSince(rows,latestDate,months){const cutoff=new Date(latestDate);cutoff.setMonth(cutoff.getMonth()-months);return rows.filter(row=>row.dateObj>=cutoff)}
    function normalizeRow(row){return{date:row.date,dateObj:parseGermanDate(row.date),title:row.title||"",author:row.author||"",link:row.link||"",category:row.category||"other",text:row.text||""}}
    function parseGermanDate(value){const[day,month,year]=value.split(".").map(Number);return new Date(year,month-1,day)}
    function labelFor(category){return CATEGORY_LABELS[category]||category}
    function colorForLabel(label){const category=Object.entries(CATEGORY_LABELS).find(([,value])=>value===label)?.[0];return CATEGORY_COLORS[category]||"#73736b"}
    function escapeHtml(value){return String(value).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;")}
    function escapeAttr(value){return escapeHtml(value).replaceAll("`","&#096;")}
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
