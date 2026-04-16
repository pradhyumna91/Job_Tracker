"""Job scrapers for multiple sources."""

import hashlib
import json
import time
import random
import logging
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

from config import SEARCH_QUERIES, LOCATIONS, EXPERIENCE_LEVEL

logger = logging.getLogger("job_hunter.scrapers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _make_id(source: str, title: str, company: str, location: str) -> str:
    raw = f"{source}|{title}|{company}|{location}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _polite_delay():
    time.sleep(random.uniform(1.5, 3.5))


# ---------------------------------------------------------------------------
# LinkedIn (public guest API — no login required)
# ---------------------------------------------------------------------------
def scrape_linkedin(query: str, location: str = "United States") -> list[dict]:
    """Scrape LinkedIn public job listings via guest API."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        encoded_loc = quote_plus(location)
        # LinkedIn f_E: 1=Internship, 2=Entry level. Comma-separated for both.
        exp_map = {"intern": "&f_E=1", "entry": "&f_E=1%2C2"}
        exp_filter = exp_map.get(EXPERIENCE_LEVEL, "")
        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={encoded_query}&location={encoded_loc}"
            f"&f_TPR=r86400{exp_filter}&start=0"  # last 24 hours
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"LinkedIn returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("div", class_="base-card")

        for card in cards:
            title_el = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            location_el = card.find("span", class_="job-search-card__location")
            link_el = card.find("a", class_="base-card__full-link")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""
            link = link_el["href"] if link_el and link_el.has_attr("href") else ""

            if not title or not company:
                continue

            jobs.append({
                "id": _make_id("linkedin", title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link.split("?")[0] if link else "",
                "source": "linkedin",
                "description": "",
                "salary": "",
                "tags": [query],
            })

        logger.info(f"LinkedIn: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"LinkedIn scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Indeed (public search page)
# ---------------------------------------------------------------------------
def scrape_indeed(query: str, location: str = "United States") -> list[dict]:
    """Scrape Indeed public search results."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        encoded_loc = quote_plus(location)
        url = (
            f"https://www.indeed.com/jobs?q={encoded_query}"
            f"&l={encoded_loc}&fromage=1&sort=date"  # last 1 day
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Indeed returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("div", class_="job_seen_beacon")

        for card in cards:
            title_el = card.find("h2", class_="jobTitle")
            company_el = card.find("span", attrs={"data-testid": "company-name"})
            location_el = card.find("div", attrs={"data-testid": "text-location"})
            link_el = card.find("a", class_="jcs-JobTitle")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""
            job_link = ""
            if link_el and link_el.has_attr("href"):
                href = link_el["href"]
                job_link = f"https://www.indeed.com{href}" if href.startswith("/") else href

            if not title or not company:
                continue

            jobs.append({
                "id": _make_id("indeed", title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": job_link,
                "source": "indeed",
                "description": "",
                "salary": "",
                "tags": [query],
            })

        logger.info(f"Indeed: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Indeed scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Google Jobs (via SerpAPI-style scraping of Google search)
# ---------------------------------------------------------------------------
def scrape_google_jobs(query: str) -> list[dict]:
    """Scrape Google search for job postings on major career pages."""
    jobs = []
    try:
        search_query = quote_plus(
            f"{query} jobs USA visa sponsorship site:lever.co OR site:greenhouse.io OR site:boards.greenhouse.io OR site:jobs.lever.co"
        )
        url = f"https://www.google.com/search?q={search_query}&num=20"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Google returned {resp.status_code}")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        for result in soup.find_all("div", class_="g"):
            link_el = result.find("a")
            title_el = result.find("h3")
            if not link_el or not title_el:
                continue
            link = link_el.get("href", "")
            title = title_el.get_text(strip=True)
            snippet_el = result.find("span", class_="aCOpRe")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            company = _extract_company_from_url(link)

            jobs.append({
                "id": _make_id("google", title, company, "US"),
                "title": title,
                "company": company,
                "location": "United States",
                "url": link,
                "source": "google",
                "description": snippet,
                "salary": "",
                "tags": [query],
            })

        logger.info(f"Google Jobs: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Google scraper error for '{query}': {e}")
    return jobs


def _extract_company_from_url(url: str) -> str:
    """Try to extract company name from lever/greenhouse URLs."""
    url_lower = url.lower()
    for domain in ["jobs.lever.co/", "boards.greenhouse.io/"]:
        if domain in url_lower:
            idx = url_lower.index(domain) + len(domain)
            slug = url[idx:].split("/")[0]
            return slug.replace("-", " ").title()
    return "Unknown"


# ---------------------------------------------------------------------------
# RemoteOK (JSON API — supports remote ML/AI jobs)
# ---------------------------------------------------------------------------
def scrape_remoteok() -> list[dict]:
    """Scrape RemoteOK API for remote ML/AI/SWE jobs."""
    jobs = []
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"RemoteOK returned {resp.status_code}")
            return jobs

        data = resp.json()
        if isinstance(data, list) and len(data) > 1:
            data = data[1:]  # first element is metadata

        target_tags = {
            "machine learning", "ml", "ai", "data science", "data scientist",
            "python", "deep learning", "software engineer", "sde", "nlp",
            "computer vision", "tensorflow", "pytorch",
        }

        for item in data:
            tags_raw = [t.lower() for t in item.get("tags", [])]
            position = item.get("position", "").lower()

            is_relevant = (
                any(t in target_tags for t in tags_raw)
                or any(kw in position for kw in [
                    "machine learning", "data scien", "ml ", "ai ",
                    "software engineer", "sde", "deep learning",
                ])
            )
            if not is_relevant:
                continue

            company = item.get("company", "")
            title = item.get("position", "")
            jobs.append({
                "id": _make_id("remoteok", title, company, "Remote"),
                "title": title,
                "company": company,
                "location": item.get("location", "Remote"),
                "url": item.get("url", f"https://remoteok.com/remote-jobs/{item.get('slug', '')}"),
                "source": "remoteok",
                "description": item.get("description", ""),
                "salary": "",
                "tags": tags_raw,
            })

        logger.info(f"RemoteOK: found {len(jobs)} relevant jobs")
    except Exception as e:
        logger.error(f"RemoteOK scraper error: {e}")
    return jobs


# ---------------------------------------------------------------------------
# Jobright.ai (job search with visa sponsorship filter)
# ---------------------------------------------------------------------------
def scrape_jobright(query: str) -> list[dict]:
    """Scrape Jobright.ai search results."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        url = (
            f"https://jobright.ai/jobs/search"
            f"?keyword={encoded_query}&location=United+States"
            f"&sort=date"
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Jobright returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")

        # Jobright renders job cards — try multiple selectors
        cards = soup.find_all("div", attrs={"data-testid": "job-card"})
        if not cards:
            cards = soup.find_all("a", class_=lambda c: c and "job" in c.lower())
        if not cards:
            # Fallback: parse any structured job-like elements
            cards = soup.find_all("div", class_=lambda c: c and ("card" in (c or "").lower() or "job" in (c or "").lower()))

        for card in cards:
            # Try to extract title
            title_el = (
                card.find("h2") or card.find("h3")
                or card.find("span", class_=lambda c: c and "title" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "title" in (c or "").lower())
            )
            # Try to extract company
            company_el = (
                card.find("span", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("p")
            )
            # Try to extract location
            location_el = (
                card.find("span", class_=lambda c: c and "location" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "location" in (c or "").lower())
            )

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""

            link = ""
            link_el = card.find("a", href=True)
            if link_el:
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://jobright.ai{href}"

            if not title:
                continue

            jobs.append({
                "id": _make_id("jobright", title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link,
                "source": "jobright",
                "description": "",
                "salary": "",
                "tags": [query],
            })

        logger.info(f"Jobright: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Jobright scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Hiring Cafe (hiring.cafe — aggregated job board)
# ---------------------------------------------------------------------------
def scrape_hiring_cafe(query: str) -> list[dict]:
    """Scrape Hiring Cafe search results."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        url = (
            f"https://hiring.cafe/jobs"
            f"?query={encoded_query}&location=United+States"
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Hiring Cafe returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")

        # Hiring Cafe renders job listings — try flexible selectors
        cards = soup.find_all("div", attrs={"data-testid": "job-card"})
        if not cards:
            cards = soup.find_all("article")
        if not cards:
            cards = soup.find_all("div", class_=lambda c: c and ("card" in (c or "").lower() or "job" in (c or "").lower() or "listing" in (c or "").lower()))
        if not cards:
            # Try tr elements for table-based layouts
            cards = soup.find_all("tr", class_=lambda c: c and "job" in (c or "").lower())

        for card in cards:
            title_el = (
                card.find("h2") or card.find("h3")
                or card.find("a", class_=lambda c: c and "title" in (c or "").lower())
                or card.find("span", class_=lambda c: c and "title" in (c or "").lower())
            )
            company_el = (
                card.find("span", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "company" in (c or "").lower())
            )
            location_el = (
                card.find("span", class_=lambda c: c and "location" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "location" in (c or "").lower())
            )

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""

            link = ""
            link_el = card.find("a", href=True)
            if link_el:
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://hiring.cafe{href}"

            if not title:
                continue

            jobs.append({
                "id": _make_id("hiringcafe", title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link,
                "source": "hiringcafe",
                "description": "",
                "salary": "",
                "tags": [query],
            })

        logger.info(f"Hiring Cafe: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Hiring Cafe scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def scrape_all() -> list[dict]:
    """Run all scrapers and return combined job list."""
    all_jobs = []

    for query in SEARCH_QUERIES:
        all_jobs.extend(scrape_linkedin(query))
        _polite_delay()
        all_jobs.extend(scrape_indeed(query))
        _polite_delay()
        all_jobs.extend(scrape_google_jobs(query))
        _polite_delay()
        all_jobs.extend(scrape_jobright(query))
        _polite_delay()
        all_jobs.extend(scrape_hiring_cafe(query))
        _polite_delay()

    all_jobs.extend(scrape_remoteok())

    # Deduplicate by id
    seen_ids = set()
    unique_jobs = []
    for job in all_jobs:
        if job["id"] not in seen_ids:
            seen_ids.add(job["id"])
            unique_jobs.append(job)

    logger.info(f"Total unique jobs scraped: {len(unique_jobs)}")
    return unique_jobs
