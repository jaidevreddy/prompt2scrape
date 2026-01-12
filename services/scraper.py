import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


@dataclass
class ScrapeResult:
    url: str
    html: str
    final_url: str
    status: str
    elapsed_ms: int


def scrape_html(
    url: str,
    timeout_ms: int = 30_000,
    retries: int = 2,
    extra_wait_ms: int = 1200,
    user_agent: Optional[str] = None,
) -> ScrapeResult:
    """
    Scrape a dynamic page using headless Chromium (Playwright).

    Basic safety:
    - timeout
    - retries with backoff
    - returns html + metadata

    Note: You should respect robots.txt and the site's Terms of Service.
    """
    if not url or not url.strip():
        raise ValueError("URL is empty.")

    url = url.strip()
    last_err: Exception | None = None

    # Some sites break on "networkidle" because requests never stop.
    # We'll attempt multiple wait strategies in order.
    wait_strategies = ["domcontentloaded", "load", "networkidle"]

    for attempt in range(retries + 1):
        start = time.time()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )

                context = browser.new_context(
                    user_agent=user_agent
                    or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1365, "height": 768},
                    locale="en-US",
                )

                page = context.new_page()

                resp = None
                nav_err = None

                for wait_until in wait_strategies:
                    try:
                        resp = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                        nav_err = None
                        break
                    except Exception as e:
                        nav_err = e
                        continue

                if nav_err is not None:
                    raise nav_err

                page.wait_for_timeout(extra_wait_ms)

                # Try to reduce blank pages
                try:
                    page.wait_for_selector("body", timeout=5000)
                except Exception:
                    pass

                html = page.content()
                final_url = page.url
                status = str(resp.status) if resp is not None else "unknown"

                context.close()
                browser.close()

                elapsed_ms = int((time.time() - start) * 1000)

                if not html or len(html) < 1000:
                    raise RuntimeError(
                        "Fetched HTML is unexpectedly short. Site may be blocking automation."
                    )

                return ScrapeResult(
                    url=url,
                    html=html,
                    final_url=final_url,
                    status=status,
                    elapsed_ms=elapsed_ms,
                )

        except (PlaywrightTimeoutError, RuntimeError, Exception) as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
            continue

    raise RuntimeError(f"Failed to scrape after {retries + 1} attempt(s). Last error: {last_err}")
