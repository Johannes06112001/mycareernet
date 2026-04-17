"""
MyCareernet Job Scraper
API: POST https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/
"""
import argparse, json, re, sys, time, requests
from datetime import datetime
from pathlib import Path

API_URL       = "https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/"
PORTAL_BASE   = "https://mycareernet.co/mycareernet/jobs"
PAYLOAD_CACHE = Path("discovered_payload.json")
OUTPUT_HTML   = Path("docs/index.html")
RAW_JSON      = Path("docs/jobs_raw.json")

HEADERS = {
    "User-Agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.7680.166 Safari/537.36 Edg/146.0.3856.84",
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

# ── Payload ───────────────────────────────────────────────────────────────────

def parse_exp(s):
    m = re.match(r'^(\d+)_(\d+)$', s.strip()) if s else None
    return (int(m.group(1)), int(m.group(2))) if m else (0, 99)

def variants(keyword, location, exp, page=1):
    mn, mx  = parse_exp(exp)
    loc_str = location or ""
    loc_arr = [location] if location else []
    exp_str = f"{mn}_{mx}" if exp else ""
    return [
        {"keyword": keyword, "location": loc_str, "experience": exp_str, "pageNo": page, "pageSize": 20, "sortBy": "latest"},
        {"keyword": keyword, "location": loc_arr, "experience": {"min": mn, "max": mx} if exp else {}, "pageNo": page, "pageSize": 20},
        {"keyword": keyword, "filters": {"location": loc_arr, "experience": [exp_str] if exp_str else []}, "pageNo": page, "pageSize": 20},
        {"searchText": keyword, "city": loc_str, "minExp": mn, "maxExp": mx, "page": page, "limit": 20},
        {"keyword": keyword, "location": loc_str, "minExp": str(mn), "maxExp": str(mx), "page": page, "pageSize": 20},
        {"pageNo": page, "pageSize": 20},
    ]

# ── Probe ─────────────────────────────────────────────────────────────────────

def probe(keyword, location, exp):
    s = requests.Session()
    s.headers.update(HEADERS)
    print(f"[PROBE] Teste 6 Payload-Varianten...")
    for i, payload in enumerate(variants(keyword, location, exp), 1):
        print(f"  Variante {i}: {json.dumps(payload)}")
        try:
            r = s.post(API_URL, json=payload, timeout=15)
            print(f"  → HTTP {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                jobs = extract_jobs(data)
                total = get_total(data)
                print(f"  ✅ FUNKTIONIERT! Jobs={len(jobs)}, Total={total}")
                if jobs:
                    print(f"  Job-Keys: {list(jobs[0].keys())[:12]}")
                cache = {"variant": i, "payload": payload, "total": total,
                         "sample_keys": list(jobs[0].keys()) if jobs else [],
                         "discovered": datetime.now().isoformat()}
                PAYLOAD_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
                print(f"  Gecacht → {PAYLOAD_CACHE}")
                return cache
            else:
                print(f"  Body: {r.text[:120]}")
        except Exception as e:
            print(f"  Fehler: {e}")
    print("[PROBE] Keine Variante hat funktioniert.")
    return None

# ── Fetch ─────────────────────────────────────────────────────────────────────

def extract_jobs(data):
    if isinstance(data, list): return data
    for k in ["data","jobs","results","jobList","jobListings","content","items","records"]:
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list): return v
        if isinstance(v, dict):
            for ik in ["data","jobs","results","list","records"]:
                if isinstance(v.get(ik), list): return v[ik]
    return []

def get_total(data):
    if not isinstance(data, dict): return None
    for k in ["total","totalCount","totalRecords","count","totalJobs"]:
        if k in data: return int(data[k])
        if isinstance(data.get("data"), dict) and k in data["data"]: return int(data["data"][k])
    return None

def fetch(base_payload, max_pages):
    s = requests.Session()
    s.headers.update(HEADERS)
    page_key = next((k for k in ["pageNo","page","pageNum"] if k in base_payload), "pageNo")
    all_jobs = []
    for n in range(1, max_pages + 1):
        try:
            r = s.post(API_URL, json={**base_payload, page_key: n}, timeout=15)
            print(f"[FETCH] Seite {n}: HTTP {r.status_code}", end="")
            if r.status_code != 200: print(f" → {r.text[:80]}"); break
            data  = r.json()
            jobs  = extract_jobs(data)
            total = get_total(data)
            if not jobs: print(" → keine Jobs mehr"); break
            all_jobs.extend(jobs)
            print(f" → +{len(jobs)} (Gesamt: {len(all_jobs)}/{total or '?'})")
            if total and len(all_jobs) >= total: break
            time.sleep(0.7)
        except Exception as e:
            print(f"\n[FETCH] Fehler: {e}"); break
    print(f"[FETCH] Fertig: {len(all_jobs)} Jobs")
    return all_jobs

# ── Normalize ─────────────────────────────────────────────────────────────────

def norm(raw):
    def g(*keys, default=""):
        for k in keys:
            v = raw.get(k)
            if v and str(v).strip(): return str(v).strip()
        return default
    skills = ""
    for k in ["skills","keySkills","key_skills","tags","skillSet"]:
        v = raw.get(k)
        if isinstance(v, list) and v: skills = ", ".join(str(x).strip() for x in v if x)[:200]; break
        if isinstance(v, str) and v:  skills = v[:200]; break
    return {
        "title":    g("title","jobTitle","designation","position", default="N/A"),
        "company":  g("company","companyName","company_name","employer","organization"),
        "location": g("location","jobLocation","city","cities", default="India"),
        "exp":      g("experience","experienceRequired","exp","minExperience","reqExperience"),
        "salary":   g("salary","salaryRange","ctc","compensation","package"),
        "posted":   g("postedDate","posted_date","createdAt","publishedDate","postDate"),
        "skills":   skills,
        "desc":     g("description","jobDescription","shortDescription","jobSummary")[:280],
        "url":      g("applyUrl","apply_url","jobUrl","url","link","applicationUrl") or PORTAL_BASE,
        "type":     g("jobType","employmentType","type","workType"),
        "category": g("category","function","domain","department","industry"),
    }

def fmt_date(s):
    if not s: return ""
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%d %H:%M:%S","%Y-%m-%d","%d/%m/%Y"]:
        try: return datetime.strptime(s[:26], fmt).strftime("%d. %b %Y")
        except: pass
    return s[:10]

# ── HTML ──────────────────────────────────────────────────────────────────────

def generate_html(jobs, keyword="", location="", exp=""):
    items = [norm(j) for j in jobs]
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")

    badges = ""
    if keyword:  badges += f'<span class="badge">&#x1F50D; {keyword}</span>'
    if location: badges += f'<span class="badge">&#x1F4CD; {location}</span>'
    if exp:
        mn, mx = parse_exp(exp)
        badges += f'<span class="badge">&#x26A1; {mn}&#x2013;{mx} Yrs</span>'

    cards = ""
    for j in items:
        date  = fmt_date(j["posted"])
        tags  = [t.strip() for t in j["skills"].split(",") if t.strip()][:6]
        metas = []
        if j["exp"]:      metas.append(f'<span class="meta">&#x26A1; {j["exp"]}</span>')
        if j["type"]:     metas.append(f'<span class="meta">&#x1F3F7; {j["type"]}</span>')
        if j["salary"]:   metas.append(f'<span class="meta">&#x1F4B0; {j["salary"]}</span>')
        if j["category"]: metas.append(f'<span class="meta">&#x1F4C2; {j["category"]}</span>')

        cards += f"""
<article class="card">
  <div class="card-head">
    <div>
      <div class="job-title">{j['title']}</div>
      <div class="company">{j['company']}</div>
      <div class="loc">&#x1F4CD; {j['location']}</div>
    </div>
    {'<div class="date">' + date + '</div>' if date else ''}
  </div>
  {'<div class="metas">' + ''.join(metas) + '</div>' if metas else ''}
  {'<p class="desc">' + j["desc"] + ('...' if len(j["desc"]) == 280 else '') + '</p>' if j["desc"] else ''}
  {'<div class="tags">' + ''.join(f"<span class='tag'>{t}</span>" for t in tags) + '</div>' if tags else ''}
  <div class="foot">
    <a href="{j['url']}" target="_blank" rel="noopener" class="btn">Apply &#x2192;</a>
  </div>
</article>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCareernet Jobs{' – ' + keyword if keyword else ''}</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#080c12;--bg2:#0f1520;--bg3:#161e2e;--line:#1e2d42;--a:#3b82f6;--a2:#60a5fa;--txt:#e2e8f0;--muted:#4a6080;--tag-bg:#1a3050;--tag-c:#7eb8f0;--r:10px}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh}}
/* header */
.hdr{{background:linear-gradient(160deg,#060e1c,#0a1930 55%,#060d18);border-bottom:1px solid var(--line);padding:52px 24px 40px;text-align:center;position:relative;overflow:hidden}}
.hdr::after{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 70% 50% at 50% -5%,rgba(59,130,246,.17) 0%,transparent 70%);pointer-events:none}}
.pill{{display:inline-flex;align-items:center;gap:7px;font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}}
.dot{{width:6px;height:6px;border-radius:50%;background:var(--a);box-shadow:0 0 8px var(--a);animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
h1{{font-family:'Syne',system-ui,sans-serif;font-size:clamp(1.9rem,5vw,2.9rem);font-weight:800;letter-spacing:-.03em;color:#fff}}
h1 em{{color:var(--a2);font-style:normal}}
.sub{{margin-top:7px;color:var(--muted);font-size:.84rem}}
.badges{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:16px}}
.badge{{background:rgba(59,130,246,.13);border:1px solid rgba(59,130,246,.28);color:var(--a2);padding:4px 14px;border-radius:999px;font-size:.77rem;font-weight:600}}
/* stats bar */
.bar{{background:var(--bg2);border-bottom:1px solid var(--line);padding:10px 24px;display:flex;gap:18px;flex-wrap:wrap;font-size:.79rem;color:var(--muted);align-items:center}}
.bar strong{{color:var(--txt)}}
.bar .right{{margin-left:auto}}
.bar .right a{{color:var(--a2);text-decoration:none}}
/* layout */
.wrap{{max-width:960px;margin:0 auto;padding:26px 14px 64px}}
/* card */
.card{{background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;margin-bottom:12px;transition:border-color .2s,box-shadow .2s,transform .15s}}
.card:hover{{border-color:rgba(59,130,246,.38);box-shadow:0 6px 30px rgba(0,0,0,.45);transform:translateY(-2px)}}
.card-head{{display:flex;justify-content:space-between;gap:12px;margin-bottom:9px}}
.job-title{{font-family:'Syne',system-ui,sans-serif;font-size:1rem;font-weight:700;color:#fff;margin-bottom:3px}}
.company{{font-size:.81rem;color:var(--a2);font-weight:600;margin-bottom:2px}}
.loc{{font-size:.77rem;color:var(--muted)}}
.date{{background:var(--bg3);border:1px solid var(--line);color:var(--muted);font-size:.71rem;padding:3px 9px;border-radius:6px;white-space:nowrap;flex-shrink:0;align-self:flex-start}}
.metas{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}}
.meta{{font-size:.73rem;color:var(--muted);background:var(--bg3);border:1px solid var(--line);padding:2px 9px;border-radius:5px}}
.desc{{font-size:.81rem;color:#6b8aaa;line-height:1.55;margin-bottom:10px}}
.tags{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}}
.tag{{background:var(--tag-bg);color:var(--tag-c);font-size:.71rem;padding:2px 9px;border-radius:5px;border:1px solid rgba(96,165,250,.12)}}
.foot{{display:flex;justify-content:flex-end}}
.btn{{background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;text-decoration:none;padding:7px 18px;border-radius:7px;font-size:.79rem;font-weight:600;transition:opacity .2s,transform .15s}}
.btn:hover{{opacity:.85;transform:translateY(-1px)}}
/* empty */
.empty{{text-align:center;padding:80px 20px;color:var(--muted);font-size:1rem}}
/* footer */
footer{{text-align:center;padding:26px;color:var(--muted);font-size:.74rem;border-top:1px solid var(--line)}}
footer a{{color:var(--a2);text-decoration:none}}
</style>
</head>
<body>
<header class="hdr">
  <div class="pill"><span class="dot"></span>MyCareernet &middot; Auto Scraper</div>
  <h1>Jobs in <em>India</em></h1>
  <p class="sub">Updated: {now}</p>
  {('<div class="badges">' + badges + '</div>') if badges else ''}
</header>
<div class="bar">
  <span>&#x1F4CB; <strong>{len(items)}</strong> jobs loaded</span>
  <span>&#x1F552; <strong>{now}</strong></span>
  <span class="right"><a href="{PORTAL_BASE}" target="_blank">mycareernet.co &#x2197;</a></span>
</div>
<main class="wrap">
{cards if items else '<div class="empty">No jobs found.</div>'}
</main>
<footer><a href="{PORTAL_BASE}" target="_blank">MyCareernet</a> &middot; {now}</footer>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword",  default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--exp",      default="")
    ap.add_argument("--pages",    type=int, default=10)
    ap.add_argument("--output",   default=str(OUTPUT_HTML))
    ap.add_argument("--probe",    action="store_true")
    args = ap.parse_args()

    # 1. Payload ermitteln
    if args.probe or not PAYLOAD_CACHE.exists():
        result = probe(args.keyword, args.location, args.exp)
        if not result:
            print("FEHLER: Kein Payload gefunden. Prüfe ob der Runner Zugriff auf mycareernet.co hat.")
            sys.exit(1)
        base_payload = result["payload"]
    else:
        cache = json.loads(PAYLOAD_CACHE.read_text(encoding="utf-8"))
        base_payload = cache["payload"]
        print(f"[INFO] Payload aus Cache (Variante {cache.get('variant','?')}): {json.dumps(base_payload)}")
        # Filter überschreiben falls angegeben
        if args.keyword:
            for k in ["keyword","searchText","query"]:
                if k in base_payload: base_payload[k] = args.keyword; break
            else: base_payload["keyword"] = args.keyword
        if args.location:
            for k in ["location","city"]:
                if k in base_payload:
                    base_payload[k] = [args.location] if isinstance(base_payload[k], list) else args.location
                    break

    # 2. Jobs holen
    jobs = fetch(base_payload, max_pages=args.pages)

    # 3. Output-Verzeichnis anlegen und speichern
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    html = generate_html(jobs, keyword=args.keyword, location=args.location, exp=args.exp)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"[DONE] {len(jobs)} Jobs → {args.output}")

    RAW_JSON.parent.mkdir(parents=True, exist_ok=True)
    RAW_JSON.write_text(json.dumps(jobs[:10], indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    main()
