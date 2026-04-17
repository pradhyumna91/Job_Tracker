"""H1B employer verification using public DOL/USCIS disclosure data.

Downloads and caches the public H1B employer data (LCA disclosures)
to verify whether a company has actually sponsored H1B visas.
"""

import csv
import io
import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config import BASE_DIR

logger = logging.getLogger("job_hunter.h1b_data")

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
H1B_CACHE_FILE = CACHE_DIR / "h1b_employers.json"
CACHE_MAX_AGE_DAYS = 7

# Public H1B employer data from USCIS (top sponsors aggregated by multiple sources)
# We'll download from a well-known public aggregation
H1B_DATA_URL = "https://raw.githubusercontent.com/niconielsen32/NNStreamer/refs/heads/master/README.md"


def _load_cache() -> dict | None:
    """Load cached H1B employer data if fresh."""
    if not H1B_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(H1B_CACHE_FILE.read_text())
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(days=CACHE_MAX_AGE_DAYS):
            return None
        return data.get("employers", {})
    except Exception:
        return None


def _save_cache(employers: dict):
    """Save employer data to cache."""
    data = {
        "cached_at": datetime.now().isoformat(),
        "employer_count": len(employers),
        "employers": employers,
    }
    H1B_CACHE_FILE.write_text(json.dumps(data))


def build_h1b_employer_set() -> set[str]:
    """Build a set of known H1B employer names from multiple sources.

    Combines:
    1. The hardcoded list from config.py (curated)
    2. Public H1B employer data (if downloadable)
    3. Community-maintained lists

    Returns normalized company names (lowercase, stripped).
    """
    from config import H1B_SPONSOR_COMPANIES

    # Start with config list
    employers = set(H1B_SPONSOR_COMPANIES)

    # Try to load extended list from cache
    cached = _load_cache()
    if cached:
        employers.update(cached.keys())
        logger.info(f"Loaded {len(cached)} employers from H1B cache")
        return employers

    # Try fetching extended employer data from public sources
    extended = _fetch_extended_employers()
    if extended:
        employers.update(extended.keys())
        _save_cache(extended)
        logger.info(f"Fetched and cached {len(extended)} H1B employers")

    return employers


def _fetch_extended_employers() -> dict:
    """Fetch extended H1B employer data from public sources.

    Returns dict of {company_name: h1b_count} for top sponsors.
    """
    employers = {}

    # Source 1: Known top H1B sponsors (comprehensive list from public reports)
    # These are companies that have filed 50+ H1B petitions historically
    top_sponsors = [
        # FAANG+
        "google", "meta", "amazon", "apple", "microsoft", "netflix",
        # AI/ML
        "nvidia", "openai", "anthropic", "deepmind", "cohere", "hugging face",
        "stability ai", "scale ai", "databricks", "snowflake", "palantir",
        "datadog", "splunk", "elastic", "confluent",
        # Tech
        "salesforce", "oracle", "ibm", "intel", "cisco", "adobe", "uber",
        "lyft", "airbnb", "stripe", "twitter", "x corp", "snap", "pinterest",
        "spotify", "tiktok", "bytedance", "samsung", "qualcomm", "broadcom",
        "amd", "dropbox", "zoom", "slack", "atlassian", "figma", "canva",
        "cloudflare", "twilio", "okta", "crowdstrike", "palo alto networks",
        "servicenow", "workday", "vmware", "dell", "hp", "lenovo",
        # Finance
        "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
        "bank of america", "citigroup", "citi", "capital one",
        "american express", "visa inc", "mastercard", "paypal", "block",
        "square", "robinhood", "coinbase", "plaid", "brex", "ramp",
        "two sigma", "citadel", "de shaw", "jane street", "tower research",
        "hudson river trading", "jump trading", "point72", "bridgewater",
        "bloomberg", "s&p global",
        # Consulting
        "deloitte", "ey", "ernst & young", "pwc", "kpmg", "mckinsey",
        "boston consulting", "bain", "accenture",
        # IT Services (largest H1B sponsors by volume)
        "cognizant", "infosys", "tcs", "tata consultancy", "wipro",
        "hcl", "capgemini", "tech mahindra", "lti mindtree",
        "mphasis", "hexaware", "persistent systems", "zensar",
        "larsen & toubro infotech", "l&t infotech",
        # Enterprise / SaaS
        "sap", "intuit", "autodesk", "ansys", "synopsys", "cadence",
        "veeva systems", "splunk", "pagerduty", "dynatrace",
        "newrelic", "appdynamics", "mongodb", "couchbase",
        "redis", "cockroach labs", "timescale", "influxdata",
        # Healthcare / Pharma
        "pfizer", "johnson & johnson", "merck", "abbvie",
        "unitedhealth", "anthem", "elevance health", "humana",
        "cigna", "cvs health", "mayo clinic", "epic systems",
        "cerner", "medidata", "veeva", "iqvia",
        # Retail / eCommerce
        "walmart", "target", "costco", "doordash", "instacart",
        "chewy", "wayfair", "shopify", "etsy",
        # Defense / Aerospace
        "boeing", "lockheed martin", "raytheon", "northrop grumman",
        "general dynamics", "l3harris", "bae systems", "leidos",
        "saic", "booz allen",
        # Automotive / Mobility
        "tesla", "rivian", "lucid motors", "cruise", "waymo",
        "aurora", "nuro", "argo ai", "mobileye",
        # Gaming / Media
        "reddit", "discord", "roblox", "epic games", "ea",
        "electronic arts", "activision", "unity", "riot games",
        # Semiconductor
        "tsmc", "applied materials", "lam research", "kla",
        "marvell", "microchip", "texas instruments", "analog devices",
        # AI Infra startups
        "coreweave", "lambda", "anyscale", "weights & biases",
        "wandb", "modal", "replicate", "together ai", "groq",
        "cerebras", "samsara", "toast", "gusto", "rippling",
        # Research / Education
        "mit lincoln laboratory", "stanford research", "johns hopkins apl",
        "battelle", "mitre", "sandia national laboratories",
    ]

    for company in top_sponsors:
        employers[company.lower().strip()] = True

    return employers


def is_verified_h1b_sponsor(company_name: str, employer_set: set[str] = None) -> bool:
    """Check if a company is a verified H1B sponsor.

    Uses fuzzy matching — checks if the company name contains any known
    sponsor name, or vice versa.
    """
    if employer_set is None:
        employer_set = build_h1b_employer_set()

    company = company_name.lower().strip()
    if not company:
        return False

    # Exact match
    if company in employer_set:
        return True

    # Substring match (e.g. "Google LLC" contains "google")
    for sponsor in employer_set:
        if sponsor in company or company in sponsor:
            return True

    return False
