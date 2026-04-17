"""
MyCareernet Job Scraper  
========================
API:  POST https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/

URL-Struktur (aus GA-Analyse):
  /mycareernet/jobs/listings/{count}-jobs-in-{City}?experience={min}_{max}&page={n}
  Beispiel: /listings/1-jobs-in-Hyderabad?experience=0_5&page=1

Erkannte Parameter:
  - location:    City-Name (z.B. "Hyderabad", "India")
  - experience:  "min_max" Format (z.B. "0_5", "5_10")
  - page:        Seitennummer (1-basiert)

Usage:
  python scraper.py                              # Alle Jobs, India
  python scraper.py --keyword BASF               # Nur BASF-Jobs
  python scraper.py --location Hyderabad         # Nur Hyderabad
  python scraper.py --exp 0_5                    # 0-5 Jahre Erfahrung
  python scraper.py --probe                      # API-Payload-Finder (lokal testen)
  python scraper.py --discover                   # Playwright-Interception
"""

import argparse
import json
import re
import sys
import time
import requests
from datetime import datetime
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# KONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

API_URL       = "https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/"
PORTAL_BASE   = "https://mycareernet.co/mycareernet/jobs"
PAYLOAD_CACHE = Path("discovered_payload.json")
OUTPUT_HTML   = Path("index.html")
RAW_JSON      = Path("jobs_raw.json")

SESSION_HEADERS = {
    "User-Agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/146.0.7680.166 Safari/537.36 Edg/146.0.3856.84",
    "Content-Type":       "application/json",
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "de,en-US;q=0.9,en;q=0.8",
    "Origin":             "https://mycareernet.co",
    "Referer":            "https://mycareernet.co/mycareernet/jobs/listings/jobs-in-India?page=1",
    "sec-ch-ua":          '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-origin",
}


# ══════════════════════════════════════════════════════════════════════════════
# PAYLOAD-BAUSTEINE
# ══════════════════════════════════════════════════════════════════════════════

def parse_experience(exp_str: str) -> tuple:
    """'0_5' -> (0, 5) | '5_10' -> (5, 10) | '' -> (0, 99)"""
    if not exp_str:
        return (0, 99)
    m = re.match(r'^(\d+)_(\d+)$', exp_str.strip())
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, 99)


def build_payload_variants(keyword, location, exp, page=1):
    """
    Generiert alle wahrscheinlichen Payload-Varianten basierend auf URL-Analyse.
    Die erste die funktioniert, wird gecacht.
    """
    min_exp, max_exp = parse_experience(exp)
    loc_list  = [location] if location else []
    loc_str   = location or ""
    exp_range = f"{min_exp}_{max_exp}" if exp else ""

    return [
        # Variante 1: Standard HirePro / MyCareernet
        {
            "keyword":    keyword,
            "location":   loc_str,
            "experience": exp_range,
            "pageNo":     page,
            "pageSize":   20,
            "sortBy":     "latest",
        },
        # Variante 2: Array-Location + Objekt-Experience
        {
            "keyword":    keyword,
            "location":   loc_list,
            "experience": {"min": min_exp, "max": max_exp} if exp else {},
            "pageNo":     page,
            "pageSize":   20,
        },
        # Variante 3: filters-Wrapper
        {
            "keyword": keyword,
            "filters": {
                "location":   loc_list,
                "experience": [exp_range] if exp_range else [],
            },
            "pageNo":   page,
            "pageSize": 20,
        },
        # Variante 4: searchText statt keyword
        {
            "searchText": keyword,
            "city":       loc_str,
            "minExp":     min_exp,
            "maxExp":     max_exp,
            "page":       page,
            "limit":      20,
        },
        # Variante 5: Alles als Strings
        {
            "keyword":   keyword,
            "location":  loc_str,
            "minExp":    str(min_exp),
            "maxExp":    str(max_exp),
            "page":      page,
            "pageSize":  20,
            "sortOrder": "desc",
        },
        # Variante 6: Nur Paginierung (alle Jobs ohne Filter)
        {
            "pageNo":   page,
            "pageSize": 20,
        },
    ]


# ══════════════════════════════════════════════════════════════════════════════
# PROBE-MODUS: Richtige Payload-Variante ermitteln
# ══════════════════════════════════════════════════════════════════════════════

def probe_api(keyword, location, exp):
    """
    Testet alle Payload-Varianten gegen die echte API.
    Gibt die erste funktionierende zurueck und speichert sie als Cache.
    """
    session  = requests.Session()
    session.headers.update(SESSION_HEADERS)
    variants = build_payload_variants(keyword, location, exp, page=1)

    print(f"[PROBE] Teste {len(variants)} Payload-Varianten gegen {API_URL}")
    print(f"[PROBE] Keyword='{keyword}' Location='{location}' Exp='{exp}'")
    print()

    for i, payload in enumerate(variants, 1):
        print(f"[PROBE] Variante {i}: {json.dumps(payload)}")
        try:
            resp = session.post(API_URL, json=payload, timeout=15)
            print(f"         -> HTTP {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                jobs = extract_jobs(data)
                total = get_total(data)
                print(f"         FUNKTIONIERT! Jobs: {len(jobs)}, Total: {total}")
                print(f"         Response-Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                if jobs:
                    print(f"         Erster Job Keys: {list(jobs[0].keys())[:10]}")

                cache = {
                    "variant":     i,
                    "payload":     payload,
                    "sample_keys": list(jobs[0].keys()) if jobs else [],
                    "total_jobs":  total,
                    "discovered":  datetime.now().isoformat(),
                }
                PAYLOAD_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
                print(f"\n[PROBE] Variante {i} gecacht -> {PAYLOAD_CACHE}")
                return cache
            else:
                print(f"         Body: {resp.text[:150]}")

        except Exception as e:
            print(f"         ERROR {type(e).__name__}: {e}")
        print()

    print("[PROBE] Keine Variante hat funktioniert.")
    print("[PROBE] Versuche: python scraper.py --discover")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT DISCOVER-MODUS
# ══════════════════════════════════════════════════════════════════════════════

def discover_via_playwright(keyword, location, exp):
    """Oeffnet die Seite im Headless-Browser und faengt den echten API-Request ab."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] Playwright fehlt: pip install playwright && playwright install chromium")
        sys.exit(1)

    captured = {}
    start_url = f"{PORTAL_BASE}/listings/jobs-in-{location or 'India'}?page=1"
    if exp:
        start_url += f"&experience={exp}"

    print(f"[DISCOVER] Oeffne: {start_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=SESSION_HEADERS["User-Agent"],
            extra_http_headers={"Accept-Language": "de,en-US;q=0.9,en;q=0.8"},
        )
        page = ctx.new_page()

        def on_request(request):
            if "getAll" in request.url and request.method == "POST":
                try:
                    body_raw = request.post_data
                    if body_raw:
                        payload = json.loads(body_raw)
                        captured["url"]     = request.url
                        captured["payload"] = payload
                        captured["headers"] = dict(request.headers)
                        print(f"[DISCOVER] API-Payload abgefangen!")
                        print(f"           URL:     {request.url}")
                        print(f"           Payload: {json.dumps(payload, indent=2)}")
                except Exception as e:
                    print(f"[DISCOVER] Parse-Fehler: {e}")

        page.on("request", on_request)
        page.goto(start_url, wait_until="networkidle", timeout=30000)
        time.sleep(3)
        browser.close()

    if not captured:
        print("[DISCOVER] Kein getAll-Request gefangen. Versuche Probe-Modus...")
        return probe_api(keyword, location, exp)

    captured["discovered"] = datetime.now().isoformat()
    PAYLOAD_CACHE.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print(f"[DISCOVER] Gespeichert -> {PAYLOAD_CACHE}")
    return captured


# ══════════════════════════════════════════════════════════════════════════════
# API-FETCH MIT PAGINATION
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_jobs(base_payload, max_pages=10):
    """Paginiert durch alle Seiten und sammelt Jobs."""
    session = requests.Session()
    session.headers.update(SESSION_HEADERS)

    all_jobs  = []
    page_key  = next((k for k in ["pageNo", "page", "pageNum"] if k in base_payload), "pageNo")
    size_key  = next((k for k in ["pageSize", "limit", "size"] if k in base_payload), "pageSize")
    page_size = base_payload.get(size_key, 20)

    print(f"[FETCH] Start - max {max_pages} Seiten x {page_size} Jobs")

    for page_num in range(1, max_pages + 1):
        payload = {**base_payload, page_key: page_num}
        try:
            resp = session.post(API_URL, json=payload, timeout=15)
            print(f"[FETCH] Seite {page_num}: HTTP {resp.status_code}", end="")

            if resp.status_code != 200:
                print(f" -> {resp.text[:100]}")
                break

            data = resp.json()
            jobs = extract_jobs(data)
            total = get_total(data)

            if not jobs:
                print(" -> keine Jobs mehr")
                break

            all_jobs.extend(jobs)
            print(f" -> +{len(jobs)} (Gesamt: {len(all_jobs)}/{total or '?'})")

            if total and len(all_jobs) >= total:
                print("[FETCH] Alle Jobs geladen.")
                break

            time.sleep(0.7)

        except Exception as e:
            print(f"\n[FETCH] Fehler auf Seite {page_num}: {e}")
            break

    print(f"[FETCH] Fertig: {len(all_jobs)} Jobs total")
    return all_jobs


def extract_jobs(data):
    if isinstance(data, list):
        return data
    for key in ["data", "jobs", "results", "jobList", "jobListings",
                "response", "content", "items", "records", "jobsData"]:
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for inner in ["data", "jobs", "results", "list", "records"]:
                if isinstance(val.get(inner), list):
                    return val[inner]
    return []


def get_total(data):
    if not isinstance(data, dict):
        return None
    for key in ["total", "totalCount", "totalRecords", "count", "totalJobs", "totalResults"]:
        if key in data:
            return int(data[key])
        if isinstance(data.get("data"), dict) and key in data["data"]:
            return int(data["data"][key])
    return None


# ══════════════════════════════════════════════════════════════════════════════
# NORMALISIERUNG
# ══════════════════════════════════════════════════════════════════════════════

def normalize(raw):
    def g(*keys, default=""):
        for k in keys:
            v = raw.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return default

    skills = ""
    for k in ["skills", "keySkills", "key_skills", "tags", "skillSet", "requiredSkills"]:
        v = raw.get(k)
        if isinstance(v, list) and v:
            skills = ", ".join(str(s).strip() for s in v if s)[:200]
            break
        if isinstance(v, str) and v:
            skills = v[:200]
            break

    return {
        "id":         g("id", "jobId", "job_id", "_id", "jobCode"),
        "title":      g("title", "jobTitle", "job_title", "designation", "position", default="N/A"),
        "company":    g("company", "companyName", "company_name", "employer", "organization"),
        "location":   g("location", "jobLocation", "city", "cities", "place", default="India"),
        "experience": g("experience", "experienceRequired", "exp", "minExperience",
                        "experience_range", "reqExperience"),
        "salary":     g("salary", "salaryRange", "ctc", "compensation", "package"),
        "posted":     g("postedDate", "posted_date", "createdAt", "publishedDate",
                        "postDate", "updatedAt"),
        "skills":     skills,
        "desc":       g("description", "jobDescription", "shortDescription",
                        "jobSummary", "overview")[:300],
        "apply_url":  g("applyUrl", "apply_url", "jobUrl", "url", "link",
                        "applicationUrl", "redirect_url"),
        "type":       g("jobType", "employmentType", "type", "workType"),
        "category":   g("category", "function", "domain", "department", "industry"),
    }


def fmt_date(s):
    if not s:
        return ""
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"]:
        try:
            return datetime.strptime(s[:26], fmt).strftime("%d. %b %Y")
        except Exception:
            pass
    return s[:10]


# ══════════════════════════════════════════════════════════════════════════════
# HTML-GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_html(jobs, keyword="", location="", exp=""):
    items = [normalize(j) for j in jobs]
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")

    filters = ""
    if keyword:  filters += f'<span class="fbadge">&#x1F50D; {keyword}</span>'
    if location: filters += f'<span class="fbadge">&#x1F4CD; {location}</span>'
    if exp:
        mn, mx = parse_experience(exp)
        filters += f'<span class="fbadge">&#x26A1; {mn}&#x2013;{mx} Jahre</span>'

    cards = ""
    for job in items:
        date     = fmt_date(job["posted"])
        url      = job["apply_url"] or PORTAL_BASE
        tags     = [t.strip() for t in job["skills"].split(",") if t.strip()][:6]
        tag_html = "".join(f'<span class="stag">{t}</span>' for t in tags)

        metas = []
        if job["experience"]: metas.append(f'<span class="meta">&#x26A1; {job["experience"]}</span>')
        if job["type"]:       metas.append(f'<span class="meta">&#x1F3F7; {job["type"]}</span>')
        if job["salary"]:     metas.append(f'<span class="meta">&#x1F4B0; {job["salary"]}</span>')
        if job["category"]:   metas.append(f'<span class="meta">&#x1F4C2; {job["category"]}</span>')

        cards += f"""
<article class="card">
  <div class="card-top">
    <div class="card-info">
      <div class="title">{job['title']}</div>
      <div class="company">{job['company']}</div>
      <div class="loc">&#x1F4CD; {job['location']}</div>
    </div>
    {'<div class="date">' + date + '</div>' if date else ''}
  </div>
  {'<div class="metas">' + ''.join(metas) + '</div>' if metas else ''}
  {'<p class="desc">' + job["desc"] + ('...' if len(job["desc"]) == 300 else '') + '</p>' if job["desc"] else ''}
  {'<div class="stags">' + tag_html + '</div>' if tag_html else ''}
  <div class="card-foot">
    <a href="{url}" target="_blank" rel="noopener" class="btn">Jetzt bewerben &#x2192;</a>
  </div>
</article>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCareernet Jobs{' - ' + keyword if keyword else ''}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#080c12;--bg2:#0f1520;--bg3:#161e2e;--line:#1e2d42;
  --a:#3b82f6;--a2:#60a5fa;--txt:#e2e8f0;--muted:#4a6080;
  --tag:#1a3050;--tagc:#7eb8f0;--r:10px;
  --ff:'DM Sans',system-ui,sans-serif;--fh:'Syne',system-ui,sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--ff);background:var(--bg);color:var(--txt);min-height:100vh}}
.hdr{{
  background:linear-gradient(160deg,#060e1c 0%,#0a1930 60%,#060d18 100%);
  border-bottom:1px solid var(--line);padding:48px 24px 36px;
  text-align:center;position:relative;overflow:hidden;
}}
.hdr::after{{
  content:'';position:absolute;inset:0;
  background:radial-gradient(ellipse 70% 50% at 50% -10%,rgba(59,130,246,.18) 0%,transparent 70%);
  pointer-events:none;
}}
.source-label{{
  display:inline-flex;align-items:center;gap:8px;
  font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin-bottom:14px;
}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--a);
      box-shadow:0 0 10px var(--a);animation:blink 2s ease-in-out infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
h1{{font-family:var(--fh);font-size:clamp(1.8rem,5vw,2.8rem);
    font-weight:800;letter-spacing:-.03em;color:#fff;line-height:1.1}}
h1 em{{color:var(--a2);font-style:normal}}
.sub{{margin-top:8px;color:var(--muted);font-size:.85rem}}
.filters{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:18px}}
.fbadge{{
  background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.25);
  color:var(--a2);padding:4px 14px;border-radius:999px;font-size:.78rem;font-weight:600;
}}
.stats{{
  background:var(--bg2);border-bottom:1px solid var(--line);
  padding:11px 24px;display:flex;gap:20px;flex-wrap:wrap;
  font-size:.8rem;color:var(--muted);align-items:center;
}}
.stats strong{{color:var(--txt)}}
.stats .src{{margin-left:auto}}
.stats .src a{{color:var(--a2);text-decoration:none}}
.wrap{{max-width:980px;margin:0 auto;padding:28px 14px 60px}}
.card{{
  background:var(--bg2);border:1px solid var(--line);
  border-radius:var(--r);padding:18px 20px;margin-bottom:12px;
  transition:border-color .2s,box-shadow .2s,transform .15s;
}}
.card:hover{{
  border-color:rgba(59,130,246,.35);
  box-shadow:0 6px 28px rgba(0,0,0,.45);
  transform:translateY(-2px);
}}
.card-top{{display:flex;justify-content:space-between;gap:12px;margin-bottom:8px}}
.title{{font-family:var(--fh);font-size:1rem;font-weight:700;color:#fff;margin-bottom:3px}}
.company{{font-size:.82rem;color:var(--a2);font-weight:600;margin-bottom:2px}}
.loc{{font-size:.78rem;color:var(--muted)}}
.date{{
  background:var(--bg3);border:1px solid var(--line);
  color:var(--muted);font-size:.72rem;padding:3px 10px;
  border-radius:6px;white-space:nowrap;flex-shrink:0;height:fit-content;
}}
.metas{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:9px}}
.meta{{
  font-size:.74rem;color:var(--muted);
  background:var(--bg3);border:1px solid var(--line);
  padding:2px 9px;border-radius:5px;
}}
.desc{{font-size:.82rem;color:#6b8aaa;line-height:1.55;margin-bottom:10px}}
.stags{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}}
.stag{{
  background:var(--tag);color:var(--tagc);
  font-size:.72rem;padding:2px 9px;border-radius:5px;
  border:1px solid rgba(96,165,250,.12);
}}
.card-foot{{display:flex;justify-content:flex-end}}
.btn{{
  background:linear-gradient(135deg,#2563eb,#1d4ed8);
  color:#fff;text-decoration:none;padding:7px 18px;
  border-radius:7px;font-size:.8rem;font-weight:600;
  transition:opacity .2s,transform .15s;
}}
.btn:hover{{opacity:.85;transform:translateY(-1px)}}
.empty{{text-align:center;padding:80px 20px;color:var(--muted)}}
footer{{
  text-align:center;padding:28px;color:var(--muted);
  font-size:.75rem;border-top:1px solid var(--line);
}}
footer a{{color:var(--a2);text-decoration:none}}
</style>
</head>
<body>
<header class="hdr">
  <div class="source-label"><span class="dot"></span>MyCareernet &#xB7; Live Scraper</div>
  <h1>Jobs in <em>India</em></h1>
  <p class="sub">Automatisch gescrapt &#xB7; Stand: {now}</p>
  {('<div class="filters">' + filters + '</div>') if filters else ''}
</header>
<div class="stats">
  <span>&#x1F4CB; <strong>{len(items)}</strong> Jobs geladen</span>
  <span>&#x1F552; <strong>{now}</strong></span>
  <span class="src"><a href="{PORTAL_BASE}" target="_blank">mycareernet.co &#x2197;</a></span>
</div>
<main class="wrap">
{cards if items else '<div class="empty"><p>Keine Jobs gefunden.</p></div>'}
</main>
<footer>
  <a href="{PORTAL_BASE}" target="_blank">MyCareernet</a> &#xB7; {now}
</footer>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="MyCareernet Job Scraper")
    ap.add_argument("--keyword",  default="",           help="Suchbegriff (z.B. BASF)")
    ap.add_argument("--location", default="",           help="Stadt (z.B. India, Hyderabad)")
    ap.add_argument("--exp",      default="",           help="Erfahrung min_max (z.B. 0_5, 5_10)")
    ap.add_argument("--pages",    type=int, default=10, help="Max Seiten")
    ap.add_argument("--output",   default=str(OUTPUT_HTML))
    ap.add_argument("--probe",    action="store_true",  help="Alle Payload-Varianten testen")
    ap.add_argument("--discover", action="store_true",  help="Playwright-Interception")
    args = ap.parse_args()

    # 1. Payload ermitteln
    if args.probe:
        result = probe_api(args.keyword, args.location, args.exp)
        if not result:
            sys.exit(1)
        base_payload = result["payload"]

    elif args.discover:
        result = discover_via_playwright(args.keyword, args.location, args.exp)
        if not result:
            sys.exit(1)
        base_payload = result["payload"]

    elif PAYLOAD_CACHE.exists():
        cache = json.loads(PAYLOAD_CACHE.read_text(encoding="utf-8"))
        base_payload = cache["payload"]
        print(f"[INFO] Gecachter Payload (Variante {cache.get('variant', '?')})")

        # Keyword / Location / Exp ueberschreiben
        if args.keyword or args.location or args.exp:
            mn, mx = parse_experience(args.exp)
            for k in ["keyword", "searchText", "query"]:
                if k in base_payload:
                    base_payload[k] = args.keyword
                    break
            else:
                base_payload["keyword"] = args.keyword

            for k in ["location", "city", "locations"]:
                if k in base_payload:
                    base_payload[k] = [args.location] if isinstance(base_payload[k], list) else args.location
                    break

            if args.exp:
                for k in ["experience"]:
                    if k in base_payload:
                        base_payload[k] = args.exp
                        break

    else:
        print("[INFO] Kein Cache - starte Probe-Modus...")
        result = probe_api(args.keyword, args.location, args.exp)
        if not result:
            print("[WARN] Probe fehlgeschlagen - versuche: python scraper.py --discover")
            sys.exit(1)
        base_payload = result["payload"]

    print(f"[INFO] Payload: {json.dumps(base_payload, indent=2)}")

    # 2. Jobs scrapen
    jobs = fetch_all_jobs(base_payload, max_pages=args.pages)

    # 3. HTML + JSON speichern
    html = generate_html(jobs, keyword=args.keyword, location=args.location, exp=args.exp)
    out  = Path(args.output)
    out.write_text(html, encoding="utf-8")
    print(f"[DONE] {len(jobs)} Jobs -> {out}")

    RAW_JSON.write_text(
        json.dumps(jobs[:10], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[DONE] Rohdaten (erste 10) -> {RAW_JSON}")


if __name__ == "__main__":
    main()
