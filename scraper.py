"""
MyCareernet Job Scraper
========================
API:     POST https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/
Auth:    X-AUTH-TOKEN Header (wird per Playwright vom echten Browser-Request geklaut)
"""

import argparse, json, re, sys, time, requests
from datetime import datetime
from pathlib import Path

API_URL     = "https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/"
PORTAL_BASE = "https://mycareernet.co/mycareernet/jobs"
CACHE_FILE  = Path("session_cache.json")
OUTPUT_HTML = Path("index.html")
RAW_JSON    = Path("jobs_raw.json")

# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 1: Token + Payload per Playwright abfangen
# ══════════════════════════════════════════════════════════════════════════════

def get_session(location="India") -> dict:
    """
    Öffnet die Jobs-Seite im Headless-Browser.
    Fängt X-AUTH-TOKEN + echten POST-Body vom getAll-Request ab.
    Gibt {"token": "...", "payload": {...}, "extra_headers": {...}} zurück.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] playwright nicht installiert")
        sys.exit(1)

    start_url = f"{PORTAL_BASE}/listings/jobs-in-{location}?page=1"
    captured  = {}

    print(f"[PLAYWRIGHT] Öffne: {start_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/146.0.7680.166 Safari/537.36 Edg/146.0.3856.84",
        )
        page = ctx.new_page()

        def on_request(req):
            if "getAll" in req.url and req.method == "POST":
                headers = dict(req.headers)
                token   = headers.get("x-auth-token") or headers.get("X-AUTH-TOKEN", "")
                body    = req.post_data or "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}

                if token:
                    captured["token"]         = token
                    captured["payload"]       = payload
                    captured["extra_headers"] = headers
                    print(f"[PLAYWRIGHT] ✅ X-AUTH-TOKEN: {token[:40]}...")
                    print(f"[PLAYWRIGHT]    Payload:      {json.dumps(payload)[:150]}")
                else:
                    print(f"[PLAYWRIGHT] ⚠️  Request ohne Token abgefangen, Headers: {list(headers.keys())}")

        page.on("request", on_request)

        try:
            page.goto(start_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"[PLAYWRIGHT] Timeout/Fehler: {e} — warte trotzdem auf Requests...")

        # Extra warten falls networkidle zu früh getriggert
        time.sleep(4)
        browser.close()

    if not captured.get("token"):
        print("[PLAYWRIGHT] ❌ Kein Token gefunden. Seite möglicherweise geblockt.")
        sys.exit(1)

    captured["captured_at"] = datetime.now().isoformat()
    CACHE_FILE.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print(f"[PLAYWRIGHT] Session gespeichert → {CACHE_FILE}")
    return captured


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 2: API direkt aufrufen (mit Token)
# ══════════════════════════════════════════════════════════════════════════════

def build_payload(base: dict, keyword: str, location: str, exp: str, page: int) -> dict:
    """Nimmt den abgefangenen Payload und überschreibt Filter + Seite."""
    p = base.copy()

    # Seite setzen
    for k in ["pageNo", "page", "pageNum"]:
        if k in p:
            p[k] = page
            break
    else:
        p["pageNo"] = page

    # Keyword
    if keyword:
        for k in ["keyword", "searchText", "query", "q"]:
            if k in p:
                p[k] = keyword
                break
        else:
            p["keyword"] = keyword

    # Location
    if location:
        for k in ["location", "city", "locations"]:
            if k in p:
                p[k] = [location] if isinstance(p[k], list) else location
                break

    # Experience (Format: "0_5")
    if exp:
        mn, mx = _parse_exp(exp)
        for k in ["experience"]:
            if k in p:
                p[k] = exp
                break
        for k in ["minExp", "min_exp"]:
            if k in p:
                p[k] = mn
        for k in ["maxExp", "max_exp"]:
            if k in p:
                p[k] = mx

    return p


def _parse_exp(s):
    m = re.match(r'^(\d+)_(\d+)$', s.strip())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 99)


def fetch_jobs(session: dict, keyword: str, location: str, exp: str, max_pages: int) -> list:
    token        = session["token"]
    base_payload = session.get("payload", {"pageNo": 1, "pageSize": 20})
    extra_hdrs   = session.get("extra_headers", {})

    # Basis-Headers: alles was der Browser mitschickte, plus unsere Overrides
    headers = {
        "User-Agent":      extra_hdrs.get("user-agent", "Mozilla/5.0"),
        "Content-Type":    "application/json",
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": extra_hdrs.get("accept-language", "de,en-US;q=0.9"),
        "Origin":          "https://mycareernet.co",
        "Referer":         f"{PORTAL_BASE}/listings/jobs-in-{location or 'India'}?page=1",
        "X-AUTH-TOKEN":    token,
        "sec-fetch-dest":  "empty",
        "sec-fetch-mode":  "cors",
        "sec-fetch-site":  "same-origin",
    }
    # Weitere Custom-Headers aus dem Browser-Request übernehmen
    for k, v in extra_hdrs.items():
        if k.lower().startswith("x-") and k.lower() != "x-auth-token":
            headers[k] = v

    session_r = requests.Session()
    session_r.headers.update(headers)

    all_jobs = []
    print(f"[FETCH] Start — Token: {token[:30]}...")
    print(f"[FETCH] Max {max_pages} Seiten, Keyword='{keyword}', Location='{location}'")

    for page in range(1, max_pages + 1):
        payload = build_payload(base_payload, keyword, location, exp, page)

        try:
            resp = session_r.post(API_URL, json=payload, timeout=15)
            print(f"[FETCH] Seite {page}: HTTP {resp.status_code}", end="")

            if resp.status_code == 400:
                err = resp.json().get("error", {})
                print(f" → {err}")
                if "TOKEN" in str(err):
                    print("[FETCH] Token abgelaufen — bitte erneut starten")
                break

            if resp.status_code != 200:
                print(f" → {resp.text[:100]}")
                break

            data  = resp.json()
            jobs  = _extract(data)
            total = _total(data)

            if not jobs:
                print(" → keine Jobs mehr")
                break

            all_jobs.extend(jobs)
            print(f" → +{len(jobs)} (Gesamt: {len(all_jobs)}/{total or '?'})")

            if total and len(all_jobs) >= total:
                break

            time.sleep(0.6)

        except Exception as e:
            print(f"\n[FETCH] Fehler: {e}")
            break

    print(f"[FETCH] ✅ {len(all_jobs)} Jobs")
    return all_jobs


def _extract(data):
    if isinstance(data, list):
        return data
    for k in ["data","jobs","results","jobList","jobListings","response","content","items","records","jobsData"]:
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for ik in ["data","jobs","results","list","records"]:
                if isinstance(v.get(ik), list):
                    return v[ik]
    return []


def _total(data):
    if not isinstance(data, dict):
        return None
    for k in ["total","totalCount","totalRecords","count","totalJobs","totalResults"]:
        if k in data:
            return int(data[k])
        if isinstance(data.get("data"), dict) and k in data["data"]:
            return int(data["data"][k])
    return None


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 3: Normalisierung + HTML
# ══════════════════════════════════════════════════════════════════════════════

def normalize(r):
    def g(*keys, default=""):
        for k in keys:
            v = r.get(k)
            if v and str(v).strip(): return str(v).strip()
        return default
    skills = ""
    for k in ["skills","keySkills","key_skills","tags","skillSet","requiredSkills"]:
        v = r.get(k)
        if isinstance(v, list) and v:  skills = ", ".join(str(s) for s in v if s)[:200]; break
        if isinstance(v, str) and v:   skills = v[:200]; break
    return {
        "title":      g("title","jobTitle","job_title","designation","position", default="N/A"),
        "company":    g("company","companyName","company_name","employer","organization"),
        "location":   g("location","jobLocation","city","cities","place", default="India"),
        "experience": g("experience","experienceRequired","exp","minExperience","experience_range"),
        "salary":     g("salary","salaryRange","ctc","compensation","package"),
        "posted":     g("postedDate","posted_date","createdAt","publishedDate","postDate"),
        "skills":     skills,
        "desc":       g("description","jobDescription","shortDescription","jobSummary","overview")[:300],
        "apply_url":  g("applyUrl","apply_url","jobUrl","url","link","applicationUrl","redirect_url"),
        "type":       g("jobType","employmentType","type","workType"),
        "category":   g("category","function","domain","department","industry"),
    }


def fmt_date(s):
    if not s: return ""
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%d %H:%M:%S","%Y-%m-%d","%d/%m/%Y"]:
        try: return datetime.strptime(s[:26], fmt).strftime("%d. %b %Y")
        except: pass
    return s[:10]


def generate_html(jobs, keyword="", location="", exp=""):
    items = [normalize(j) for j in jobs]
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")

    filters = ""
    if keyword:  filters += f'<span class="fb">&#x1F50D; {keyword}</span>'
    if location: filters += f'<span class="fb">&#x1F4CD; {location}</span>'
    if exp:
        mn, mx = _parse_exp(exp)
        filters += f'<span class="fb">&#x26A1; {mn}&#x2013;{mx} Jahre</span>'

    cards = ""
    for job in items:
        date     = fmt_date(job["posted"])
        url      = job["apply_url"] or PORTAL_BASE
        tags     = [t.strip() for t in job["skills"].split(",") if t.strip()][:6]
        tag_html = "".join(f'<span class="st">{t}</span>' for t in tags)
        metas    = []
        if job["experience"]: metas.append(f'<span class="mt">&#x26A1; {job["experience"]}</span>')
        if job["type"]:       metas.append(f'<span class="mt">&#x1F3F7; {job["type"]}</span>')
        if job["salary"]:     metas.append(f'<span class="mt">&#x1F4B0; {job["salary"]}</span>')
        if job["category"]:   metas.append(f'<span class="mt">&#x1F4C2; {job["category"]}</span>')
        cards += f"""
<article class="card">
  <div class="ct">
    <div>
      <div class="tt">{job['title']}</div>
      <div class="co">{job['company']}</div>
      <div class="lo">&#x1F4CD; {job['location']}</div>
    </div>
    {'<div class="db">' + date + '</div>' if date else ''}
  </div>
  {'<div class="ms">' + ''.join(metas) + '</div>' if metas else ''}
  {'<p class="ds">' + job["desc"] + ('...' if len(job["desc"])==300 else '') + '</p>' if job["desc"] else ''}
  {'<div class="sg">' + tag_html + '</div>' if tag_html else ''}
  <div class="cf"><a href="{url}" target="_blank" class="btn">Jetzt bewerben &#x2192;</a></div>
</article>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCareernet Jobs{' - '+keyword if keyword else ''}</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#080c12;--bg2:#0f1520;--bg3:#161e2e;--ln:#1e2d42;--a:#3b82f6;--a2:#60a5fa;--tx:#e2e8f0;--mu:#4a6080;--tg:#1a3050;--tc:#7eb8f0}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',system-ui,sans-serif;background:var(--bg);color:var(--tx);min-height:100vh}}
.hdr{{background:linear-gradient(160deg,#060e1c,#0a1930 60%,#060d18);border-bottom:1px solid var(--ln);padding:48px 24px 36px;text-align:center;position:relative;overflow:hidden}}
.hdr::after{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 70% 50% at 50% -10%,rgba(59,130,246,.18),transparent 70%);pointer-events:none}}
.src{{display:inline-flex;align-items:center;gap:8px;font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mu);margin-bottom:14px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--a);box-shadow:0 0 10px var(--a);animation:blink 2s ease-in-out infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
h1{{font-family:'Syne',system-ui,sans-serif;font-size:clamp(1.8rem,5vw,2.8rem);font-weight:800;letter-spacing:-.03em;color:#fff}}
h1 em{{color:var(--a2);font-style:normal}}
.sub{{margin-top:8px;color:var(--mu);font-size:.85rem}}
.frow{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:18px}}
.fb{{background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.25);color:var(--a2);padding:4px 14px;border-radius:999px;font-size:.78rem;font-weight:600}}
.stats{{background:var(--bg2);border-bottom:1px solid var(--ln);padding:11px 24px;display:flex;gap:20px;flex-wrap:wrap;font-size:.8rem;color:var(--mu);align-items:center}}
.stats strong{{color:var(--tx)}}.stats .sl{{margin-left:auto}}.stats .sl a{{color:var(--a2);text-decoration:none}}
.wrap{{max-width:980px;margin:0 auto;padding:28px 14px 60px}}
.card{{background:var(--bg2);border:1px solid var(--ln);border-radius:10px;padding:18px 20px;margin-bottom:12px;transition:border-color .2s,box-shadow .2s,transform .15s}}
.card:hover{{border-color:rgba(59,130,246,.35);box-shadow:0 6px 28px rgba(0,0,0,.45);transform:translateY(-2px)}}
.ct{{display:flex;justify-content:space-between;gap:12px;margin-bottom:8px}}
.tt{{font-family:'Syne',system-ui,sans-serif;font-size:1rem;font-weight:700;color:#fff;margin-bottom:3px}}
.co{{font-size:.82rem;color:var(--a2);font-weight:600;margin-bottom:2px}}
.lo{{font-size:.78rem;color:var(--mu)}}
.db{{background:var(--bg3);border:1px solid var(--ln);color:var(--mu);font-size:.72rem;padding:3px 10px;border-radius:6px;white-space:nowrap;flex-shrink:0;height:fit-content}}
.ms{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:9px}}
.mt{{font-size:.74rem;color:var(--mu);background:var(--bg3);border:1px solid var(--ln);padding:2px 9px;border-radius:5px}}
.ds{{font-size:.82rem;color:#6b8aaa;line-height:1.55;margin-bottom:10px}}
.sg{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}}
.st{{background:var(--tg);color:var(--tc);font-size:.72rem;padding:2px 9px;border-radius:5px;border:1px solid rgba(96,165,250,.12)}}
.cf{{display:flex;justify-content:flex-end}}
.btn{{background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;text-decoration:none;padding:7px 18px;border-radius:7px;font-size:.8rem;font-weight:600;transition:opacity .2s,transform .15s}}
.btn:hover{{opacity:.85;transform:translateY(-1px)}}
.empty{{text-align:center;padding:80px 20px;color:var(--mu)}}
footer{{text-align:center;padding:28px;color:var(--mu);font-size:.75rem;border-top:1px solid var(--ln)}}
footer a{{color:var(--a2);text-decoration:none}}
</style>
</head>
<body>
<header class="hdr">
  <div class="src"><span class="dot"></span>MyCareernet &#xB7; Live Scraper</div>
  <h1>Jobs in <em>India</em></h1>
  <p class="sub">Automatisch gescrapt &#xB7; Stand: {now}</p>
  {('<div class="frow">'+filters+'</div>') if filters else ''}
</header>
<div class="stats">
  <span>&#x1F4CB; <strong>{len(items)}</strong> Jobs</span>
  <span>&#x1F552; <strong>{now}</strong></span>
  <span class="sl"><a href="{PORTAL_BASE}" target="_blank">mycareernet.co &#x2197;</a></span>
</div>
<main class="wrap">
{cards if items else '<div class="empty"><p>Keine Jobs gefunden.</p></div>'}
</main>
<footer><a href="{PORTAL_BASE}" target="_blank">MyCareernet</a> &#xB7; {now}</footer>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword",  default="")
    ap.add_argument("--location", default="India")
    ap.add_argument("--exp",      default="")
    ap.add_argument("--pages",    type=int, default=10)
    ap.add_argument("--output",   default=str(OUTPUT_HTML))
    args = ap.parse_args()

    # Immer frischen Token holen (Playwright)
    session = get_session(location=args.location)

    # Jobs scrapen
    jobs = fetch_jobs(session, args.keyword, args.location, args.exp, args.pages)

    # HTML + Rohdaten speichern
    html = generate_html(jobs, keyword=args.keyword, location=args.location, exp=args.exp)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"[DONE] {len(jobs)} Jobs -> {args.output}")

    RAW_JSON.write_text(json.dumps(jobs[:10], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[DONE] Rohdaten -> {RAW_JSON}")


if __name__ == "__main__":
    main()
