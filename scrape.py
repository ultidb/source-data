import atexit
import logging as log
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from tor import torIsRunning, startTorServer


# Singleton Chrome driver for Selenium requests
_selenium_driver = None


def getSeleniumDriver():
    """Get or create the singleton Chrome driver instance"""
    global _selenium_driver

    if _selenium_driver is None:
        log.debug("Initializing Chrome driver")

        if not torIsRunning():
            tor_process = startTorServer()
            atexit.register(tor_process.kill)

        chrome_options = Options()
        # Configure Tor SOCKS5 proxy
        chrome_options.add_argument("--proxy-server=socks5://127.0.0.1:9050")
        # Additional options for headless Chrome
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        _selenium_driver = webdriver.Chrome(options=chrome_options)
        _selenium_driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Register cleanup on exit
        atexit.register(cleanupSeleniumDriver)

    return _selenium_driver


def cleanupSeleniumDriver():
    """Cleanup the singleton Chrome driver on exit"""
    global _selenium_driver
    if _selenium_driver is not None:
        log.debug("Cleaning up Chrome driver")
        try:
            _selenium_driver.quit()
        except:
            pass
        _selenium_driver = None


def extractBreadcrumbTournamentSlug(page_source):
    """The tournament slug named in `page_source`'s breadcrumb nav (second
    link's href, e.g. ".../events/<slug>/" -> "<slug>"), or None if there's
    no breadcrumb nav, it has fewer than two links, or the second link has
    no href -- all of which indicate a blank, failed, or otherwise unusable
    page load (see makeProxiedRequestSelenium's content validation)."""
    soup = BeautifulSoup(page_source, "html.parser")
    breadcrumbs = soup.find("div", {"class": "breadcrumbs"})
    if not breadcrumbs:
        return None
    tournament_links = breadcrumbs.find_all("a")
    if len(tournament_links) < 2 or not tournament_links[1].get("href"):
        return None
    return tournament_links[1]["href"].rstrip("/").split("/")[-1]


def expectedTournamentSlugFromUrl(url):
    """The tournament slug a schedule-page URL should resolve to (the path
    segment right after "events/"), or None if `url` isn't a schedule page.

    Only tournament *schedule* pages (".../events/<slug>/schedule/...",
    built by parseNewSchedule) carry a breadcrumb linking back to
    "events/<slug>" -- that's what makeProxiedRequestSelenium's content
    validation checks the fetched page against. Team pages
    (".../events/teams/?EventTeamId=...", from convertTeamLinkToTeam) also
    contain "/events/" but aren't schedule pages and have no such breadcrumb
    to check; requiring "/schedule/" too excludes them, so
    expected_tournament_slug is None and validation is skipped for them, as
    it always has been (a real team page never carries an events/<slug>
    breadcrumb, so validating it against one would always "fail")."""
    if "/events/" not in url or "/schedule/" not in url:
        return None
    parts = url.split("/")
    for i, part in enumerate(parts):
        if part == "events" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def makeProxiedRequestSelenium(url):
    log.debug(f"Making proxied Selenium request to {url}")

    driver = getSeleniumDriver()

    expected_tournament_slug = expectedTournamentSlugFromUrl(url)

    max_retries = 3
    for retry in range(max_retries):
        try:
            # Store the current page source before navigating to detect when it changes
            old_page_source = driver.page_source if driver.current_url != "data:," else ""

            driver.get(url)

            # Wait for the page content to actually change by checking that the URL loaded
            # and the page source is different from before
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.current_url == url or url in d.current_url
                )
            except Exception:
                log.warning(f"URL did not fully load: expected {url}, got {driver.current_url}")

            # Additional wait for page content to fully load before reading page_source.
            # Without this wait, the driver may return stale content from the
            # previous page, causing tournament data to be written to wrong files.
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "breadcrumbs"))
                )
            except Exception:
                # If breadcrumbs not found, wait for any content indicator
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except Exception:
                    pass

            # Wait for the page source to actually change from the previous page
            max_attempts = 20
            for attempt in range(max_attempts):
                current_page_source = driver.page_source
                if current_page_source != old_page_source:
                    break
                time.sleep(0.1)
            else:
                log.warning(f"Page source did not change after {max_attempts} attempts for URL: {url}")

            # Small delay to ensure dynamic content has loaded
            time.sleep(0.5)

            # Get the page source
            page_source = driver.page_source

            # CRITICAL: Validate that the loaded content matches the requested tournament.
            # A failed/blank navigation (empty <body>, no breadcrumbs at all) must be
            # treated as a validation failure exactly like a slug mismatch -- previously
            # `if breadcrumbs:` silently skipped validation entirely when breadcrumbs
            # were missing, letting an empty `<html><head></head><body></body></html>`
            # page through as if it were a successful fetch.
            if expected_tournament_slug:
                actual_slug = extractBreadcrumbTournamentSlug(page_source)

                if actual_slug != expected_tournament_slug:
                    got_description = repr(actual_slug) if actual_slug is not None else "no breadcrumbs (blank/failed page load)"
                    log.warning(
                        f"Content validation failed on attempt {retry + 1}/{max_retries} for {url}: "
                        f"expected tournament '{expected_tournament_slug}' but got {got_description}. "
                        f"Retrying..."
                    )
                    if retry < max_retries - 1:
                        # Force reload by adding a small delay and clearing any cached state
                        time.sleep(1)
                        continue
                    else:
                        log.error(
                            f"Failed to load valid content for {url} after {max_retries} attempts "
                            f"(expected tournament '{expected_tournament_slug}')"
                        )
                        raise Exception("Tournament content validation failed")

            # Convert to bytes to match the return type of makeProxiedRequest
            return page_source.encode('utf-8')

        except Exception as e:
            if retry < max_retries - 1:
                log.warning(f"Attempt {retry + 1} failed, retrying: {e}")
                time.sleep(1)
                continue
            else:
                log.error(f"Selenium request to {url} failed after {max_retries} attempts with error: {e}")
                cleanupSeleniumDriver()
                raise
