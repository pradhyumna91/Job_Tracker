"""H-1B employer verification backed by the USCIS H-1B Employer Data Hub.

The USCIS Data Hub publishes, per fiscal year, every employer that filed an
H-1B petition along with approval and denial counts:

    https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub

This module downloads that CSV, aggregates it into a name -> counts index,
caches it on disk, and answers "has this company actually sponsored H-1Bs?"

Two things it deliberately does NOT do:

* It never falls back to bare substring matching. Matching "ge" inside
  "SeatGeek" flagged a third of this database as verified sponsors; names are
  compared on token boundaries only.
* It never reports a company as a sponsor just because the name looks familiar.
  Every positive answer carries a match type and, where USCIS has the data, a
  real approval count — so a company with 3,000 approvals is distinguishable
  from one with 1.

If the download is unavailable (offline, USCIS reorganizes the site), the
module degrades to the curated seed list in config.py with match type
"curated" and no counts, rather than failing.
"""

import csv
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests

from config import (
    BASE_DIR,
    H1B_SPONSOR_COMPANIES,
    CITIZENSHIP_REQUIRED_EMPLOYERS,
    H1B_COMPANY_ALIASES,
)

logger = logging.getLogger("job_hunter.h1b_data")

CACHE_DIR = BASE_DIR / "cache"
H1B_CACHE_FILE = CACHE_DIR / "h1b_uscis_index.json"
CACHE_MAX_AGE_DAYS = 30  # USCIS publishes annually; monthly refresh is plenty

# Per-fiscal-year export. USCIS publishes a new file roughly a year in arrears,
# so we probe backwards from the current year to find the newest one available.
USCIS_URL_TEMPLATE = (
    "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
)
USCIS_OLDEST_YEAR = 2015
DOWNLOAD_TIMEOUT = 60

# A name matched only on a token-prefix (e.g. job says "Amazon", USCIS says
# "AMAZON WEB SERVICES INC") needs more evidence than an exact name match,
# because short prefixes are shared by unrelated small employers.
MIN_APPROVALS_FOR_PREFIX_MATCH = 5

# Corporate suffixes carrying no identifying information.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "lc", "ltd", "limited", "corp", "corporation",
    "co", "company", "lp", "llp", "lllp", "pllc", "plc", "pc", "gmbh", "ag",
    "nv", "bv", "sa", "sas", "srl", "spa", "pvt", "pte", "ab", "oy", "as",
}

# Trailing geographic qualifiers: "Ericsson US" and "Ericsson" are one company.
_GEO_SUFFIXES = {"usa", "us", "u s", "america", "north america", "na", "americas"}

# Tokens too generic to identify a company on their own. A single-token company
# name in this set is never resolved by prefix match.
_GENERIC_TOKENS = {
    "general", "american", "national", "united", "first", "global", "data",
    "tech", "technology", "technologies", "systems", "system", "solutions",
    "solution", "group", "labs", "lab", "health", "healthcare", "capital",
    "partners", "associates", "consulting", "consultants", "services",
    "service", "international", "advanced", "premier", "professional",
    "management", "enterprises", "industries", "corporate", "business",
    "digital", "cloud", "software", "analytics", "research", "institute",
    "university", "college", "school", "medical", "center", "centre",
    "hospital", "bank", "financial", "finance", "insurance", "energy",
    "media", "studio", "studios", "design", "creative", "network", "networks",
    "security", "logistics", "staffing", "resources", "talent", "recruiting",
    "industry", "company", "holdings", "ventures", "innovations",
}

_PUNCT_RE = re.compile(r"[^a-z0-9&\s]+")
_WS_RE = re.compile(r"\s+")
_DBA_RE = re.compile(r"\s+(?:dba|d b a|doing business as)\s+")


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def normalize_company(name: str) -> str:
    """Reduce a company name to a comparable form.

    Lowercases, expands '&' to 'and', drops punctuation, and strips trailing
    legal and geographic suffixes:

        "Amazon.com Services LLC"  -> "amazon com services"
        "Johnson & Johnson, Inc."  -> "johnson and johnson"
    """
    if not name:
        return ""
    text = name.lower().replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()

    tokens = text.split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]

    # Strip trailing legal/geo suffixes repeatedly ("Foo Inc USA" -> "foo").
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        if tokens[-1] in _LEGAL_SUFFIXES:
            tokens.pop()
            changed = True
        elif tokens[-1] in _GEO_SUFFIXES:
            tokens.pop()
            changed = True
        elif len(tokens) > 2 and " ".join(tokens[-2:]) in _GEO_SUFFIXES:
            tokens = tokens[:-2]
            changed = True

    return " ".join(tokens)


def name_variants(name: str) -> List[str]:
    """Normalized forms a company might be indexed or searched under.

    USCIS records trade names as "0965688 BC LTD DBA PROCOGIA", while a job
    board says "ProCogia" — so both sides of a DBA become lookup keys.
    """
    variants: List[str] = []

    def add(value: str) -> None:
        if value and value not in variants:
            variants.append(value)

    add(normalize_company(name))

    lowered = (name or "").lower()
    if _DBA_RE.search(lowered):
        for part in _DBA_RE.split(lowered):
            add(normalize_company(part))

    # "Google (Alphabet)" / "Stripe (YC S10)" — the parenthetical is often the
    # better-known name, so index it too.
    for inner in re.findall(r"\(([^)]{2,40})\)", name or ""):
        add(normalize_company(inner))

    return variants


def normalize_phrase(text: str) -> str:
    """Light normalization that keeps legal suffixes.

    Blocklist entries are matched with this rather than normalize_company(),
    because suffix stripping destroys them: "The Aerospace Corporation" would
    reduce to "aerospace" and then block every aerospace company in the index.
    """
    if not text:
        return ""
    lowered = text.lower().replace("&", " and ")
    lowered = _PUNCT_RE.sub(" ", lowered)
    lowered = _WS_RE.sub(" ", lowered).strip()
    if lowered.startswith("the "):
        lowered = lowered[4:]
    return lowered


def is_token_prefix(prefix: str, full: str) -> bool:
    """True when `prefix` matches `full` on whole-token boundaries.

    Whole-token matching is the point: plain substring comparison is what made
    "ge" match SeatGeek and "ea" match Health Research.
    """
    if prefix == full:
        return True
    return full.startswith(prefix + " ")


def _is_distinctive(key: str) -> bool:
    """Whether a normalized name is specific enough to match on a prefix."""
    tokens = key.split()
    if not tokens:
        return False
    if len(tokens) >= 2:
        return True
    token = tokens[0]
    return len(token) >= 4 and token not in _GENERIC_TOKENS


# ---------------------------------------------------------------------------
# Employer records
# ---------------------------------------------------------------------------

@dataclass
class EmployerRecord:
    """Aggregated USCIS petition counts for one employer name."""

    name: str
    initial_approval: int = 0
    initial_denial: int = 0
    continuing_approval: int = 0
    continuing_denial: int = 0

    @property
    def approvals(self) -> int:
        return self.initial_approval + self.continuing_approval

    @property
    def denials(self) -> int:
        return self.initial_denial + self.continuing_denial

    def merge(self, other: "EmployerRecord") -> None:
        self.initial_approval += other.initial_approval
        self.initial_denial += other.initial_denial
        self.continuing_approval += other.continuing_approval
        self.continuing_denial += other.continuing_denial


@dataclass
class SponsorMatch:
    """The verdict for one company lookup."""

    is_sponsor: bool
    match: str  # exact | alias | prefix | curated | citizenship-required | none
    approvals: int = 0
    fiscal_year: Optional[str] = None
    matched_name: Optional[str] = None

    @property
    def confidence(self) -> str:
        if self.match in ("exact", "alias"):
            return "high"
        if self.match == "prefix":
            return "medium"
        if self.match == "curated":
            return "low"
        return "none"


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

class H1BIndex:
    """Lookup over USCIS employer data plus curated fallbacks."""

    def __init__(
        self,
        employers: Optional[Dict[str, EmployerRecord]] = None,
        fiscal_year: Optional[str] = None,
    ):
        self.fiscal_year = fiscal_year
        self.employers: Dict[str, EmployerRecord] = employers or {}
        self._prefixes: Dict[str, EmployerRecord] = {}
        self._curated: Set[str] = {normalize_company(c) for c in H1B_SPONSOR_COMPANIES}
        self._curated.discard("")
        self._blocked: Set[str] = set()
        for entry in CITIZENSHIP_REQUIRED_EMPLOYERS:
            phrase = normalize_phrase(entry)
            # A one-word blocklist entry matches far too broadly once it is
            # compared against token windows — skip it loudly rather than
            # silently excluding every company sharing that word.
            if not phrase:
                continue
            tokens = phrase.split()
            if len(tokens) == 1 and (len(phrase) < 4 or tokens[0] in _GENERIC_TOKENS):
                logger.warning(
                    "Ignoring over-broad citizenship blocklist entry %r", entry
                )
                continue
            self._blocked.add(phrase)
        self._aliases: Dict[str, str] = {
            normalize_company(k): normalize_company(v)
            for k, v in H1B_COMPANY_ALIASES.items()
        }
        self._cache: Dict[str, SponsorMatch] = {}
        self._build_prefixes()

    # -- construction ------------------------------------------------------

    def _build_prefixes(self) -> None:
        """Index each employer under its leading 1..4 token prefixes.

        Counts are summed across the corporate family, so "amazon" carries the
        combined total of Amazon Web Services, Amazon.com Services, and so on.
        """
        self._prefixes = {}
        for key, record in self.employers.items():
            tokens = key.split()
            for size in range(1, min(len(tokens), 4) + 1):
                prefix = " ".join(tokens[:size])
                existing = self._prefixes.get(prefix)
                if existing is None:
                    agg = EmployerRecord(name=prefix)
                    agg.merge(record)
                    self._prefixes[prefix] = agg
                else:
                    existing.merge(record)

    # -- lookup ------------------------------------------------------------

    def lookup(self, company_name: str) -> SponsorMatch:
        """Resolve a company name to a sponsorship verdict."""
        if not company_name or not company_name.strip():
            return SponsorMatch(False, "none")

        cached = self._cache.get(company_name)
        if cached is not None:
            return cached

        result = self._lookup_uncached(company_name)
        self._cache[company_name] = result
        return result

    def _lookup_uncached(self, company_name: str) -> SponsorMatch:
        variants = name_variants(company_name)

        # 0. Employers that require US citizenship or a clearance. These override
        #    everything: a national lab may appear in USCIS data for a handful of
        #    roles while being closed to F-1 candidates in practice.
        blocked = self._blocked_by(normalize_phrase(company_name))
        if blocked:
            return SponsorMatch(False, "citizenship-required", matched_name=blocked)

        # 1. Exact match on the USCIS name.
        for variant in variants:
            record = self.employers.get(variant)
            if record and record.approvals > 0:
                return self._match_from(record, "exact")

        # 2. Curated alias resolving to a USCIS name ("alphabet" -> "google").
        for variant in variants:
            target = self._aliases.get(variant)
            if not target:
                continue
            record = self.employers.get(target) or self._prefixes.get(target)
            if record and record.approvals > 0:
                return self._match_from(record, "alias")
            if target in self._curated:
                return SponsorMatch(True, "alias", matched_name=target)

        # 3. Token-prefix match, for distinctive names with real volume behind them.
        for variant in variants:
            if not _is_distinctive(variant):
                continue
            record = self._prefixes.get(variant)
            if record and record.approvals >= MIN_APPROVALS_FOR_PREFIX_MATCH:
                return self._match_from(record, "prefix")

        # 4. Curated seed list — ONLY when there is no USCIS data to consult.
        #    With the real index loaded, absence from 31k employers is itself
        #    evidence, and a hand-typed list must not override it: the list
        #    claimed SpaceX, Lockheed Martin and Northrop Grumman as sponsors
        #    when USCIS records no H-1B approvals for any of them.
        if not self.employers:
            for variant in variants:
                if variant in self._curated:
                    return SponsorMatch(True, "curated", matched_name=variant)
                if _is_distinctive(variant):
                    for seed in self._curated:
                        if is_token_prefix(seed, variant):
                            return SponsorMatch(True, "curated", matched_name=seed)

        return SponsorMatch(False, "none")

    def _match_from(self, record: EmployerRecord, match: str) -> SponsorMatch:
        return SponsorMatch(
            is_sponsor=True,
            match=match,
            approvals=record.approvals,
            fiscal_year=self.fiscal_year,
            matched_name=record.name,
        )

    def _blocked_by(self, phrase: str) -> Optional[str]:
        """The blocklist entry matching this name, if any."""
        if not phrase:
            return None
        if phrase in self._blocked:
            return phrase
        windows = _token_windows(phrase)
        for entry in self._blocked:
            if entry in windows:
                return entry
        return None

    # -- stats -------------------------------------------------------------

    def describe(self) -> str:
        if self.employers:
            return f"USCIS FY{self.fiscal_year}: {len(self.employers):,} employers"
        return f"curated list only: {len(self._curated):,} companies"


def _token_windows(text: str) -> Set[str]:
    """All contiguous token runs of a name, for blocklist phrase matching."""
    tokens = text.split()
    return {
        " ".join(tokens[i:j])
        for i in range(len(tokens))
        for j in range(i + 1, min(i + 6, len(tokens)) + 1)
    }


# ---------------------------------------------------------------------------
# Download + cache
# ---------------------------------------------------------------------------

def _parse_uscis_csv(text: str) -> Tuple[Dict[str, EmployerRecord], Optional[str]]:
    """Aggregate a USCIS export into {normalized name: record}.

    The export has one row per employer *per city*, so the same employer
    appears many times and the counts must be summed.
    """
    employers: Dict[str, EmployerRecord] = {}
    fiscal_year: Optional[str] = None

    reader = csv.DictReader(text.splitlines())
    for row in reader:
        raw_name = (row.get("Employer") or "").strip()
        if not raw_name:
            continue
        if fiscal_year is None:
            fiscal_year = (row.get("Fiscal Year") or "").strip() or None

        def count(column: str) -> int:
            try:
                return int((row.get(column) or "0").strip() or 0)
            except ValueError:
                return 0

        record = EmployerRecord(
            name=raw_name,
            initial_approval=count("Initial Approval"),
            initial_denial=count("Initial Denial"),
            continuing_approval=count("Continuing Approval"),
            continuing_denial=count("Continuing Denial"),
        )

        for key in name_variants(raw_name):
            existing = employers.get(key)
            if existing is None:
                merged = EmployerRecord(name=key)
                merged.merge(record)
                employers[key] = merged
            else:
                existing.merge(record)

    return employers, fiscal_year


def _download_latest() -> Tuple[Dict[str, EmployerRecord], Optional[str]]:
    """Fetch the newest available fiscal-year export, probing backwards."""
    current_year = datetime.now().year
    for year in range(current_year, USCIS_OLDEST_YEAR - 1, -1):
        url = USCIS_URL_TEMPLATE.format(year=year)
        try:
            response = requests.get(
                url,
                timeout=DOWNLOAD_TIMEOUT,
                headers={"User-Agent": "job-hunter/1.0"},
            )
        except requests.RequestException as exc:
            logger.warning("USCIS FY%s download failed: %s", year, exc)
            continue

        if response.status_code != 200:
            continue
        # A missing file returns the USCIS 404 page with a 200 in some cases.
        if "text/html" in response.headers.get("Content-Type", "").lower():
            continue

        employers, fiscal_year = _parse_uscis_csv(response.text)
        if employers:
            logger.info(
                "Downloaded USCIS FY%s H-1B data: %s employer names",
                fiscal_year or year, f"{len(employers):,}"
            )
            return employers, fiscal_year or str(year)

    logger.warning("No USCIS H-1B export could be downloaded")
    return {}, None


def _save_cache(employers: Dict[str, EmployerRecord], fiscal_year: Optional[str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": datetime.now().isoformat(),
        "fiscal_year": fiscal_year,
        "source": USCIS_URL_TEMPLATE.format(year=fiscal_year) if fiscal_year else None,
        "employer_count": len(employers),
        # Compact: [initial_appr, initial_den, cont_appr, cont_den]
        "employers": {
            key: [r.initial_approval, r.initial_denial,
                  r.continuing_approval, r.continuing_denial]
            for key, r in employers.items()
        },
    }
    tmp = H1B_CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(H1B_CACHE_FILE)
    logger.info("Cached %s H-1B employer names to %s", f"{len(employers):,}", H1B_CACHE_FILE)


def _load_cache() -> Tuple[Optional[Dict[str, EmployerRecord]], Optional[str]]:
    if not H1B_CACHE_FILE.exists():
        return None, None
    try:
        payload = json.loads(H1B_CACHE_FILE.read_text())
        cached_at = datetime.fromisoformat(payload["cached_at"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("H-1B cache unreadable (%s); refetching", exc)
        return None, None

    if datetime.now() - cached_at > timedelta(days=CACHE_MAX_AGE_DAYS):
        logger.info("H-1B cache is stale; refetching")
        return None, None

    employers = {
        key: EmployerRecord(
            name=key,
            initial_approval=counts[0],
            initial_denial=counts[1],
            continuing_approval=counts[2],
            continuing_denial=counts[3],
        )
        for key, counts in payload.get("employers", {}).items()
    }
    return employers, payload.get("fiscal_year")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: Optional[H1BIndex] = None


def get_index(refresh: bool = False) -> H1BIndex:
    """Return the shared H1BIndex, building it on first use.

    Order of preference: fresh disk cache, then download, then curated-only.
    """
    global _index
    if _index is not None and not refresh:
        return _index

    employers, fiscal_year = (None, None) if refresh else _load_cache()

    if employers is None:
        employers, fiscal_year = _download_latest()
        if employers:
            try:
                _save_cache(employers, fiscal_year)
            except OSError as exc:
                logger.warning("Could not write H-1B cache: %s", exc)

    _index = H1BIndex(employers or {}, fiscal_year)
    logger.info("H-1B index ready — %s", _index.describe())
    return _index


def lookup_sponsor(company_name: str) -> SponsorMatch:
    """Full sponsorship verdict for a company name."""
    return get_index().lookup(company_name)


if __name__ == "__main__":  # pragma: no cover - manual refresh helper
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    index = get_index(refresh=True)
    print(index.describe())
    for probe in ("Google", "Amazon Web Services (AWS)", "Walmart",
                  "Tata Consultancy Services", "SpaceX",
                  "Naval Nuclear Laboratory", "Bob's Plumbing LLC"):
        m = index.lookup(probe)
        print(f"  {probe:28} {str(m.is_sponsor):5} {m.match:22} "
              f"approvals={m.approvals:,}")
