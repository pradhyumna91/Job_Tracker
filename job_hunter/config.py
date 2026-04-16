"""Configuration for the job hunter."""

import os
from pathlib import Path

# --- Directories ---
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jobs.db"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# --- Target Roles ---
TARGET_ROLES = [
    "Data Scientist",
    "Machine Learning Engineer",
    "ML Engineer",
    "AI Engineer",
    "AI/ML Engineer",
    "Software Development Engineer",
    "Software Engineer",
    "SDE",
]

# --- Experience Level ---
# "entry" includes both internships AND full-time entry-level/new-grad roles
EXPERIENCE_LEVEL = "entry"  # "intern", "entry", "mid", "senior"

# --- Search Queries (used for job board queries) ---
# Internship queries (summer & fall 2026)
SEARCH_QUERIES_INTERN = [
    "Data Science Intern",
    "Data Scientist Intern",
    "Machine Learning Intern",
    "ML Engineer Intern",
    "AI ML Intern",
    "Software Engineer Intern",
    "Software Development Engineer Intern",
    "SDE Intern",
    "Data Science Internship Summer 2026",
    "Machine Learning Internship Fall 2026",
    "Software Engineer Internship Summer 2026",
    "AI Engineer Internship",
]

# Full-time entry-level / new grad queries
SEARCH_QUERIES_FULLTIME = [
    "Data Scientist Entry Level",
    "Data Scientist New Grad",
    "Data Scientist Junior",
    "Machine Learning Engineer Entry Level",
    "Machine Learning Engineer New Grad",
    "ML Engineer Junior",
    "AI Engineer Entry Level",
    "AI ML Engineer New Grad",
    "Software Engineer New Grad",
    "Software Engineer Entry Level",
    "Software Development Engineer New Grad",
    "SDE New Grad",
    "Data Scientist visa sponsorship",
    "Machine Learning Engineer visa sponsorship",
    "AI Engineer visa sponsorship",
    "Software Engineer visa sponsorship entry level",
]

# Combined — all queries
SEARCH_QUERIES = SEARCH_QUERIES_INTERN + SEARCH_QUERIES_FULLTIME

# --- Seniority keywords to EXCLUDE (filter out senior roles) ---
SENIOR_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "lead",
    "director", "manager", "head of", "vp ", "vice president",
    "distinguished", "architect", "8+ years", "10+ years",
    "7+ years", "6+ years", "5+ years",
]

# --- Beginner-level keywords to PRIORITIZE ---
INTERN_KEYWORDS = [
    "intern", "internship", "co-op", "coop",
    "new grad", "new graduate", "entry level", "entry-level",
    "junior", "jr.", "jr ", "associate",
    "early career", "recent graduate", "campus",
    "summer 2026", "fall 2026", "spring 2026",
    "summer 2025", "fall 2025",
    "0-2 years", "0-1 years", "1-2 years",
]

# --- Location preferences ---
LOCATIONS = [
    "United States",
]

# --- Visa / Sponsorship keywords (positive signals) ---
SPONSORSHIP_POSITIVE = [
    "visa sponsorship",
    "h1b",
    "h-1b",
    "sponsor",
    "work authorization assistance",
    "immigration sponsorship",
    "will sponsor",
    "sponsorship available",
    "sponsorship provided",
    "open to sponsorship",
]

# --- Visa / Sponsorship keywords (negative signals — company won't sponsor) ---
SPONSORSHIP_NEGATIVE = [
    "no sponsorship",
    "not sponsor",
    "unable to sponsor",
    "cannot sponsor",
    "will not sponsor",
    "won't sponsor",
    "does not sponsor",
    "without sponsorship",
    "no visa sponsorship",
    "not able to sponsor",
    "sponsorship is not available",
    "must be authorized to work",
    "must be eligible to work",
    "permanent resident",
    "us citizen",
    "u.s. citizen",
    "green card",
    "security clearance required",
]

# --- Scheduler ---
CHECK_INTERVAL_MINUTES = 60

# --- Notification ---
ENABLE_DESKTOP_NOTIFICATION = True
ENABLE_SOUND = False

# --- Known H1B Sponsor Companies (top sponsors from USCIS data) ---
# This is a curated seed list. The scraper also checks job descriptions.
H1B_SPONSOR_COMPANIES = {
    "google", "meta", "amazon", "apple", "microsoft", "netflix", "nvidia",
    "salesforce", "oracle", "ibm", "intel", "cisco", "adobe", "uber",
    "lyft", "airbnb", "stripe", "snowflake", "databricks", "palantir",
    "twitter", "x corp", "snap", "pinterest", "spotify", "tiktok",
    "bytedance", "samsung", "qualcomm", "broadcom", "amd",
    "walmart", "target", "costco", "jpmorgan", "jp morgan",
    "goldman sachs", "morgan stanley", "bank of america", "citigroup",
    "citi", "capital one", "american express", "visa inc", "mastercard",
    "paypal", "block", "square", "robinhood", "coinbase", "plaid",
    "deloitte", "ey", "ernst & young", "pwc", "kpmg", "mckinsey",
    "boston consulting", "bain", "accenture", "cognizant", "infosys",
    "tcs", "wipro", "hcl", "capgemini", "dxc technology",
    "tesla", "spacex", "boeing", "lockheed martin", "raytheon",
    "general electric", "ge", "siemens", "honeywell",
    "pfizer", "johnson & johnson", "j&j", "merck", "abbvie",
    "unitedhealth", "anthem", "humana", "cigna", "cvs health",
    "mayo clinic", "epic systems",
    "openai", "anthropic", "deepmind", "cohere", "hugging face",
    "stability ai", "midjourney", "scale ai", "datadog", "splunk",
    "elastic", "confluent", "hashicorp", "cloudflare", "twilio",
    "okta", "crowdstrike", "palo alto networks", "zscaler",
    "servicenow", "workday", "vmware", "dell", "hp", "lenovo",
    "dropbox", "zoom", "slack", "atlassian", "figma", "canva",
    "doordash", "instacart", "grubhub", "chewy", "wayfair",
    "zillow", "redfin", "opendoor", "rivian", "lucid motors",
    "cruise", "waymo", "aurora", "argo ai", "nuro",
    "reddit", "discord", "twitch", "roblox", "epic games",
    "ea", "electronic arts", "activision", "unity",
    "ann arbor", "two sigma", "citadel", "de shaw", "jane street",
    "tower research", "hudson river trading", "jump trading",
    "point72", "bridgewater", "renaissance technologies",
    "bloomberg", "thomson reuters", "s&p global",
    "mckesson", "cardinal health", "amerisourcebergen",
    "procter & gamble", "p&g", "unilever", "coca-cola", "pepsico",
    "3m", "caterpillar", "john deere", "cummins",
    "northrop grumman", "general dynamics", "l3harris",
    "bae systems", "leidos", "saic", "booz allen",
    "coreweave", "lambda", "anyscale", "weights & biases",
    "wandb", "dbt labs", "fivetran", "airbyte", "prefect",
    "modal", "replicate", "together ai", "groq", "cerebras",
    "samsara", "toast", "brex", "ramp", "gusto", "rippling",
}
