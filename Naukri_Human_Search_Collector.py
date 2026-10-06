"""
Naukri Human-Search Job Collector
=================================
Purpose:
- Open Naukri in Microsoft Edge.
- You perform login/search/filtering manually in the normal browser.
- When the job-results page is visible, press Enter in this terminal.
- The script reads the jobs currently displayed and exports their URLs/details to CSV.
- No auto-apply, no automated login, no automated clicking of Apply, no quota handling.

This is intentionally human-in-the-loop. It does not try to disguise automation
or bypass Naukri anti-bot/security controls.

Usage:
    pip install selenium pandas webdriver-manager
    python Naukri_Human_Search_Collector.py

On each results page:
    1. Search/filter normally in Edge.
    2. Scroll the page manually if you want more cards loaded.
    3. Press Enter in the terminal to collect the visible job cards.
    4. Move to the next page manually and press Enter again.
    5. Type q + Enter when finished.

CSV:
    naukri_jobs.csv
"""

import os
import time
import logging
import pandas as pd

from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.common.exceptions import WebDriverException

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False


CSV_FILE = os.getenv("NAUKRI_CSV_FILE", "naukri_jobs.csv")
EDGE_DRIVER_PATH = os.getenv("EDGE_DRIVER_PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_edge_driver():
    """Open a normal visible Edge browser. No headless/stealth options."""
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")

    if WEBDRIVER_MANAGER_AVAILABLE:
        service = EdgeService(EdgeChromiumDriverManager().install())
    elif EDGE_DRIVER_PATH:
        service = EdgeService(executable_path=EDGE_DRIVER_PATH)
    else:
        service = EdgeService()

    return webdriver.Edge(service=service, options=options)


def text_from(wrapper, selectors):
    """Return the first non-empty text matching one of the CSS selectors."""
    for selector in selectors:
        try:
            elements = wrapper.find_elements("css selector", selector)
            for element in elements:
                value = element.text.strip()
                if value:
                    return value
        except Exception:
            continue
    return ""


def attr_from(wrapper, selectors, attribute):
    """Return the first non-empty attribute matching one of the CSS selectors."""
    for selector in selectors:
        try:
            elements = wrapper.find_elements("css selector", selector)
            for element in elements:
                value = element.get_attribute(attribute)
                if value:
                    return value.strip()
        except Exception:
            continue
    return ""


def normalize_url(url):
    if not url:
        return ""
    if url.startswith("/"):
        return "https://www.naukri.com" + url
    return url


def collect_visible_jobs(driver):
    """
    Collect job cards currently rendered in the browser.

    No keyword/role/location/experience/freshness/application-type filtering
    is performed by this script. Whatever Naukri shows after the user's
    manual search is what gets collected.
    """
    selectors = [
        "div.srp-jobtuple-wrapper",
        "div.cust-job-tuple",
        "article.jobTuple",
    ]

    cards = []
    for selector in selectors:
        try:
            cards = driver.find_elements("css selector", selector)
            if cards:
                break
        except Exception:
            pass

    logger.info("Visible job cards found: %d", len(cards))

    jobs = []
    for card in cards:
        title = text_from(card, [
            "a.title",
            "h2 a",
            "a[href*='/job-listings/']",
        ])

        company = text_from(card, [
            "a.comp-name",
            ".comp-name",
            ".row2 a",
            "[class*='company']",
        ])

        location = text_from(card, [
            ".locWdth",
            ".loc",
            "[class*='loc']",
        ])

        experience = text_from(card, [
            ".expwdth",
            ".exp",
            "[class*='exp']",
        ])

        posted = text_from(card, [
            ".job-post-day",
            ".job-posted",
            "[class*='job-post']",
        ])

        url = attr_from(card, [
            "a.title",
            "h2 a",
            "a[href*='/job-listings/']",
        ], "href")

        url = normalize_url(url)

        # Title + URL are the minimum useful identifiers.
        if title and url:
            jobs.append({
                "Job Title": title,
                "Company Name": company,
                "Location": location,
                "Experience": experience,
                "Posted": posted,
                "Job URL": url,
                "Collected From": driver.current_url,
                "Collected At": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

    return jobs


def save_jobs(jobs):
    """Append jobs to CSV and remove duplicates by Job URL."""
    if not jobs:
        return

    new_df = pd.DataFrame(jobs)

    if os.path.exists(CSV_FILE):
        try:
            old_df = pd.read_csv(CSV_FILE)
            df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            df = new_df
    else:
        df = new_df

    if "Job URL" in df.columns:
        df = df.drop_duplicates(subset=["Job URL"], keep="first")

    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    logger.info("Saved %d unique jobs to %s", len(df), CSV_FILE)


def main():
    logger.info("=" * 65)
    logger.info("Naukri Human-Search Job Collector")
    logger.info("=" * 65)
    logger.info("No auto-login, no auto-apply, no search filters, no stealth.")
    logger.info("You control the Edge browser and perform the search manually.")

    driver = None
    try:
        driver = create_edge_driver()
        driver.get("https://www.naukri.com/")
        time.sleep(2)

        print("\nEdge is open.")
        print("1. Login manually if needed.")
        print("2. Search for any job normally on Naukri.")
        print("3. Apply any filters yourself in the Naukri UI.")
        print("4. Scroll manually if you want more jobs loaded.")
        print("5. Return to this terminal and press ENTER to collect.")
        print("6. Go to another results page/search manually and press ENTER again.")
        print("7. Type q and press ENTER when finished.\n")

        while True:
            command = input("Press ENTER to collect this page, or q + ENTER to quit: ")

            if command.strip().lower() == "q":
                break

            jobs = collect_visible_jobs(driver)

            if not jobs:
                logger.warning(
                    "No job cards detected. Make sure a Naukri job-results page "
                    "is open and the cards are visible."
                )
                continue

            save_jobs(jobs)
            logger.info("Collected %d jobs from the current page.", len(jobs))
            print(f"Collected {len(jobs)} jobs. CSV: {CSV_FILE}\n")

            print("Continue manually in Edge. Press ENTER after another results page is ready.")

    except WebDriverException as exc:
        logger.error("Edge/Selenium error: %s", exc)
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Finished. Job URLs are in: %s", CSV_FILE)


if __name__ == "__main__":
    main()
