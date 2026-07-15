import json
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "frontend"


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    handler = partial(SimpleHTTPRequestHandler, directory=PROJECT_ROOT / "public")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def cycle_fixture() -> dict:
    return json.loads((FRONTEND_FIXTURES / "gefs-2026071500.json").read_text())


def install_fixture_routes(page: Page, variant: str = "normal") -> None:
    manifest = (FRONTEND_FIXTURES / "manifest.json").read_bytes()
    cycle_name = "empty.json" if variant == "empty" else "gefs-2026071500.json"
    cycle = (FRONTEND_FIXTURES / cycle_name).read_bytes()
    page.route(
        "**/data/manifest.json",
        lambda route: route.fulfill(
            status=200,
            body=manifest,
            content_type="application/json",
        ),
    )
    page.route(
        "**/data/gefs/2026071500.json",
        lambda route: route.fulfill(
            status=200,
            body=cycle,
            content_type="application/json",
        ),
    )


def dashboard_page(
    browser: Browser,
    site_url: str,
    *,
    cycle_fixture: str = "normal",
    viewport: dict[str, int] | None = None,
) -> Page:
    page = browser.new_page(viewport=viewport or {"width": 1440, "height": 900})
    install_fixture_routes(page, cycle_fixture)
    page.goto(site_url)
    return page
