"""
Naukri Recommended Jobs Collector - Edge/Selenium
--------------------------------------------------
COLLECTION ONLY:
- Does NOT click Apply.
- Does NOT click Apply on Company Site.
- Collects recommended jobs from the selected Naukri tab.
- Reads application type without triggering it.
- Applies the selected freshness filter locally.
- Removes duplicates and exports CSV.

Environment:
    NAUKRI_EMAIL
    NAUKRI_PASSWORD
    FRESHNESS_FILTER=last_1_day | last_3_days | current_day
    OUTPUT_CSV=naukri_recommended_jobs.csv

Run:
    python Naukri-Recommended-Collector.py
"""

import os
import re
import time
import logging
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False


load_dotenv()

NAUKRI_EMAIL = os.getenv("NAUKRI_EMAIL", "").strip()
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "").strip()
FRESHNESS_FILTER = os.getenv("FRESHNESS_FILTER", "last_1_day").strip().lower()
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    "naukri_recommended_jobs.csv"
).strip()

FRESHNESS_CONFIG = {
    "last_1_day": {"max_days": 1},
    "1_day": {"max_days": 1},
    "last_3_days": {"max_days": 3},
    "3_days": {"max_days": 3},
    "current_day": {"max_days": 0},
    "today": {"max_days": 0},
}

if FRESHNESS_FILTER not in FRESHNESS_CONFIG:
    raise ValueError(
        "FRESHNESS_FILTER must be last_1_day, last_3_days, or current_day."
    )

TABS = [
    {"num": 1, "id": "apply", "label": "Applies"},
    {"num": 2, "id": "profile", "label": "Profile"},
    {"num": 3, "id": "top_candidate", "label": "Top Candidate"},
    {"num": 4, "id": "preference", "label": "Preferences"},
    {"num": 5, "id": "similar_jobs", "label": "You might like"},
]

CSV_COLUMNS = [
    "Job Title",
    "Company Name",
    "Location",
    "Experience",
    "Job Posted Date / Freshness",
    "Application Type",
    "Job URL",
    "Job ID",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_edge_driver():
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")

    if WEBDRIVER_MANAGER_AVAILABLE:
        service = EdgeService(EdgeChromiumDriverManager().install())
    else:
        service = EdgeService()

    return webdriver.Edge(service=service, options=options)


def login_naukri(driver):
    driver.get("https://login.naukri.com/")

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "usernameField"))
        )
    except TimeoutException:
        logger.info("Login form not visible; existing session may be active.")
        return

    driver.find_element(By.ID, "usernameField").send_keys(NAUKRI_EMAIL)
    password = driver.find_element(By.ID, "passwordField")
    password.send_keys(NAUKRI_PASSWORD)
    password.send_keys(Keys.ENTER)

    time.sleep(6)

    if "login" in driver.current_url.lower():
        logger.warning("Login page is still open. Verify credentials/OTP/CAPTCHA.")
    else:
        logger.info("Naukri session established.")


def normalize_job_url(url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("/"):
        url = "https://www.naukri.com" + url
    elif url.startswith("//"):
        url = "https:" + url

    parts = urlsplit(url)

    if not parts.scheme or not parts.netloc:
        return ""

    clean = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            "",
            "",
        )
    )

    if not clean.startswith(("https://www.naukri.com/", "https://naukri.com/")):
        return ""

    return clean


def parse_freshness_days(text):
    if not text:
        return None

    value = " ".join(text.lower().split())

    if any(x in value for x in [
        "just now",
        "today",
        "few hours",
        "hour ago",
        "hours ago",
    ]):
        return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)\s*days?\s*ago", value)
    if match:
        return float(match.group(1))

    match = re.search(r"(\d+)\s*hours?\s*ago", value)
    if match:
        return float(match.group(1)) / 24.0

    match = re.search(r"(\d+)\s*minutes?\s*ago", value)
    if match:
        return float(match.group(1)) / 1440.0

    return None


def freshness_matches(text):
    age = parse_freshness_days(text)

    # If the recommended card does not expose freshness text,
    # do not invent a date. Keep it only when the text is unavailable;
    # application type is still verified separately.
    if age is None:
        return True

    if FRESHNESS_FILTER in ("current_day", "today"):
        return age == 0.0

    return age <= FRESHNESS_CONFIG[FRESHNESS_FILTER]["max_days"]


def select_tabs():
    print("\n" + "=" * 60)
    print("Select Recommended Jobs tab(s) to collect:")
    for tab in TABS:
        print(f"  {tab['num']}. {tab['label']}")
    print("=" * 60)

    raw = input(
        "Enter numbers (e.g. 1,3,5) or press Enter for Top Candidate: "
    ).strip()

    if not raw:
        return [TABS[2]]

    selected = []

    for part in raw.split(","):
        if part.strip().isdigit():
            index = int(part.strip()) - 1
            if 0 <= index < len(TABS):
                selected.append(TABS[index])

    return selected or [TABS[2]]


def navigate_to_tab(driver, tab_id):
    driver.get("https://www.naukri.com/mnjuser/recommendedjobs")

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".tab-list"))
        )
        time.sleep(2)

        tab_el = driver.find_element(
            By.CSS_SELECTOR,
            f"div#{tab_id} .tab-list-item"
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            tab_el,
        )

        # This is a navigation/tab-selection click, NOT an Apply click.
        driver.execute_script("arguments[0].click();", tab_el)
        time.sleep(3)

    except Exception as exc:
        logger.warning("Could not activate tab '%s': %s", tab_id, exc)


def extract_job_cards(driver):
    soup = BeautifulSoup(driver.page_source, "html5lib")

    cards = soup.select("article.jobTuple")

    if not cards:
        cards = soup.select(
            "article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple"
        )

    records = []

    for card in cards:
        title_el = card.select_one(
            ".title, .title.typ-16Bold, h2 a, a.title"
        )
        company_el = card.select_one(
            ".comp-name, .companyInfo a, .subTitle"
        )
        location_el = card.select_one(
            ".locWdth, .location, .loc"
        )
        experience_el = card.select_one(
            ".expwdth, .experience, .exp"
        )

        title = title_el.get_text(" ", strip=True) if title_el else ""
        company = company_el.get_text(" ", strip=True) if company_el else ""
        location = location_el.get_text(" ", strip=True) if location_el else ""
        experience = experience_el.get_text(" ", strip=True) if experience_el else ""

        link_el = card.select_one("a.title, h2 a, a[href*='job']")
        url = normalize_job_url(
            link_el.get("href", "") if link_el else ""
        )

        text = card.get_text(" ", strip=True)
        freshness_match = re.search(
            r"(just now|today|few hours ago|\d+\s+(?:hour|hours|day|days)\s+ago)",
            text,
            flags=re.I,
        )
        freshness = freshness_match.group(1) if freshness_match else ""

        if not url:
            continue

        if not freshness_matches(freshness):
            continue

        job_id = (
            card.get("data-job-id")
            or card.get("data-jobid")
            or ""
        )

        records.append({
            "Job Title": title,
            "Company Name": company,
            "Location": location,
            "Experience": experience,
            "Job Posted Date / Freshness": freshness,
            "Job URL": url,
            "Job ID": str(job_id).strip(),
        })

    return records


def detect_application_type(driver):
    """
    READ ONLY.
    No Apply control is clicked.
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
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if not element.is_displayed():
                        continue

                    text = " ".join([
                        element.text or "",
                        element.get_attribute("aria-label") or "",
                        element.get_attribute("title") or "",
                        element.get_attribute("value") or "",
                    ]).strip().lower()

                    if text:
                        candidates.append(text)
                except Exception:
                    continue
        except Exception:
            continue

    for text in candidates:
        if "apply on company site" in text or "apply on company website" in text:
            return "Apply on Company Site"

    for text in candidates:
        if re.sub(r"\s+", " ", text).strip() == "apply":
            return "Apply"

    return ""


def inspect_application_type(driver, record):
    try:
        driver.get(record["Job URL"])
        time.sleep(2)

        app_type = detect_application_type(driver)

        if app_type:
            record["Application Type"] = app_type
            return record

        # Read page source only; never click.
        source = driver.page_source.lower()

        if "apply on company site" in source:
            record["Application Type"] = "Apply on Company Site"
        elif re.search(r">\s*apply\s*<", source):
            record["Application Type"] = "Apply"

    except WebDriverException as exc:
        logger.warning(
            "Could not inspect application option: %s",
            exc,
        )

    record["Application Type"] = record.get("Application Type", "")
    return record


def deduplicate(records):
    seen_urls = set()
    seen_ids = set()
    result = []

    for record in records:
        url = normalize_job_url(record.get("Job URL", ""))
        job_id = str(record.get("Job ID", "")).strip()

        if not url:
            continue

        if url.lower().rstrip("/") in seen_urls:
            continue

        if job_id and job_id in seen_ids:
            continue

        record["Job URL"] = url

        seen_urls.add(url.lower().rstrip("/"))
        if job_id:
            seen_ids.add(job_id)

        result.append(record)

    return result


def export_csv(records):
    filtered = [
        record
        for record in records
        if record.get("Application Type")
        in ("Apply", "Apply on Company Site")
        and normalize_job_url(record.get("Job URL", ""))
    ]

    filtered = deduplicate(filtered)

    df = pd.DataFrame(filtered, columns=CSV_COLUMNS)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    logger.info("Exported %d unique jobs to %s", len(filtered), OUTPUT_CSV)


def main():
    logger.info("=" * 70)
    logger.info("NAUKRI RECOMMENDED JOB COLLECTOR - NO APPLY ACTION")
    logger.info("=" * 70)

    if not NAUKRI_EMAIL or not NAUKRI_PASSWORD:
        logger.error("Set NAUKRI_EMAIL and NAUKRI_PASSWORD in .env")
        return

    selected_tabs = select_tabs()

    driver = None
    all_records = []

    try:
        driver = create_edge_driver()
        login_naukri(driver)

        for tab in selected_tabs:
            logger.info("Collecting tab: %s", tab["label"])

            navigate_to_tab(driver, tab["id"])

            records = extract_job_cards(driver)
            records = deduplicate(records)

            logger.info(
                "Freshness-matching unique cards in '%s': %d",
                tab["label"],
                len(records),
            )

            for index, record in enumerate(records, start=1):
                logger.info(
                    "[%d/%d] Inspecting application option: %s",
                    index,
                    len(records),
                    record["Job URL"],
                )

                record["Application Type"] = ""
                record = inspect_application_type(driver, record)

                if record["Application Type"] in (
                    "Apply",
                    "Apply on Company Site",
                ):
                    all_records.append(record)
                    logger.info(
                        "  ✓ Included: %s | %s",
                        record["Application Type"],
                        record["Job Title"],
                    )

        all_records = deduplicate(all_records)
        export_csv(all_records)

        logger.info("=" * 70)
        logger.info("COLLECTION COMPLETE")
        logger.info("No Apply action was executed.")
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
