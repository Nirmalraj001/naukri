
"""
Naukri Frontend / React Job Collector
--------------------------------------
COLLECTION ONLY. This script NEVER clicks:
    - Apply
    - Apply on Company Site

It searches Naukri using configurable target roles, collects job cards,
opens each candidate job detail page READ-ONLY, validates:
    1) target role
    2) frontend/React technology relevance
    3) excluded domain/technology rules
    4) freshness
    5) supported application type

Then it deduplicates and exports the final validated jobs to CSV.

Recommended .env
----------------
NAUKRI_EMAIL=your-email
NAUKRI_PASSWORD=your-password

KEYWORDS=Frontend Developer,React Developer,React.js Developer,Senior Frontend Developer,UI Developer,UI Engineer,Next.js Developer
LOCATION=Chennai
FRESHNESS_FILTER=last_1_day
PAGES_PER_KEYWORD=3
MAX_JOBS=0

TARGET_ROLES=Frontend Developer,React Developer,React.js Developer,Senior Frontend Developer,UI Developer,UI Engineer,Next.js Developer
FRONTEND_SKILLS=React,React.js,ReactJS,JavaScript,ES6,TypeScript,Next.js,Redux,Redux Toolkit,HTML5,CSS3,Tailwind CSS,REST API,REST APIs,Node.js,Express.js
EXCLUDED_TERMS=Java Developer,Java Full Stack,Spring Boot,.NET Developer,.NET Full Stack,C# Developer,PHP Developer,Python Developer,Python Django,Salesforce Developer,SAP Developer,DevOps Engineer,Data Engineer,QA Engineer,Automation Tester,Test Engineer,Android Developer,iOS Developer,Angular Developer,Teamcenter Developer,Java Backend,Java Back-end,Java Production Support
MIN_RELEVANCE_SCORE=55
MIN_FRONTEND_SKILLS=1
OUTPUT_CSV=naukri_jobs.csv
EDGE_DRIVER_PATH=

Install:
    pip install selenium webdriver-manager python-dotenv beautifulsoup4 pandas html5lib
"""

import os
import uuid
from pathlib import Path
import re
import time
import logging
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
)

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False


# ============================================================
# Configuration
# ============================================================

load_dotenv()

NAUKRI_EMAIL = os.getenv("NAUKRI_EMAIL", "").strip()
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "").strip()

LOCATION = os.getenv("LOCATION", "").strip()

KEYWORDS = [
    x.strip() for x in os.getenv(
        "KEYWORDS",
        "Frontend Developer,React Developer,React.js Developer"
    ).split(",") if x.strip()
]

TARGET_ROLES = [
    x.strip() for x in os.getenv(
        "TARGET_ROLES",
        "Frontend Developer,React Developer,React.js Developer,"
        "Senior Frontend Developer,UI Developer,UI Engineer,Next.js Developer"
    ).split(",") if x.strip()
]

FRONTEND_SKILLS = [
    x.strip() for x in os.getenv(
        "FRONTEND_SKILLS",
        "React,React.js,ReactJS,JavaScript,ES6,TypeScript,Next.js,"
        "Redux,Redux Toolkit,HTML5,CSS3,Tailwind CSS,REST API,REST APIs,"
        "Node.js,Express.js"
    ).split(",") if x.strip()
]

EXCLUDED_TERMS = [
    x.strip() for x in os.getenv(
        "EXCLUDED_TERMS",
        "Java Developer,Java Full Stack,Spring Boot,.NET Developer,"
        ".NET Full Stack,C# Developer,PHP Developer,Python Developer,"
        "Python Django,Salesforce Developer,SAP Developer,DevOps Engineer,"
        "Data Engineer,QA Engineer,Automation Tester,Test Engineer,"
        "Android Developer,iOS Developer,Angular Developer,"
        "Teamcenter Developer,Java Backend,Java Back-end,Java Production Support"
    ).split(",") if x.strip()
]

FRESHNESS_FILTER = os.getenv("FRESHNESS_FILTER", "last_1_day").strip().lower()
PAGES_PER_KEYWORD = max(1, int(os.getenv("PAGES_PER_KEYWORD", "3")))
MAX_JOBS = max(0, int(os.getenv("MAX_JOBS", "0")))
MIN_RELEVANCE_SCORE = max(0, min(100, int(os.getenv("MIN_RELEVANCE_SCORE", "55"))))
MIN_FRONTEND_SKILLS = max(0, int(os.getenv("MIN_FRONTEND_SKILLS", "1")))

OUTPUT_CSV = (
    os.getenv("OUTPUT_CSV", "naukri_jobs.csv").strip()
    or "naukri_jobs.csv"
)
EDGE_DRIVER_PATH = os.getenv("EDGE_DRIVER_PATH", "").strip()

FRESHNESS_CONFIG = {
    "current_day": {"job_age": 1, "max_days": 0},
    "today": {"job_age": 1, "max_days": 0},
    "last_1_day": {"job_age": 1, "max_days": 1},
    "1_day": {"job_age": 1, "max_days": 1},
    "last_3_days": {"job_age": 3, "max_days": 3},
    "3_days": {"job_age": 3, "max_days": 3},
}

if FRESHNESS_FILTER not in FRESHNESS_CONFIG:
    raise ValueError(
        "FRESHNESS_FILTER must be current_day, last_1_day, or last_3_days"
    )

FRESHNESS = FRESHNESS_CONFIG[FRESHNESS_FILTER]

CSV_COLUMNS = [
    "Job Title",
    "Company Name",
    "Location",
    "Experience",
    "Skills",
    "Job Posted Date / Freshness",
    "Application Type",
    "Job URL",
    "Job ID",
    "Relevance Score",
    "Match Status",
    "Matched Roles",
    "Matched Skills",
    "Excluded Terms Found",
]


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# Generic helpers
# ============================================================

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_for_match(value):
    """
    Normalization used ONLY for matching.
    It does not modify the original CSV values.
    """
    value = clean_text(value).lower()
    value = value.replace("react.js", "reactjs")
    value = value.replace("node.js", "nodejs")
    value = value.replace("next.js", "nextjs")
    value = value.replace("tailwindcss", "tailwind css")
    value = re.sub(r"[^a-z0-9+#.\-/ ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def contains_term(text, term):
    text_n = normalize_for_match(text)
    term_n = normalize_for_match(term)

    if not text_n or not term_n:
        return False

    # For short terms such as "C#", "ES6", use a boundary-aware check.
    pattern = r"(?<![a-z0-9])" + re.escape(term_n) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text_n))


def first_matching_text(soup, selectors):
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            value = clean_text(el.get_text(" ", strip=True))
            if value:
                return value
    return ""


# ============================================================
# Config validation
# ============================================================

def validate_config():
    errors = []

    if not NAUKRI_EMAIL:
        errors.append("NAUKRI_EMAIL is missing")
    if not NAUKRI_PASSWORD:
        errors.append("NAUKRI_PASSWORD is missing")
    if not KEYWORDS:
        errors.append("KEYWORDS is empty")
    if not TARGET_ROLES:
        errors.append("TARGET_ROLES is empty")
    if not FRONTEND_SKILLS:
        errors.append("FRONTEND_SKILLS is empty")

    if errors:
        for error in errors:
            logger.error(error)
        return False

    return True


# ============================================================
# Browser / login
# ============================================================

def create_edge_driver():
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")

    if WEBDRIVER_MANAGER_AVAILABLE:
        logger.info("Using webdriver-manager for EdgeDriver.")
        service = EdgeService(EdgeChromiumDriverManager().install())
    elif EDGE_DRIVER_PATH:
        logger.info("Using EdgeDriver: %s", EDGE_DRIVER_PATH)
        service = EdgeService(executable_path=EDGE_DRIVER_PATH)
    else:
        logger.info("Using EdgeDriver from PATH.")
        service = EdgeService()

    return webdriver.Edge(service=service, options=options)


def login_naukri(driver):
    logger.info("Opening Naukri login...")
    driver.get("https://login.naukri.com/")

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "usernameField"))
        )
    except TimeoutException:
        logger.info(
            "Login form not visible; existing Naukri session may already be active."
        )
        return

    driver.find_element(By.ID, "usernameField").send_keys(NAUKRI_EMAIL)
    driver.find_element(By.ID, "passwordField").send_keys(NAUKRI_PASSWORD)
    driver.find_element(By.ID, "passwordField").send_keys(Keys.ENTER)

    time.sleep(6)

    if "login" in driver.current_url.lower():
        logger.warning(
            "Still on login page. Verify credentials, OTP, CAPTCHA, or account state."
        )
    else:
        logger.info("Naukri session established.")


# ============================================================
# URL / search
# ============================================================

def normalize_job_url(url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.naukri.com" + url

    parts = urlsplit(url)

    if not parts.scheme or not parts.netloc:
        return ""

    host = parts.netloc.lower()

    if host not in ("www.naukri.com", "naukri.com"):
        return ""

    # Remove tracking/query parameters while preserving the Naukri path.
    return urlunsplit(
        (
            parts.scheme.lower(),
            host,
            parts.path.rstrip("/"),
            "",
            "",
        )
    )


def build_search_urls():
    """
    Generate a clean URL for each configured role/search keyword and page.

    IMPORTANT:
    The collector does not trust the search page to perform all semantic
    filtering. It always performs local title/description/skills validation
    after fetching each job.
    """
    urls = []

    for keyword in KEYWORDS:
        slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")

        if LOCATION:
            location_slug = re.sub(
                r"[^a-z0-9]+", "-", LOCATION.lower()
            ).strip("-")
            base = f"https://www.naukri.com/{slug}-jobs-in-{location_slug}"
        else:
            base = f"https://www.naukri.com/{slug}-jobs"

        for page in range(1, PAGES_PER_KEYWORD + 1):
            if page == 1:
                url = base
            else:
                url = f"{base}-{page}"

            # Naukri supports jobAge on the search URL. It is only a
            # server-side first-pass filter; local validation follows.
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}jobAge={FRESHNESS['job_age']}"

            urls.append((keyword, page, url))

    return urls


# ============================================================
# Freshness
# ============================================================

def parse_freshness_days(text):
    text = clean_text(text).lower()

    if not text:
        return None

    if any(x in text for x in [
        "just now",
        "today",
        "few hours ago",
        "hour ago",
        "hours ago",
        "minute ago",
        "minutes ago",
    ]):
        if "day" not in text:
            return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)\s*days?\s*ago", text)
    if match:
        return float(match.group(1))

    match = re.search(r"(\d+)\s*hours?\s*ago", text)
    if match:
        return float(match.group(1)) / 24.0

    match = re.search(r"(\d+)\s*minutes?\s*ago", text)
    if match:
        return float(match.group(1)) / 1440.0

    # Some Naukri cards show "1 Day" rather than "1 Day ago".
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*days?\b", text)
    if match:
        return float(match.group(1))

    return None


def freshness_matches(text):
    age = parse_freshness_days(text)

    # If freshness cannot be extracted from a card, the search URL's
    # jobAge filter is the server-side fallback. We do not invent a date.
    if age is None:
        return True

    if FRESHNESS_FILTER in ("current_day", "today"):
        return age == 0.0

    return age <= FRESHNESS["max_days"]


# ============================================================
# Job card parsing
# ============================================================

def extract_job_id(wrapper, url):
    for attr in (
        "data-job-id",
        "data-jobid",
        "data-id",
    ):
        value = wrapper.get(attr)
        if value:
            return clean_text(value)

    # Naukri URLs commonly contain a numeric job identifier.
    match = re.search(r"/([0-9]{8,})(?:[/?#]|$)", url)
    return match.group(1) if match else ""


def extract_card_record(wrapper):
    title = first_matching_text(
        wrapper,
        [
            "a.title",
            "h2 a",
            ".title",
            ".row1 h2",
        ],
    )

    company = first_matching_text(
        wrapper,
        [
            ".comp-name",
            ".companyInfo a",
            ".subTitle",
            ".row2 .comp-name",
        ],
    )

    location = first_matching_text(
        wrapper,
        [
            ".locWdth",
            ".location",
            ".loc",
            ".row3 .locWdth",
        ],
    )

    experience = first_matching_text(
        wrapper,
        [
            ".expwdth",
            ".experience",
            ".exp",
            ".row3 .expwdth",
        ],
    )

    # IMPORTANT:
    # Do not use generic ".row4" as freshness. In the observed CSV it can
    # contain the job description, which caused the old collector to put
    # the description into "Job Posted Date / Freshness".
    freshness = first_matching_text(
        wrapper,
        [
            ".jobAge",
            ".freshness",
            ".posted",
            ".job-posted",
            "[class*='jobAge']",
        ],
    )

    link = wrapper.select_one("a.title, h2 a, a[href*='job-listings']")
    url = normalize_job_url(
        link.get("href", "") if link else ""
    )

    return {
        "Job Title": title,
        "Company Name": company,
        "Location": location,
        "Experience": experience,
        "Skills": "",
        "Job Posted Date / Freshness": freshness,
        "Application Type": "",
        "Job URL": url,
        "Job ID": extract_job_id(wrapper, url),
    }


def collect_cards_from_search_page(driver):
    """
    Read current search page. No Apply action.
    """
    soup = BeautifulSoup(driver.page_source, "html5lib")

    wrappers = soup.select("div.srp-jobtuple-wrapper")

    if not wrappers:
        wrappers = soup.select("div.cust-job-tuple")

    records = []

    for wrapper in wrappers:
        record = extract_card_record(wrapper)

        if not record["Job URL"]:
            continue

        if not record["Job Title"]:
            continue

        if not freshness_matches(record["Job Posted Date / Freshness"]):
            continue

        records.append(record)

    return records


# ============================================================
# Pagination / infinite scroll
# ============================================================

def collect_search_pages(driver, keyword, first_url):
    """
    Collect a search page and its infinite-scroll content.

    For every configured page URL:
      - navigate fresh
      - clear previous in-memory page records
      - repeatedly scroll until the number of cards stops increasing
      - return only records from this search request

    This avoids mixing stale data between different role searches.
    """
    logger.info("SEARCH: %s", keyword)
    logger.info("URL: %s", first_url)

    driver.get(first_url)
    time.sleep(3)

    page_records = []
    seen_on_page = set()
    stable_rounds = 0
    previous_count = 0

    for _ in range(12):
        current = collect_cards_from_search_page(driver)

        for record in current:
            key = (
                record.get("Job ID")
                or record.get("Job URL")
                or (
                    record.get("Job Title", ""),
                    record.get("Company Name", ""),
                )
            )

            if key in seen_on_page:
                continue

            seen_on_page.add(key)
            page_records.append(record)

        current_count = len(page_records)

        if current_count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 2:
            break

        previous_count = current_count

        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        time.sleep(2)

    logger.info(
        "Collected %d freshness-matching cards from search: %s",
        len(page_records),
        keyword,
    )

    return page_records


# ============================================================
# Job detail page
# ============================================================

def extract_detail_text(driver):
    """
    Extract visible job detail text without interacting with any Apply control.
    """
    soup = BeautifulSoup(driver.page_source, "html5lib")

    # Prefer the known Naukri job-description container(s).
    description = first_matching_text(
        soup,
        [
            ".styles_JDC__dang-inner-html__h0K4t",
            ".dang-inner-html",
            ".job-desc",
            "[class*='job-desc']",
            ".jd-container",
        ],
    )

    # Skills commonly appear as tags/labels.
    skill_nodes = soup.select(
        ".key-skill, .skills, .skill, .styles_key-skill__GIPn_ a, "
        "[class*='key-skill'] a"
    )

    skills = []
    for node in skill_nodes:
        value = clean_text(node.get_text(" ", strip=True))
        if value and value not in skills:
            skills.append(value)

    # Fallback: collect visible body text for semantic matching.
    body_text = clean_text(soup.get_text(" ", strip=True))

    if not description:
        description = body_text

    return description, skills, body_text


def extract_detail_metadata(driver, record):
    """
    Fill fields from job detail page while preserving the original job URL.
    """
    description, skills, body_text = extract_detail_text(driver)

    title = first_matching_text(
        BeautifulSoup(driver.page_source, "html5lib"),
        [
            "h1",
            ".jd-header-title",
            ".jd-header-title h1",
        ],
    )

    if title:
        record["Job Title"] = title

    # Keep existing card values if detail page selectors are unavailable.
    if skills:
        record["Skills"] = ", ".join(dict.fromkeys(skills))
    else:
        record["Skills"] = ""

    # Freshness can sometimes be clearer on the detail page.
    detail_soup = BeautifulSoup(driver.page_source, "html5lib")

    detail_freshness = first_matching_text(
        detail_soup,
        [
            ".jobAge",
            ".freshness",
            "[class*='jobAge']",
            "[class*='freshness']",
        ],
    )

    if detail_freshness:
        record["Job Posted Date / Freshness"] = detail_freshness

    return description, skills, body_text


# ============================================================
# Application type — READ ONLY
# ============================================================

def detect_application_type(driver):
    """
    Read application controls/options without clicking them.

    Priority:
      Apply on Company Site
      Apply

    Anything else => excluded.
    """
    candidates = []

    selectors = [
        "button",
        "a",
        "[role='button']",
        "[class*='apply']",
        "[id*='apply']",
    ]

    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            for element in elements:
                try:
                    if not element.is_displayed():
                        continue

                    text = " ".join(
                        [
                            element.text or "",
                            element.get_attribute("aria-label") or "",
                            element.get_attribute("title") or "",
                            element.get_attribute("value") or "",
                        ]
                    )
                    text = clean_text(text).lower()

                    if text:
                        candidates.append(text)

                except StaleElementReferenceException:
                    continue

        except Exception:
            continue

    for text in candidates:
        if (
            "apply on company site" in text
            or "apply on company website" in text
        ):
            return "Apply on Company Site"

    for text in candidates:
        if text == "apply":
            return "Apply"

    # Raw HTML fallback. This still only reads page source.
    source = driver.page_source.lower()

    if "apply on company site" in source:
        return "Apply on Company Site"

    if re.search(r">\s*apply\s*<", source):
        return "Apply"

    return ""


# ============================================================
# Relevance engine
# ============================================================

def evaluate_relevance(record, description, skills):
    """
    Multi-field frontend relevance engine.

    Hard rules:
      1. An explicit excluded role/domain in the title is rejected.
      2. A clear target role must be present in title OR the title must be
         a valid frontend role supported by frontend skills.
      3. At least MIN_FRONTEND_SKILLS relevant technologies must be found.
      4. Generic "developer/software engineer" alone is never enough.

    Score:
      +50 target role in title
      +25 frontend role signal in title
      +10 each matched frontend skill (capped)
      +10 target role in description/skills
      -80 explicit excluded domain in title
      -40 excluded domain elsewhere when it appears to be the primary role
    """
    title = clean_text(record.get("Job Title"))
    company = clean_text(record.get("Company Name"))
    search_text = " ".join(
        [
            title,
            description,
            " ".join(skills or []),
        ]
    )

    matched_roles = [
        role for role in TARGET_ROLES
        if contains_term(title, role)
    ]

    matched_skills = [
        skill for skill in FRONTEND_SKILLS
        if contains_term(search_text, skill)
    ]

    excluded_title = [
        term for term in EXCLUDED_TERMS
        if contains_term(title, term)
    ]

    excluded_any = [
        term for term in EXCLUDED_TERMS
        if contains_term(search_text, term)
    ]

    # Strong explicit title exclusions always win.
    if excluded_title:
        return {
            "score": 0,
            "status": "Excluded",
            "matched_roles": matched_roles,
            "matched_skills": matched_skills,
            "excluded_terms": excluded_title,
        }

    # Explicit target role in title.
    title_role_score = 50 if matched_roles else 0

    frontend_title_signals = [
        "frontend",
        "front end",
        "react",
        "reactjs",
        "react js",
        "nextjs",
        "next js",
        "ui developer",
        "ui engineer",
        "web developer",
    ]

    frontend_title_signal = any(
        contains_term(title, signal)
        for signal in frontend_title_signals
    )

    frontend_title_score = 25 if frontend_title_signal else 0

    # Count only actual frontend technologies.
    skill_score = min(30, len(matched_skills) * 10)

    description_role_match = any(
        contains_term(search_text, role)
        for role in TARGET_ROLES
    )

    description_role_score = 10 if description_role_match else 0

    # Excluded technology elsewhere is not automatically fatal:
    # e.g. "React Developer working with Java APIs" can still be frontend.
    # However, if many exclusion signals dominate and there is no title role,
    # reject it.
    excluded_score = 0

    excluded_non_title = [
        term for term in excluded_any
        if term not in excluded_title
    ]

    if excluded_non_title and not matched_roles and not frontend_title_signal:
        excluded_score = 40

    score = max(
        0,
        title_role_score
        + frontend_title_score
        + skill_score
        + description_role_score
        - excluded_score,
    )

    qualifies = (
        score >= MIN_RELEVANCE_SCORE
        and len(matched_skills) >= MIN_FRONTEND_SKILLS
        and (bool(matched_roles) or frontend_title_signal)
    )

    return {
        "score": score,
        "status": "Relevant" if qualifies else "Excluded",
        "matched_roles": matched_roles,
        "matched_skills": matched_skills,
        "excluded_terms": excluded_non_title,
    }


# ============================================================
# Deduplication
# ============================================================

def deduplicate(records):
    seen_ids = set()
    seen_urls = set()
    result = []

    for record in records:
        url = normalize_job_url(record.get("Job URL", ""))
        job_id = clean_text(record.get("Job ID", ""))

        if not url:
            continue

        url_key = url.lower().rstrip("/")
        id_key = job_id.lower()

        if id_key and id_key in seen_ids:
            continue

        if url_key in seen_urls:
            continue

        record["Job URL"] = url

        if id_key:
            seen_ids.add(id_key)
        seen_urls.add(url_key)

        result.append(record)

    return result


# ============================================================
# CSV
# ============================================================

def export_csv(records):
    rows = []

    for record in records:
        if record.get("Match Status") != "Relevant":
            continue

        if record.get("Application Type") not in (
            "Apply",
            "Apply on Company Site",
        ):
            continue

        url = normalize_job_url(record.get("Job URL", ""))
        if not url:
            continue

        rows.append(
            {
                column: record.get(column, "")
                for column in CSV_COLUMNS
            }
        )

    rows = deduplicate(rows)

    # Fresh DataFrame every run: no stale previous CSV records.
    df = pd.DataFrame(rows, columns=CSV_COLUMNS)

    # Resolve relative output paths beside this Python script.
    output_path = Path(OUTPUT_CSV)
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parent / output_path
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically through a temporary file. If the requested CSV is
    # locked by Excel/another editor on Windows, save a timestamped copy
    # instead of crashing the entire collector.
    temp_path = output_path.with_name(
        f".{output_path.stem}.{uuid.uuid4().hex}.tmp.csv"
    )

    try:
        df.to_csv(temp_path, index=False, encoding="utf-8-sig")
        try:
            os.replace(temp_path, output_path)
        except PermissionError:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            fallback_path = output_path.with_name(
                f"{output_path.stem}_{timestamp}{output_path.suffix}"
            )
            os.replace(temp_path, fallback_path)
            logger.error(
                "Cannot replace '%s' because it is locked or not writable. "
                "Saved the CSV instead to '%s'. Close Excel/another editor "
                "and rerun if you need the original filename.",
                output_path, fallback_path
            )
            output_path = fallback_path
    except PermissionError:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        fallback_path = output_path.with_name(
            f"{output_path.stem}_{timestamp}{output_path.suffix}"
        )
        df.to_csv(fallback_path, index=False, encoding="utf-8-sig")
        logger.error(
            "Permission denied for '%s'. Saved CSV to fallback path '%s'. "
            "Check folder permissions if this continues.",
            output_path, fallback_path
        )
        output_path = fallback_path
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    logger.info("FINAL CSV: %s", output_path)
    logger.info("FINAL VALIDATED JOBS: %d", len(rows))


# ============================================================
# Main
# ============================================================

def main():
    logger.info("=" * 75)
    logger.info("NAUKRI FRONTEND / REACT JOB COLLECTOR")
    logger.info("COLLECTION ONLY - APPLY ACTIONS DISABLED")
    logger.info("=" * 75)

    if not validate_config():
        return

    logger.info("Keywords: %s", KEYWORDS)
    logger.info("Target roles: %s", TARGET_ROLES)
    logger.info("Location: %s", LOCATION or "Anywhere")
    logger.info("Freshness: %s", FRESHNESS_FILTER)
    logger.info("Min relevance score: %d", MIN_RELEVANCE_SCORE)
    logger.info("Min frontend skills: %d", MIN_FRONTEND_SKILLS)
    logger.info("Pages per keyword: %d", PAGES_PER_KEYWORD)

    driver = None

    # Completely fresh run state.
    all_candidates = []
    validated_jobs = []

    try:
        driver = create_edge_driver()
        login_naukri(driver)

        # --------------------------------------------
        # Phase 1: Search and collect
        # --------------------------------------------
        search_urls = build_search_urls()

        logger.info(
            "Generated %d search requests.",
            len(search_urls),
        )

        for keyword, page, url in search_urls:
            try:
                records = collect_search_pages(
                    driver,
                    keyword,
                    url,
                )

                for record in records:
                    record["_Search Keyword"] = keyword
                    record["_Search Page"] = page
                    all_candidates.append(record)

            except WebDriverException as exc:
                logger.warning(
                    "Search failed: %s page %d: %s",
                    keyword,
                    page,
                    exc,
                )

        logger.info(
            "Raw freshness-matching candidates: %d",
            len(all_candidates),
        )

        # --------------------------------------------
        # Phase 2: Global dedup before detail requests
        # --------------------------------------------
        all_candidates = deduplicate(all_candidates)

        logger.info(
            "Unique candidates before relevance validation: %d",
            len(all_candidates),
        )

        # --------------------------------------------
        # Phase 3: Relevance + application type
        # --------------------------------------------
        for index, record in enumerate(
            all_candidates,
            start=1,
        ):
            if MAX_JOBS and len(validated_jobs) >= MAX_JOBS:
                break

            logger.info(
                "[%d/%d] Checking: %s",
                index,
                len(all_candidates),
                record["Job Title"],
            )

            try:
                driver.get(record["Job URL"])
                time.sleep(1.5)

                description, skills, _ = extract_detail_metadata(
                    driver,
                    record,
                )

                # Freshness validation on detail page as well.
                if not freshness_matches(
                    record.get("Job Posted Date / Freshness", "")
                ):
                    logger.info("  - Excluded: freshness")
                    continue

                relevance = evaluate_relevance(
                    record,
                    description,
                    skills,
                )

                record["Relevance Score"] = relevance["score"]
                record["Match Status"] = relevance["status"]
                record["Matched Roles"] = ", ".join(
                    relevance["matched_roles"]
                )
                record["Matched Skills"] = ", ".join(
                    relevance["matched_skills"]
                )
                record["Excluded Terms Found"] = ", ".join(
                    relevance["excluded_terms"]
                )

                if relevance["status"] != "Relevant":
                    logger.info(
                        "  - Excluded: relevance score=%s | title=%s",
                        relevance["score"],
                        record["Job Title"],
                    )
                    continue

                # READ ONLY: classify the application option.
                application_type = detect_application_type(driver)
                record["Application Type"] = application_type

                if application_type not in (
                    "Apply",
                    "Apply on Company Site",
                ):
                    logger.info(
                        "  - Excluded: unsupported application option"
                    )
                    continue

                validated_jobs.append(record)

                logger.info(
                    "  ✓ INCLUDED: %s | score=%s | app=%s",
                    record["Job Title"],
                    relevance["score"],
                    application_type,
                )

            except WebDriverException as exc:
                logger.warning(
                    "  - Detail page failed: %s",
                    exc,
                )

        # --------------------------------------------
        # Phase 4: Final dedup + CSV
        # --------------------------------------------
        validated_jobs = deduplicate(validated_jobs)

        export_csv(validated_jobs)

        logger.info("=" * 75)
        logger.info("COLLECTION COMPLETE")
        logger.info("Raw candidates: %d", len(all_candidates))
        logger.info("Validated frontend jobs: %d", len(validated_jobs))
        logger.info("No Apply button was clicked.")
        logger.info("No Apply on Company Site button was clicked.")
        logger.info("=" * 75)

    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)

    finally:
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed.")
            except Exception:
                pass


if __name__ == "__main__":
    main()
