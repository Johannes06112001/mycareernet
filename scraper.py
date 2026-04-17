"""
MyCareernet Job Scraper
========================
API Endpoint: POST https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/

Modes:
  python scraper.py --discover   → Öffnet Playwright, fängt echten API-Request ab, speichert Payload
  python scraper.py              → Nutzt gespeicherten Payload + ruft API direkt auf → generiert HTML
  python scraper.py --keyword BASF --location India  → Gefilterte Suche
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime
from pathlib import Path

# ── Konfiguration ─────────────────────────────────────────────────────────────

API_URL     = "https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/"
PORTAL_URL  = "https://mycareernet.co/mycareernet/jobs"
PAYLOAD_CACHE = Path("discovered_payload.json")
OUTPUT_HTML   = Path("index.html")

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Content-Type":    "application/json",
    "Accept":          "application/json, text/plain, */*",
    "Origin":          "https://mycareernet.co",
    "Referer":         "https://mycareernet.co/mycareernet/jobs",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Phase 1: API-Payload via Playwright entdecken ─────────────────────────────

def discover_payload(keyword: str = "", location: str = "") -> dict:
    """Öffnet die Seite im Headless-Browser, fängt den getAll-Request ab."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] Playwright nicht installiert: pip install playwright && playwright install chromium")
        sys.exit(1)

    captured = {}

    print("[DISCOVER] Starte Browser-Interception...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = ctx.new_page()

        def on_request(request):
            if "getAll" in request.url and request.method == "POST":
                try:
                    body = request.post_data
                    if body:
                        captured["url"]     = request.url
                        captured["payload"] = json.loads(body)
                        captured["headers"] = dict(request.headers)
                        print(f"[DISCOVER] ✅ API-Request abgefangen: {request.url}")
                        print(f"[DISCOVER]    Payload: {body[:300]}")
                except Exception as e:
                    print(f"[DISCOVER] Payload-Parse-Fehler: {e}")

        page.on("request", on_request)

        print(f"[DISCOVER] Lade {PORTAL_URL} ...")
        page.goto(PORTAL_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)  # Extra-Warten auf lazy-loaded requests

        browser.close()

    if not captured:
        print("[DISCOVER] ⚠️  Kein getAll-Request abgefangen. Fallback auf Standard-Payload.")
        captured["payload"] = build_default_payload(keyword, location)
        captured["url"] = API_URL

    # Payload patchen falls keyword/location angegeben
    if keyword or location:
        captured["payload"] = patch_payload(captured["payload"], keyword, location)

    PAYLOAD_CACHE.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print(f"[DISCOVER] Payload gespeichert in {PAYLOAD_CACHE}")
    return captured


def build_default_payload(keyword: str = "", location: str = "") -> dict:
    """Standard-Payload-Struktur (typisch für HirePro-Backend)."""
    return {
        "keyword":    keyword,
        "location":   [location] if location else [],
        "pageNo":     1,
        "pageSize":   50,
        "sortBy":     "latest",
        "filters":    {},
        "searchType": "jobs",
    }


def patch_payload(payload: dict, keyword: str, location: str) -> dict:
    """Überschreibt Keyword/Location im abgefangenen Payload."""
    patched = payload.copy()
    # Versuche gängige Felder zu setzen
    for k in ["keyword", "searchText", "query", "q", "search"]:
        if k in patched:
            patched[k] = keyword
            break
    else:
        patched["keyword"] = keyword

    for loc_key in ["location", "locations", "city"]:
        if loc_key in patched:
            patched[loc_key] = [location] if location else []
            break

    return patched


# ── Phase 2: API direkt aufrufen ──────────────────────────────────────────────

def fetch_jobs(payload: dict, max_pages: int = 5) -> list[dict]:
    """Ruft die API mit Pagination auf, gibt alle Jobs zurück."""
    all_jobs = []
    session  = requests.Session()
    session.headers.update(HEADERS)

    # Finde pagination-Schlüssel im Payload
    page_key = next((k for k in ["pageNo", "page", "pageNum", "offset"] if k in payload), "pageNo")
    size_key = next((k for k in ["pageSize", "limit", "size", "count"] if k in payload), "pageSize")

    page_size = payload.get(size_key, 20)
    current_page = 1

    print(f"[FETCH] Starte API-Calls (max {max_pages} Seiten, {page_size} Jobs/Seite)...")

    while current_page <= max_pages:
        p = payload.copy()
        p[page_key] = current_page

        try:
            resp = session.post(API_URL, json=p, timeout=15)
            print(f"[FETCH] Seite {current_page}: HTTP {resp.status_code}")

            if resp.status_code != 200:
                print(f"[FETCH] Fehler: {resp.text[:200]}")
                break

            data = resp.json()

            # Flexible Job-Extraktion (verschiedene Response-Strukturen)
            jobs = extract_jobs_from_response(data)

            if not jobs:
                print(f"[FETCH] Keine weiteren Jobs auf Seite {current_page}.")
                break

            all_jobs.extend(jobs)
            print(f"[FETCH] +{len(jobs)} Jobs (Gesamt: {len(all_jobs)})")

            # Prüfe ob es noch mehr Seiten gibt
            total = get_total_count(data)
            if total and len(all_jobs) >= total:
                break

            current_page += 1
            time.sleep(0.8)  # Höfliche Pause

        except Exception as e:
            print(f"[FETCH] Exception auf Seite {current_page}: {e}")
            break

    print(f"[FETCH] Fertig. {len(all_jobs)} Jobs insgesamt.")
    return all_jobs


def extract_jobs_from_response(data: dict | list) -> list[dict]:
    """Extrahiert Jobs aus verschiedenen möglichen Response-Strukturen."""
    if isinstance(data, list):
        return data

    # Gängige Wrapper-Schlüssel
    for key in ["data", "jobs", "results", "jobList", "jobListings",
                "response", "content", "items", "records"]:
        if key in data:
            val = data[key]
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                # Noch eine Ebene tiefer
                for inner in ["data", "jobs", "results", "list", "records"]:
                    if inner in val and isinstance(val[inner], list):
                        return val[inner]

    return []


def get_total_count(data: dict) -> int | None:
    """Versucht die Gesamtanzahl aus der Response zu lesen."""
    for key in ["total", "totalCount", "totalRecords", "count",
                "totalJobs", "totalResults"]:
        if key in data:
            return int(data[key])
        if "data" in data and isinstance(data["data"], dict):
            if key in data["data"]:
                return int(data["data"][key])
    return None


# ── Phase 3: Statisches HTML generieren ──────────────────────────────────────

def normalize_job(raw: dict) -> dict:
    """Normalisiert ein Job-Objekt auf ein einheitliches Schema."""
    def get(*keys, default=""):
        for k in keys:
            if k in raw and raw[k]:
                return str(raw[k])
        return default

    return {
        "id":          get("id", "jobId", "job_id", "_id", default=""),
        "title":       get("title", "jobTitle", "job_title", "designation", default="Unbekannte Stelle"),
        "company":     get("company", "companyName", "company_name", "employer", default=""),
        "location":    get("location", "jobLocation", "city", "locations", default="India"),
        "experience":  get("experience", "experienceRequired", "exp", "minExperience", default=""),
        "salary":      get("salary", "salaryRange", "ctc", "compensation", default=""),
        "posted_date": get("postedDate", "posted_date", "createdAt", "publishedDate", default=""),
        "skills":      get_skills(raw),
        "description": get("description", "jobDescription", "shortDescription", default=""),
        "apply_url":   get("applyUrl", "apply_url", "jobUrl", "url", "link", default=""),
        "job_type":    get("jobType", "employmentType", "type", default=""),
        "category":    get("category", "function", "domain", "department", default=""),
    }


def get_skills(raw: dict) -> str:
    for key in ["skills", "keySkills", "key_skills", "tags", "skillSet"]:
        val = raw.get(key)
        if not val:
            continue
        if isinstance(val, list):
            return ", ".join(str(s) for s in val if s)
        if isinstance(val, str):
            return val
    return ""


def format_date(raw_date: str) -> str:
    """Versucht ein Datum lesbar zu machen."""
    if not raw_date:
        return ""
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            dt = datetime.strptime(raw_date[:26], fmt)
            return dt.strftime("%d. %b %Y")
        except ValueError:
            continue
    return raw_date[:10]


def generate_html(jobs: list[dict], keyword: str = "", location: str = "") -> str:
    """Generiert das vollständige statische HTML."""
    normalized = [normalize_job(j) for j in jobs]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    # Job-Cards
    cards_html = ""
    for job in normalized:
        date_str   = format_date(job["posted_date"])
        apply_link = job["apply_url"] or f"https://mycareernet.co/mycareernet/jobs"
        skills     = job["skills"]
        skills_html = ""
        if skills:
            tags = [s.strip() for s in skills.split(",") if s.strip()][:6]
            skills_html = "".join(f'<span class="skill-tag">{t}</span>' for t in tags)

        meta_parts = []
        if job["experience"]: meta_parts.append(f'<span class="meta-item">⚡ {job["experience"]}</span>')
        if job["job_type"]:   meta_parts.append(f'<span class="meta-item">🏷 {job["job_type"]}</span>')
        if job["salary"]:     meta_parts.append(f'<span class="meta-item">💰 {job["salary"]}</span>')
        if job["category"]:   meta_parts.append(f'<span class="meta-item">📂 {job["category"]}</span>')
        meta_html = "".join(meta_parts)

        desc = job["description"][:200].strip()
        if len(job["description"]) > 200:
            desc += "…"

        cards_html += f"""
        <article class="job-card">
            <div class="job-card-header">
                <div class="job-info">
                    <h2 class="job-title">{job['title']}</h2>
                    <div class="job-company">{job['company']}</div>
                    <div class="job-location">📍 {job['location']}</div>
                </div>
                {f'<div class="date-badge">{date_str}</div>' if date_str else ''}
            </div>
            {f'<div class="job-meta">{meta_html}</div>' if meta_parts else ''}
            {f'<p class="job-desc">{desc}</p>' if desc else ''}
            {f'<div class="skills-row">{skills_html}</div>' if skills_html else ''}
            <div class="card-footer">
                <a href="{apply_link}" target="_blank" class="apply-btn">
                    Jetzt bewerben →
                </a>
            </div>
        </article>
"""

    filter_info = ""
    if keyword: filter_info += f'<span class="filter-tag">🔍 {keyword}</span>'
    if location: filter_info += f'<span class="filter-tag">📍 {location}</span>'

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MyCareernet Jobs{' – ' + keyword if keyword else ''}</title>
<style>
  :root {{
    --bg:         #0d0f14;
    --bg2:        #161920;
    --bg3:        #1e2129;
    --border:     #2a2d38;
    --accent:     #0ea5e9;
    --accent2:    #38bdf8;
    --text:       #e2e8f0;
    --text-muted: #64748b;
    --success:    #10b981;
    --tag-bg:     #1e3a4a;
    --tag-text:   #7dd3fc;
    --radius:     12px;
    --font:       'DM Sans', system-ui, sans-serif;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }}

  /* ── Header ── */
  .header {{
    background: linear-gradient(135deg, #0a1628 0%, #0d1f3c 50%, #091420 100%);
    border-bottom: 1px solid var(--border);
    padding: 40px 24px 32px;
    text-align: center;
    position: relative;
    overflow: hidden;
  }}
  .header::before {{
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse 80% 60% at 50% 0%, rgba(14,165,233,.15) 0%, transparent 70%);
    pointer-events: none;
  }}
  .logo-row {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin-bottom: 8px;
  }}
  .logo-dot {{
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent);
    animation: pulse 2s ease-in-out infinite;
  }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}

  h1 {{
    font-size: clamp(1.6rem, 4vw, 2.4rem);
    font-weight: 700;
    letter-spacing: -.02em;
    color: #fff;
  }}
  h1 span {{ color: var(--accent2); }}

  .header-sub {{
    margin-top: 6px;
    color: var(--text-muted);
    font-size: .9rem;
  }}

  /* ── Filter-Tags ── */
  .filters-row {{
    display: flex;
    justify-content: center;
    gap: 8px;
    margin-top: 16px;
    flex-wrap: wrap;
  }}
  .filter-tag {{
    background: rgba(14,165,233,.15);
    border: 1px solid rgba(14,165,233,.3);
    color: var(--accent2);
    padding: 4px 14px;
    border-radius: 999px;
    font-size: .82rem;
    font-weight: 500;
  }}

  /* ── Stats Bar ── */
  .stats-bar {{
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 20px;
    font-size: .85rem;
    color: var(--text-muted);
    flex-wrap: wrap;
  }}
  .stat {{ display: flex; align-items: center; gap: 6px; }}
  .stat strong {{ color: var(--text); font-weight: 600; }}

  /* ── Layout ── */
  .container {{ max-width: 960px; margin: 0 auto; padding: 32px 16px; }}

  /* ── Job Card ── */
  .job-card {{
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px 22px;
    margin-bottom: 14px;
    transition: border-color .2s, transform .15s, box-shadow .2s;
    position: relative;
  }}
  .job-card:hover {{
    border-color: rgba(14,165,233,.4);
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,.4);
  }}
  .job-card-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 10px;
  }}
  .job-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: #fff;
    line-height: 1.35;
    margin-bottom: 4px;
  }}
  .job-company {{
    font-size: .85rem;
    color: var(--accent2);
    font-weight: 500;
    margin-bottom: 3px;
  }}
  .job-location {{
    font-size: .82rem;
    color: var(--text-muted);
  }}
  .date-badge {{
    background: var(--bg3);
    border: 1px solid var(--border);
    color: var(--text-muted);
    font-size: .75rem;
    padding: 3px 10px;
    border-radius: 6px;
    white-space: nowrap;
    flex-shrink: 0;
  }}

  /* ── Meta-Row ── */
  .job-meta {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }}
  .meta-item {{
    font-size: .78rem;
    color: var(--text-muted);
    background: var(--bg3);
    padding: 2px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
  }}

  /* ── Description ── */
  .job-desc {{
    font-size: .85rem;
    color: #94a3b8;
    line-height: 1.55;
    margin-bottom: 10px;
  }}

  /* ── Skills ── */
  .skills-row {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }}
  .skill-tag {{
    background: var(--tag-bg);
    color: var(--tag-text);
    font-size: .75rem;
    padding: 3px 10px;
    border-radius: 6px;
    border: 1px solid rgba(56,189,248,.15);
  }}

  /* ── Footer ── */
  .card-footer {{ display: flex; justify-content: flex-end; }}
  .apply-btn {{
    background: linear-gradient(135deg, var(--accent), #0284c7);
    color: #fff;
    text-decoration: none;
    padding: 8px 20px;
    border-radius: 8px;
    font-size: .83rem;
    font-weight: 600;
    letter-spacing: .01em;
    transition: opacity .2s, transform .15s;
  }}
  .apply-btn:hover {{ opacity: .85; transform: translateY(-1px); }}

  /* ── Empty State ── */
  .empty {{ text-align: center; padding: 80px 20px; color: var(--text-muted); }}
  .empty .emoji {{ font-size: 3rem; margin-bottom: 12px; }}

  /* ── Footer ── */
  .page-footer {{
    text-align: center;
    padding: 32px;
    color: var(--text-muted);
    font-size: .78rem;
    border-top: 1px solid var(--border);
  }}
  .page-footer a {{ color: var(--accent2); text-decoration: none; }}
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>

<header class="header">
  <div class="logo-row">
    <div class="logo-dot"></div>
    <span style="font-size:.8rem;color:var(--text-muted);letter-spacing:.1em;text-transform:uppercase">MyCareernet</span>
  </div>
  <h1>Jobs in <span>India</span></h1>
  <p class="header-sub">Automatisch gescrapt · Letzte Aktualisierung: {now}</p>
  {f'<div class="filters-row">{filter_info}</div>' if filter_info else ''}
</header>

<div class="stats-bar">
  <div class="stat">📋 <strong>{len(normalized)}</strong> Jobs geladen</div>
  <div class="stat">🕒 Stand: <strong>{now}</strong></div>
  <div class="stat" style="margin-left:auto">
    <a href="https://mycareernet.co/mycareernet/jobs" target="_blank"
       style="color:var(--accent2);text-decoration:none;font-size:.82rem">
      Quelle: mycareernet.co ↗
    </a>
  </div>
</div>

<main class="container">
  {''.join([cards_html]) if normalized else '<div class="empty"><div class="emoji">🔍</div><p>Keine Jobs gefunden.</p></div>'}
</main>

<footer class="page-footer">
  Generiert von <a href="https://mycareernet.co/mycareernet/jobs" target="_blank">MyCareernet Scraper</a>
  &nbsp;·&nbsp; {now}
</footer>

</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MyCareernet Job Scraper")
    parser.add_argument("--discover",  action="store_true", help="Playwright-Mode: API-Payload entdecken")
    parser.add_argument("--keyword",   default="",          help="Such-Keyword (z.B. BASF)")
    parser.add_argument("--location",  default="",          help="Standort (z.B. India)")
    parser.add_argument("--pages",     type=int, default=5, help="Max. Seiten (default: 5)")
    parser.add_argument("--output",    default=str(OUTPUT_HTML), help="HTML-Output-Datei")
    args = parser.parse_args()

    # ── 1. Payload bestimmen ──────────────────────────────────────────────────
    if args.discover or not PAYLOAD_CACHE.exists():
        print("[INFO] Starte Discover-Modus (Playwright)...")
        captured = discover_payload(args.keyword, args.location)
        payload  = captured["payload"]
    else:
        print(f"[INFO] Nutze gecachten Payload aus {PAYLOAD_CACHE}")
        cached  = json.loads(PAYLOAD_CACHE.read_text(encoding="utf-8"))
        payload = cached.get("payload", build_default_payload(args.keyword, args.location))
        if args.keyword or args.location:
            payload = patch_payload(payload, args.keyword, args.location)

    print(f"[INFO] Payload: {json.dumps(payload, indent=2)}")

    # ── 2. Jobs abrufen ───────────────────────────────────────────────────────
    jobs = fetch_jobs(payload, max_pages=args.pages)

    if not jobs:
        print("[WARN] Keine Jobs gefunden. Speichere trotzdem leeres HTML.")

    # ── 3. HTML generieren ────────────────────────────────────────────────────
    html = generate_html(jobs, keyword=args.keyword, location=args.location)
    out  = Path(args.output)
    out.write_text(html, encoding="utf-8")
    print(f"[DONE] ✅ {len(jobs)} Jobs → {out} ({out.stat().st_size // 1024} KB)")

    # Debug: Rohdaten speichern
    Path("jobs_raw.json").write_text(
        json.dumps(jobs[:5], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[DONE] Erste 5 Jobs (Rohdaten) → jobs_raw.json")


if __name__ == "__main__":
    main()
