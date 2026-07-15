import json
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "frontend"
CYCLE_FIXTURES = {
    "/data/gefs/2026071300.json": "gefs-2026071300.json",
    "/data/gefs/2026071400.json": "gefs-2026071400.json",
    "/data/gefs/2026071500.json": "gefs-2026071500.json",
    "/data/ifs-ens/2026071500.json": "ifs-ens-2026071500.json",
}


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
    page.clock.set_fixed_time("2026-07-15T06:00:00Z")

    def fulfill_data(route) -> None:
        path = urlparse(route.request.url).path
        if path == "/data/manifest.json":
            if variant == "manifest-unavailable":
                route.fulfill(status=503, body="暫停服務")
                return
            if variant == "manifest-malformed":
                route.fulfill(
                    status=200,
                    body='{"schema_version": 1, "sources": "not-an-array"}',
                    content_type="application/json",
                )
                return
            fixture_name = "manifest.json"
            if variant in {"expired-ok", "expired-empty"}:
                manifest = json.loads((FRONTEND_FIXTURES / fixture_name).read_text())
                source = next(source for source in manifest["sources"] if source["id"] == "gefs")
                source["status"] = variant.removeprefix("expired-")
                source["stale_after_hours"] = 1
                route.fulfill(
                    status=200,
                    body=json.dumps(manifest),
                    content_type="application/json",
                )
                return
        else:
            fixture_name = CYCLE_FIXTURES.get(path)
            if fixture_name is None:
                route.fallback()
                return
            if path == "/data/gefs/2026071500.json":
                if variant == "empty":
                    fixture_name = "empty.json"
                elif variant == "cycle-unavailable":
                    route.fulfill(status=503, body="暫停服務")
                    return
                elif variant == "cycle-malformed":
                    route.fulfill(
                        status=200,
                        body='{"schema_version": 1, "storms": "not-an-array"}',
                        content_type="application/json",
                    )
                    return

        route.fulfill(
            status=200,
            body=(FRONTEND_FIXTURES / fixture_name).read_bytes(),
            content_type="application/json",
        )

    page.route("**/data/**", fulfill_data)


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
