"""
Free job alert bot: India + Remote, software engineering intern / 0-1 years.
Sources: LinkedIn, Indeed, Naukri (via JobSpy) + Remotive (free API).
Sends new jobs to WhatsApp through CallMeBot.
"""
import json
import math
import os
import re
import time
from pathlib import Path

import requests

# ---------------- SETTINGS (edit these) ----------------
SEARCH_TERMS = [
    "software engineer intern",
    "software developer intern",
    "software engineer fresher",
    "junior software engineer",
    "graduate software engineer",
    "SDE 1",
    "remote software engineer intern",
    "remote junior software developer",
]
LOCATION = "India"
SITES = ["linkedin", "indeed", "naukri"]   # a failing site is skipped automatically
HOURS_OLD = 2          # look back 2h; overlap with the 30 min timer is deduped
RESULTS_PER_SEARCH = 30
MAX_EXPERIENCE = 1     # keep jobs needing at most this many years
MAX_ALERTS_PER_RUN = 40
JOBS_PER_MESSAGE = 4

# Optional stack filter. Leave empty [] to disable.
STACK_KEYWORDS = []    # e.g. ["python", "react", "java"]
# -------------------------------------------------------

SEEN_FILE = Path("seen_jobs.json")
PHONE = os.environ.get("CALLMEBOT_PHONE", "")      # e.g. +919876543210
APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")

TITLE_OK = re.compile(r"software|sde|developer|programmer|engineer|swe", re.I)
TITLE_BAD = re.compile(
    r"senior|\bsr\b|staff|lead|principal|manager|architect|director|head of|"
    r"\bvp\b|\b(ii|iii|iv)\b|mechanical|civil|electrical|sales|hardware|"
    r"embedded|qa manager",
    re.I,
)
FRESHER_HINT = re.compile(
    r"intern|fresher|new grad|graduate|entry[- ]level|junior|trainee|"
    r"\bsde[- ]?1\b|\bsde[- ]?i\b|engineer i\b|no experience|0 years",
    re.I,
)


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def experience(text):
    """Return (min_years or None, label)."""
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*\+?\s*(?:years|yrs|year)", t)
    if m:
        return int(m.group(1)), f"{m.group(1)}-{m.group(2)} years"
    m = re.search(r"(\d+)\s*\+?\s*(?:years|yrs|year)", t)
    if m:
        plus = "+" if "+" in m.group(0) else ""
        return int(m.group(1)), f"{m.group(1)}{plus} years"
    if FRESHER_HINT.search(t):
        return 0, "Fresher / Intern"
    return None, "Not specified"


def accept(job):
    title, desc = job["title"], job["description"]
    if not TITLE_OK.search(title) or TITLE_BAD.search(title):
        return False
    min_years, label = experience(f"{title}. {desc}")
    if min_years is not None and min_years > MAX_EXPERIENCE:
        return False
    job["experience"] = label
    if STACK_KEYWORDS:
        blob = f"{title} {desc}".lower()
        if not any(k.lower() in blob for k in STACK_KEYWORDS):
            return False
    return True


def key(job):
    url = job["url"].split("?")[0]
    return url or f"{job['title']}|{job['company']}".lower()


def fetch_jobspy():
    from jobspy import scrape_jobs

    jobs = []
    for site in SITES:
        for term in SEARCH_TERMS:
            try:
                df = scrape_jobs(
                    site_name=[site],
                    search_term=term,
                    location=LOCATION,
                    results_wanted=RESULTS_PER_SEARCH,
                    hours_old=HOURS_OLD,
                    country_indeed="India",
                    verbose=0,
                )
            except Exception as e:
                print(f"[{site}] '{term}' failed: {str(e)[:120]}")
                continue
            for r in df.to_dict("records"):
                jobs.append(
                    {
                        "title": clean(r.get("title")),
                        "company": clean(r.get("company")) or "Unknown",
                        "url": clean(r.get("job_url")),
                        "description": clean(r.get("description")),
                    }
                )
            time.sleep(2)
    return jobs


def fetch_remotive():
    jobs = []
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"category": "software-dev", "limit": 100},
            timeout=30,
        )
        for j in r.json().get("jobs", []):
            loc = (j.get("candidate_required_location") or "").lower()
            if not any(k in loc for k in ("india", "worldwide", "anywhere", "asia", "apac")):
                continue
            jobs.append(
                {
                    "title": clean(j.get("title")),
                    "company": clean(j.get("company_name")),
                    "url": clean(j.get("url")),
                    "description": re.sub(r"<[^>]+>", " ", j.get("description") or ""),
                }
            )
    except Exception as e:
        print(f"[remotive] failed: {str(e)[:120]}")
    return jobs


def send_whatsapp(text):
    if not PHONE or not APIKEY:
        print("Missing CALLMEBOT_PHONE / CALLMEBOT_APIKEY; printing instead:\n", text)
        return False
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": PHONE, "text": text, "apikey": APIKEY},
            timeout=30,
        )
        ok = r.status_code == 200
        if not ok:
            print("CallMeBot error:", r.status_code, r.text[:150])
        return ok
    except Exception as e:
        print("CallMeBot exception:", str(e)[:150])
        return False


def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except Exception:
            pass
    return {}


def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    now = time.time()
    seen = {k: v for k, v in seen.items() if now - v < 30 * 86400}  # forget after 30 days

    all_jobs = fetch_jobspy() + fetch_remotive()
    print(f"Fetched {len(all_jobs)} raw jobs")

    fresh, batch_keys = [], set()
    for job in all_jobs:
        if not job["title"] or not job["url"]:
            continue
        k = key(job)
        if k in seen or k in batch_keys:
            continue
        batch_keys.add(k)
        if accept(job):
            fresh.append(job)
    print(f"{len(fresh)} new matching jobs")

    if first_run:
        # Seed silently so you don't get flooded with old jobs on day one.
        for j in fresh:
            seen[key(j)] = now
        send_whatsapp(f"Job alert bot is live. Skipped {len(fresh)} existing jobs. New ones will arrive here.")
        SEEN_FILE.write_text(json.dumps(seen))
        return

    fresh = fresh[:MAX_ALERTS_PER_RUN]
    for i in range(0, len(fresh), JOBS_PER_MESSAGE):
        chunk = fresh[i : i + JOBS_PER_MESSAGE]
        text = "\n\n".join(
            f"{j['title']}\nCompany: {j['company']}\nExperience: {j['experience']}\n{j['url']}"
            for j in chunk
        )
        if send_whatsapp(text):
            for j in chunk:
                seen[key(j)] = now
        time.sleep(3)

    SEEN_FILE.write_text(json.dumps(seen))


if __name__ == "__main__":
    main()
