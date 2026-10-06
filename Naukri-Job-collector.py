"""
Naukri Job Collector - Human-style search / NO AUTO APPLY

Purpose:
- Search Naukri for React.js / Frontend jobs.
- Supports multiple locations.
- Filters jobs for 4+ years experience.
- No freshness/date filter.
- DOES NOT click Apply or Apply on company site.
- Opens job pages in a visible Edge browser and exports matching jobs to CSV.

CSV columns:
Job Title, Company Name, Location, Experience, Application Type, Job URL
"""

import os
import re
import time
import random
import logging
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.common.exceptions import WebDriverException

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False


# ============================================================
# Configuration
# ============================================================
load_dotenv()

# Comma-separated React/frontend search terms.
KEYWORDS = [
    x.strip()
    for x in os.getenv(
        "KEYWORDS",
        "React JS,React.js Developer,React Developer,Frontend Developer,"
        "Senior Frontend Developer,UI Developer,UI Engineer,Next.js Developer"
    ).split(",")
    if x.strip()
]

# Multiple locations are supported.
LOCATIONS = [
    x.strip()
    for x in os.getenv(
        "LOCATIONS",
        "Chennai,Coimbatore,Bangalore,Remote"
    ).split(",")
    if x.strip()
]

# Minimum experience. 4 means 4+ years.
MIN_EXPERIENCE = float(os.getenv("MIN_EXPERIENCE", "4"))

# Number of Naukri search result pages per keyword/location.
PAGES_PER_SEARCH = int(os.getenv("PAGES_PER_SEARCH", "2"))

# Maximum number of unique jobs to inspect.
MAX_JOBS = int(os.getenv("MAX_JOBS", "300"))

# CSV output.
CSV_FILE = os.getenv("CSV_FILE", "naukri_react_jobs.csv")

# Optional EdgeDriver path if webdriver-manager is not installed.
EDGE_DRIVER_PATH = os.getenv("EDGE_DRIVER_PATH", "")

# Optional Naukri login. Login is NOT required for the collector logic.
# Leave blank if you want to search without logging in.
NAUKRI_EMAIL = os.getenv("NAUKRI_EMAIL", "")
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "")

# Human-like pauses. Browser stays visible.
MIN_DELAY = float(os.getenv("MIN_DELAY", "2"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "4"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# Browser
# ============================================================
def create_edge_driver():
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    # IMPORTANT: no headless mode. The browser remains visible.
    # options.add_argument("--headless=new")

    if WEBDRIVER_MANAGER_AVAILABLE:
        service = EdgeService(EdgeChromiumDriverManager().install())
    elif EDGE_DRIVER_PATH:
        service = EdgeService(executable_path=EDGE_DRIVER_PATH)
    else:
        service = EdgeService()

    return webdriver.Edge(service=service, options=options)


def human_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ============================================================
# Optional login
# ============================================================
def login_if_configured(driver):
    """
    Login only when credentials are supplied.
    This function NEVER applies to a job.
    """
    if not NAUKRI_EMAIL or not NAUKRI_PASSWORD:
        logger.info("Naukri credentials not supplied. Continuing without login.")
        return

    try:
        logger.info("Opening Naukri login page...")
        driver.get("https://login.naukri.com/")
        time.sleep(3)

        username = driver.find_element(By.ID, "usernameField")
        password = driver.find_element(By.ID, "passwordField")

        username.clear()
        username.send_keys(NAUKRI_EMAIL)

        password.clear()
        password.send_keys(NAUKRI_PASSWORD)
        password.submit()

        time.sleep(5)
        logger.info("Naukri login completed.")
    except Exception as exc:
        logger.warning("Login could not be completed: %s", exc)
        logger.info("Continuing with public/search pages.")


# ============================================================
# Search URLs
# ============================================================
def slug(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def build_search_urls():
    """
    Build one search URL for every keyword + location + page.

    No freshness filter is used.
    Experience is filtered from the job details after collection.
    """
    urls = []

    for keyword in KEYWORDS:
        keyword_slug = slug(keyword)

        for location in LOCATIONS:
            location_slug = slug(location)

            for page in range(1, PAGES_PER_SEARCH + 1):
                if page == 1:
                    url = (
                        f"https://www.naukri.com/"
                        f"{keyword_slug}-jobs-in-{location_slug}"
                    )
                else:
                    url = (
                        f"https://www.naukri.com/"
                        f"{keyword_slug}-jobs-in-{location_slug}-{page}"
                    )

                urls.append((keyword, location, page, url))

    return urls


# ============================================================
# Search result extraction
# ============================================================
def collect_job_links(driver, search_url):
    """
    Open one search page and collect job-card URLs.

    Only job links are collected here.
    No Apply button is clicked.
    """
    links = []

    try:
        logger.info("Opening search: %s", search_url)
        driver.get(search_url)
        human_delay()

        # Small scroll to allow lazy-loaded cards to render.
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.65);")
        human_delay()

        soup = BeautifulSoup(driver.page_source, "html.parser")

        wrappers = soup.select("div.srp-jobtuple-wrapper")
        if not wrappers:
            wrappers = soup.select("div.cust-job-tuple")

        for wrapper in wrappers:
            title_link = wrapper.select_one("a.title")
            if not title_link:
                title_link = wrapper.select_one("a[href*='/job-listings-']")

            if not title_link:
                continue

            href = title_link.get("href")
            if not href:
                continue

            if href.startswith("/"):
                href = "https://www.naukri.com" + href

            links.append(href.split("?")[0])

        logger.info("Found %d links on search page.", len(links))

    except WebDriverException as exc:
        logger.warning("Search page error: %s", exc)

    return links


# ============================================================
# Job detail extraction
# ============================================================
def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def first_text(soup, selectors):
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                return value
    return ""


def extract_experience(text):
    """
    Convert common Naukri experience strings into (min_years, display_text).

    Examples:
      4-8 Yrs       -> 4
      5-10 Yrs      -> 5
      4+ Yrs        -> 4
      3-7 Yrs       -> 3
    """
    if not text:
        return None

    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*\+?\s*(?:yrs?|years?)",
        r"(\d+(?:\.\d+)?)\s*\+\s*(?:yrs?|years?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                pass

    return None


def detect_application_type(soup):
    """
    Detect application type WITHOUT clicking anything.

    Possible CSV values:
      Apply
      Apply on Company Site
      Apply / Apply on Company Site
      Not Found
    """
    # Buttons/links are inspected as text only.
    candidates = soup.select(
        "button, a, [role='button'], div[class*='apply'], "
        "div[id*='apply'], span[class*='apply']"
    )

    found_apply = False
    found_company = False

    for node in candidates:
        text = clean_text(node.get_text(" ", strip=True)).lower()

        if not text:
            continue

        if "apply on company site" in text or "company site" in text:
            found_company = True

        # Exact-ish "Apply", while avoiding "Applied".
        if re.search(r"\bapply\b", text) and "company site" not in text:
            found_apply = True

    if found_apply and found_company:
        return "Apply / Apply on Company Site"
    if found_company:
        return "Apply on Company Site"
    if found_apply:
        return "Apply"

    return "Not Found"


def extract_job_details(driver, url):
    """
    Visit a job detail page and read information only.
    NEVER clicks Apply.
    """
    try:
        logger.info("Inspecting job: %s", url)
        driver.get(url)
        human_delay()

        soup = BeautifulSoup(driver.page_source, "html.parser")

        title = first_text(
            soup,
            [
                "h1.styles_jd-header-title__rZwM1",
                "h1[class*='jd-header-title']",
                "h1",
            ],
        )

        company = first_text(
            soup,
            [
                "a[class*='jd-header-comp-name']",
                "div[class*='jd-header-comp-name']",
                "a[class*='comp-name']",
            ],
        )

        location = first_text(
            soup,
            [
                "span[class*='location']",
                "div[class*='location']",
                "span[class*='loc']",
            ],
        )

        experience_text = first_text(
            soup,
            [
                "div[class*='exp']",
                "span[class*='exp']",
                "div[class*='experience']",
                "span[class*='experience']",
            ],
        )

        # Fallback: inspect the visible page text around common experience labels.
        page_text = clean_text(soup.get_text(" ", strip=True))
        if not experience_text:
            match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*\+?\s*(?:yrs?|years?)",
                page_text,
                flags=re.I,
            )
            if match:
                experience_text = match.group(0)

        min_exp = extract_experience(experience_text)

        application_type = detect_application_type(soup)

        # If the detail page does not expose an experience value, do not
        # incorrectly classify it as 4+.
        if min_exp is None:
            return None

        if min_exp < MIN_EXPERIENCE:
            logger.info(
                "Skipped: experience %s is below %.1f+.",
                experience_text,
                MIN_EXPERIENCE,
            )
            return None

        return {
            "Job Title": title or "Not Found",
            "Company Name": company or "Not Found",
            "Location": location or "Not Found",
            "Experience": experience_text or "Not Found",
            "Application Type": application_type,
            "Job URL": url,
        }

    except WebDriverException as exc:
        logger.warning("Job page error: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Could not parse job: %s", exc)
        return None


# ============================================================
# CSV
# ============================================================
def save_results(rows):
    columns = [
        "Job Title",
        "Company Name",
        "Location",
        "Experience",
        "Application Type",
        "Job URL",
    ]

    df = pd.DataFrame(rows, columns=columns)

    if not df.empty:
        df = df.drop_duplicates(subset=["Job URL"])
        df = df.sort_values(
            by=["Job Title", "Company Name"],
            na_position="last"
        )

    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    logger.info("Saved %d jobs to %s", len(df), CSV_FILE)


# ============================================================
# Main collector
# ============================================================
def main():
    logger.info("=" * 70)
    logger.info("NAUKRI REACT JOB COLLECTOR - NO AUTO APPLY")
    logger.info("=" * 70)
    logger.info("Keywords: %s", KEYWORDS)
    logger.info("Locations: %s", LOCATIONS)
    logger.info("Minimum experience: %.1f+ years", MIN_EXPERIENCE)
    logger.info("Freshness filter: NONE")
    logger.info("Auto apply: DISABLED")
    logger.info("=" * 70)

    driver = None

    try:
        driver = create_edge_driver()

        # Optional login only. No application action exists anywhere below.
        login_if_configured(driver)

        search_urls = build_search_urls()
        logger.info("Total search pages: %d", len(search_urls))

        # First collect URLs.
        unique_urls = []
        seen = set()

        for keyword, location, page, url in search_urls:
            logger.info(
                "SEARCH | keyword=%s | location=%s | page=%d",
                keyword,
                location,
                page,
            )

            links = collect_job_links(driver, url)

            for link in links:
                if link not in seen:
                    seen.add(link)
                    unique_urls.append(link)

                if len(unique_urls) >= MAX_JOBS:
                    break

            if len(unique_urls) >= MAX_JOBS:
                break

        logger.info("Unique job URLs collected: %d", len(unique_urls))

        if not unique_urls:
            logger.warning("No job URLs found.")
            return

        # Then inspect each job detail page.
        results = []

        for index, job_url in enumerate(unique_urls, start=1):
            logger.info(
                "[%d/%d] Checking job details...",
                index,
                len(unique_urls),
            )

            job = extract_job_details(driver, job_url)

            if job:
                results.append(job)
                logger.info(
                    "MATCHED | %s | %s | %s | %s | %s",
                    job["Job Title"],
                    job["Company Name"],
                    job["Location"],
                    job["Experience"],
                    job["Application Type"],
                )

        save_results(results)

        logger.info("=" * 70)
        logger.info("COLLECTION COMPLETED")
        logger.info("Matching React/frontend jobs: %d", len(results))
        logger.info("CSV: %s", CSV_FILE)
        logger.info("No Apply button was clicked.")
        logger.info("No application was submitted.")
        logger.info("=" * 70)

    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
