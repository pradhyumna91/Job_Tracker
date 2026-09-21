"""Configuration for the job hunter."""

from pathlib import Path

# --- Directories ---
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jobs.db"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# --- Experience Level ---
# "entry" = full-time entry-level / new-grad roles (internships are excluded)
EXPERIENCE_LEVEL = "entry"  # "intern", "entry", "mid", "senior"

# --- Search Queries (used for job board queries) ---
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

# Queries actually searched — internships are excluded, so only full-time roles
SEARCH_QUERIES = SEARCH_QUERIES_FULLTIME

# --- Seniority keywords to EXCLUDE (filter out senior roles) ---
SENIOR_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "lead",
    "director", "manager", "head of", "vp ", "vice president",
    "distinguished", "architect", "8+ years", "10+ years",
    "7+ years", "6+ years", "5+ years",
]

# --- PhD keywords to EXCLUDE (F1 student without PhD) ---
PHD_REQUIRED_KEYWORDS = [
    "phd required", "ph.d. required", "ph.d required",
    "doctorate required", "doctoral required",
    "requires phd", "requires ph.d",
    "phd only", "ph.d. only",
    "must have phd", "must have ph.d",
]

# These in TITLE mean it's a PhD-track role — reject
PHD_TITLE_KEYWORDS = [
    "phd", "ph.d", "postdoc", "post-doc", "postdoctoral",
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
# Unambiguous phrases: these only ever appear when sponsorship is ruled out.
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
]

# Citizenship / clearance phrases need context. Bare "u.s. citizen",
# "permanent resident" and "must be authorized to work" appear in routine EEO
# boilerplate at companies that DO sponsor ("...without regard to citizenship
# status"), so matching them as plain substrings discards good roles. These
# patterns require the *restrictive* framing.
SPONSORSHIP_NEGATIVE_PATTERNS = [
    r"\b(?:must|required to)\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen",
    r"\b(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?(?:required|mandatory)",
    r"\brequires?\s+(?:u\.?s\.?|united states)\s+citizenship\b",
    r"\b(?:u\.?s\.?|united states)\s+citizens?\s+only\b",
    r"\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?\s+)?(?:citizen|permanent resident|green card holder)",
    r"\bcitizenship\s+(?:status\s+)?requirement\b",
    r"\b(?:active|current|existing)\s+(?:security\s+)?clearance\s+(?:is\s+)?(?:required|needed)",
    r"\bsecurity clearance\s+(?:is\s+)?required\b",
    r"\b(?:ts/sci|top secret|secret)\s+clearance\s+(?:is\s+)?required\b",
    r"\bmust\s+be\s+(?:able\s+to\s+)?(?:obtain|hold)\s+(?:a\s+)?(?:security\s+)?clearance",
    r"\bitar\b.{0,40}\b(?:restrict|requir|person)",
    r"\bmust\s+be\s+(?:a\s+)?u\.?s\.?\s+person\b",
    r"\bauthorized\s+to\s+work\s+.{0,40}\bwithout\s+(?:current\s+or\s+future\s+)?sponsorship",
    r"\bnow\s+or\s+in\s+the\s+future\b.{0,40}\bsponsorship",
    r"\bsponsorship\b.{0,40}\bnow\s+or\s+in\s+the\s+future\b",
]

# Phrases that look restrictive but are equal-opportunity boilerplate. If one of
# these covers the match, the negative signal is ignored.
SPONSORSHIP_EEO_BOILERPLATE = [
    r"without regard to",
    r"regardless of",
    r"equal (?:employment )?opportunity",
    r"does not discriminate",
    r"all qualified applicants",
    r"protected (?:veteran|class|characteristic)",
]

# --- Scheduler ---
CHECK_INTERVAL_MINUTES = 60

# Hard wall-clock budget for one scan. A healthy scan takes ~4 minutes, but
# requests' `timeout` bounds each socket operation rather than the whole
# request, so a server that trickles bytes can stall a single fetch for many
# minutes — scans have been observed taking 4+ hours. Past this budget the
# scrapers stop starting new work and the scan finishes with what it has.
SCAN_TIME_BUDGET_MINUTES = 20

# Never start another scan sooner than this after the previous one began,
# so a scan that overruns its budget can't turn into a tight retry loop.
MIN_SCAN_GAP_MINUTES = 5

# --- Dashboard pages ---
# "Posted in last 48h" page: FTE roles posted within this many hours
FRESH_POSTING_HOURS = 48
# "OPT & New Grad" page: earliest month you can start a full-time role (OPT start).
# Roles that explicitly start before this are hidden by default.
EARLIEST_START = "2027-02"

# --- Notification ---
ENABLE_DESKTOP_NOTIFICATION = True
ENABLE_SOUND = False

# --- Curated H-1B sponsor seed list ---
# OFFLINE FALLBACK ONLY. When the USCIS index is available (the normal case)
# this list is not consulted: absence from 31k real employers is better
# evidence than a hand-typed name, and letting the list win meant claiming
# SpaceX and several defense primes sponsor H-1Bs when USCIS records none.
# See h1b_data.H1BIndex._lookup_uncached.
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
    "two sigma", "citadel", "de shaw", "jane street",
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
    # IT services — the largest H-1B sponsors by raw volume
    "tata consultancy", "tech mahindra", "lti mindtree", "mphasis",
    "hexaware", "persistent systems", "zensar", "larsen and toubro infotech",
    # Enterprise / data infrastructure
    "sap", "intuit", "autodesk", "ansys", "synopsys", "cadence",
    "veeva systems", "pagerduty", "dynatrace", "new relic", "mongodb",
    "couchbase", "redis", "cockroach labs", "timescale", "influxdata",
    # Semiconductor
    "tsmc", "applied materials", "lam research", "kla", "marvell",
    "microchip", "texas instruments", "analog devices",
    # Healthcare / pharma
    "elevance health", "cerner", "medidata", "veeva", "iqvia",
    # Retail / marketplaces
    "shopify", "etsy", "instacart",
    # Mobility
    "mobileye", "riot games",
}

# --- Employers that require US citizenship or a security clearance ---
# These are off the table for an F-1 / OPT candidate regardless of whether they
# appear in USCIS data — national labs and cleared defense programs file a
# handful of H-1B petitions for non-cleared roles while the engineering roles
# you'd apply for are citizens-only. Matched on whole-token boundaries.
CITIZENSHIP_REQUIRED_EMPLOYERS = {
    "sandia national laboratories", "sandia national labs",
    "lawrence livermore national laboratory", "los alamos national laboratory",
    "oak ridge national laboratory", "oak ridge institute",
    "pacific northwest national laboratory", "idaho national laboratory",
    "argonne national laboratory", "brookhaven national laboratory",
    "fermi national accelerator laboratory", "fermilab",
    "lincoln laboratory", "mit lincoln laboratory",
    "johns hopkins applied physics laboratory", "johns hopkins apl",
    "naval nuclear laboratory", "naval research laboratory",
    "naval surface warfare center", "naval air warfare center",
    "air force research laboratory", "army research laboratory",
    "mitre", "the mitre corporation",
    "aerospace corporation", "the aerospace corporation",
    "draper", "charles stark draper laboratory",
    "battelle", "battelle memorial institute",
    # ITAR / US-person requirement, not a clearance — but same outcome on F-1.
    "spacex", "space exploration technologies",
    "national security agency", "central intelligence agency",
    "defense intelligence agency", "national reconnaissance office",
    "federal bureau of investigation",
    "savannah river national laboratory",
    "national renewable energy laboratory",
}

# --- Company aliases -> the name USCIS files under ---
# Job boards use brand names; USCIS uses registered entity names.
H1B_COMPANY_ALIASES = {
    # Brand name -> the name USCIS actually files under. Every entry here must
    # earn its place: it should resolve a company that does NOT already match
    # on its own. Identity mappings and aliases pointing at names absent from
    # USCIS are worse than nothing, because they read as deliberate.
    "walmart": "wal mart associates",          # USCIS spells it "WAL MART"
    "tcs": "tata consultancy svcs",            # USCIS abbreviates "services"
    "tata consultancy services": "tata consultancy svcs",
    "tata consultancy": "tata consultancy svcs",
    "alphabet": "google",
    "youtube": "google",
    "google cloud": "google",
    "deepmind": "google",
    "google deepmind": "google",
    "facebook": "meta platforms",
    "instagram": "meta platforms",
    "aws": "amazon web services",
    "amex": "american express",
    "chase": "jpmorgan chase",
    "citi": "citigroup",
    "ea": "electronic arts",
    "amd": "advanced micro devices",
    "hpe": "hewlett packard enterprise",
    "bytedance": "tiktok",
    "de shaw": "d e shaw",
}

