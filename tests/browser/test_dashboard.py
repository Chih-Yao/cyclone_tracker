import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page

from cyclone_tracker.models import CycleData, Manifest

from . import conftest as browser_conftest
from .conftest import (
    FRONTEND_FIXTURES,
    dashboard_page,
    install_fixture_routes,
)


def wait_for_dashboard(page: Page, storm_id: str = "09W") -> None:
    page.locator(f".instrument-shell[data-current-storm='{storm_id}']").wait_for(timeout=5_000)


def test_frontend_fixtures_match_the_published_schema() -> None:
    Manifest.model_validate_json((FRONTEND_FIXTURES / "manifest.json").read_bytes())
    for fixture_name in (
        "gefs-2026071300.json",
        "gefs-2026071400.json",
        "gefs-2026071500.json",
        "ifs-ens-2026071500.json",
        "empty.json",
    ):
        CycleData.model_validate_json((FRONTEND_FIXTURES / fixture_name).read_bytes())


def test_dashboard_shell_has_taiwan_chinese_controls(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors: list[str] = []

    def record_console_error(message: ConsoleMessage) -> None:
        if message.type == "error":
            console_errors.append(message.text)

    page.on("console", record_console_error)
    page.goto(site_url)

    assert page.locator("html").get_attribute("lang") == "zh-Hant-TW"
    assert page.locator("h1").count() == 1
    assert page.get_by_role("heading", name="西北太平洋氣旋集合預報").is_visible()
    assert page.get_by_role("link", name="跳到主要內容").get_attribute("href") == "#main-content"
    assert page.get_by_label("資料來源").is_visible()
    assert page.get_by_label("模式起報時間").is_visible()
    assert page.get_by_label("熱帶氣旋").is_visible()
    assert page.get_by_role("group", name="最大風速單位").is_visible()
    assert page.get_by_role("button", name="重新讀取資料").is_visible()
    assert page.locator("#load-status[aria-live='polite']").count() == 1
    assert page.get_by_role("region", name="預報航跡").is_visible()
    assert page.get_by_role("region", name="最大風速").is_visible()
    assert page.get_by_role("region", name="中心氣壓").is_visible()
    assert page.get_by_text("資料來源署名", exact=False).is_visible()
    assert page.get_by_text("預報具有不確定性", exact=False).is_visible()
    assert console_errors == []
    page.close()


def test_units_module_uses_exact_nautical_conversion(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    result = page.evaluate(
        """async () => {
          const units = await import('/js/units.js');
          return {
            converted: units.knotsToMetresPerSecond(100),
            metric: units.formatWind(100, 'm/s'),
            nautical: units.formatWind(100),
          };
        }"""
    )
    assert result["converted"] == pytest.approx(51.4444444444)
    assert result["metric"] == "51.4 m/s"
    assert result["nautical"] == "100.0 kt"
    page.close()


def test_units_module_rejects_unsupported_wind_units(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    message = page.evaluate(
        """async () => {
          const units = await import('/js/units.js');
          try {
            units.formatWind(100, 'mph');
          } catch (error) {
            return error.message;
          }
        }"""
    )
    assert message == "最大風速單位只支援 kt 或 m/s"
    page.close()


def test_data_module_loads_same_origin_manifest_and_cycle(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    install_fixture_routes(page)
    page.goto(site_url)
    result = page.evaluate(
        """async () => {
          const data = await import('/js/data.js');
          const manifest = await data.loadManifest();
          const source = manifest.sources.find((candidate) => candidate.id === 'gefs');
          const summary = source.cycles.find((candidate) => candidate.id === '2026071500');
              const cycle = await data.loadCycle(summary.href);
              return {
                source: source.id,
            cycle: cycle.initialized_at,
            storms: cycle.storms.map((storm) => storm.id),
          };
        }"""
    )
    assert result == {
        "source": "gefs",
        "cycle": "2026-07-15T00:00:00Z",
        "storms": ["09W", "90W"],
    }
    page.close()


def test_data_helpers_forward_optional_fetch_options(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    install_fixture_routes(page)
    page.goto(site_url)
    wait_for_dashboard(page)
    calls = page.evaluate(
        """async () => {
          const originalFetch = window.fetch;
          const calls = [];
          window.fetch = async (input, options) => {
            calls.push({url: String(input), cache: options?.cache ?? null});
            const payload = calls.length === 1
              ? {schema_version: 1, sources: []}
              : {schema_version: 1, storms: []};
            return new Response(JSON.stringify(payload), {
              status: 200,
              headers: {'Content-Type': 'application/json'},
            });
          };
          try {
            const data = await import('/js/data.js');
            await data.loadManifest(undefined, {cache: 'no-store'});
            await data.loadCycle('/data/example.json', {cache: 'reload'});
          } finally {
            window.fetch = originalFetch;
          }
          return calls;
        }"""
    )
    assert calls == [
        {"url": f"{site_url}/data/manifest.json", "cache": "no-store"},
        {"url": f"{site_url}/data/example.json", "cache": "reload"},
    ]
    page.close()


@pytest.mark.parametrize("loader", ["loadManifest", "loadCycle"])
def test_data_module_rejects_cross_origin_paths(
    browser: Browser,
    site_url: str,
    loader: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    message = page.evaluate(
        """async ({ loader }) => {
          const module = await import('/js/data.js');
          try {
            await module[loader]('https://example.com/cycle.json');
          } catch (error) {
            return error.message;
          }
        }""",
        {"loader": loader},
    )
    assert message == "只允許讀取本站的預報資料"
    page.close()


def test_data_module_distinguishes_unavailable_and_malformed_data(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.route(
        "**/data/missing.json",
        lambda route: route.fulfill(status=503, body="暫停服務"),
    )
    page.route(
        "**/data/malformed.json",
        lambda route: route.fulfill(
            status=200,
            body='{"schema_version": 1, "storms": "not-an-array"}',
            content_type="application/json",
        ),
    )
    page.goto(site_url)
    messages = page.evaluate(
        """async () => {
          const data = await import('/js/data.js');
          const messages = [];
          for (const href of ['/data/missing.json', '/data/malformed.json']) {
            try {
              await data.loadCycle(href);
            } catch (error) {
              messages.push(error.message);
            }
          }
          return messages;
        }"""
    )
    assert messages == ["目前無法取得預報資料", "預報資料格式不正確"]
    page.close()


@pytest.mark.parametrize(
    ("loader", "payload"),
    [
        (
            "loadManifest",
            {"schema_version": 1, "sources": [{"cycles": ["not-a-record"]}]},
        ),
        (
            "loadManifest",
            {"schema_version": 1, "sources": [{"cycles": [{"storms": "not-an-array"}]}]},
        ),
        (
            "loadCycle",
            {
                "schema_version": 1,
                "storms": [{"members": ["not-a-record"], "mean": {"points": []}}],
            },
        ),
        (
            "loadCycle",
            {
                "schema_version": 1,
                "storms": [
                    {
                        "members": [{"points": "not-an-array"}],
                        "mean": {"points": []},
                    }
                ],
            },
        ),
    ],
)
def test_data_module_rejects_malformed_nested_arrays(
    browser: Browser,
    site_url: str,
    loader: str,
    payload: dict,
) -> None:
    page = browser.new_page()
    page.route(
        "**/data/nested-malformed.json",
        lambda route: route.fulfill(status=200, json=payload),
    )
    page.goto(site_url)
    message = page.evaluate(
        """async ({ loader }) => {
          const data = await import('/js/data.js');
          try {
            await data[loader]('/data/nested-malformed.json');
          } catch (error) {
            return error.message;
          }
        }""",
        {"loader": loader},
    )
    assert message == "預報資料格式不正確"
    page.close()


@pytest.mark.parametrize("loader", ["loadManifest", "loadCycle"])
def test_data_module_rejects_all_disallowed_url_forms(
    browser: Browser,
    site_url: str,
    loader: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    messages = page.evaluate(
        """async ({ loader }) => {
          const data = await import('/js/data.js');
          const current = new URL(window.location.href);
          const otherPort = current.port === '65535' ? 65534 : Number(current.port) + 1;
          const paths = [
            '//example.com/cycle.json',
            `${current.protocol}//user:secret@${current.host}/data/cycle.json`,
            `${current.protocol}//${current.hostname}:${otherPort}/data/cycle.json`,
          ];
          const messages = [];
          for (const path of paths) {
            try {
              await data[loader](path);
            } catch (error) {
              messages.push(error.message);
            }
          }
          return messages;
        }""",
        {"loader": loader},
    )
    assert messages == ["只允許讀取本站的預報資料"] * 3
    page.close()


def test_dashboard_uses_only_local_runtime_resources(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    requested_urls: list[str] = []
    console_errors: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    install_fixture_routes(page)
    page.goto(site_url)
    page.locator("#forecast-map path.track-mean").wait_for(timeout=5_000)

    expected_host = urlparse(site_url).netloc
    assert requested_urls
    assert all(urlparse(url).netloc == expected_host for url in requested_urls)
    assert console_errors == []
    for selector, attribute in (
        ("script[src]", "src"),
        ("link[href]", "href"),
        ("img[src]", "src"),
    ):
        for value in page.locator(selector).evaluate_all(
            f"(nodes) => nodes.map((node) => node.getAttribute('{attribute}'))"
        ):
            assert not value.startswith(("http://", "https://", "//"))
    page.close()


def test_desktop_layout_keeps_map_dominant_and_controls_accessible(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    map_box = page.locator(".map-panel").bounding_box()
    side_box = page.locator(".chart-stack").bounding_box()
    assert map_box is not None and side_box is not None
    assert map_box["x"] < side_box["x"]
    assert map_box["width"] > side_box["width"]

    for control in page.locator("select, button").all():
        box = control.bounding_box()
        assert box is not None
        assert box["height"] >= 44

    source_select = page.get_by_label("資料來源")
    source_select.focus()
    focus_style = source_select.evaluate(
        """(node) => ({
          style: getComputedStyle(node).outlineStyle,
          width: getComputedStyle(node).outlineWidth,
        })"""
    )
    assert focus_style["style"] != "none"
    assert focus_style["width"] != "0px"
    page.close()


def test_desktop_generated_utc_telemetry_is_not_clipped(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)
    widths = page.locator("#generated-at").evaluate(
        """node => {
          const range = document.createRange();
          range.selectNodeContents(node);
          return {text: range.getBoundingClientRect().width, available: node.clientWidth};
        }"""
    )
    assert widths["text"] <= widths["available"]
    page.close()


def test_mobile_layout_is_one_column_without_horizontal_scroll(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(
        browser,
        site_url,
        viewport={"width": 390, "height": 844},
    )
    map_box = page.locator(".map-panel").bounding_box()
    wind_box = page.locator(".wind-panel").bounding_box()
    pressure_box = page.locator(".pressure-panel").bounding_box()
    assert map_box is not None and wind_box is not None and pressure_box is not None
    assert map_box["y"] < wind_box["y"] < pressure_box["y"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.close()


def test_mobile_map_legend_remains_readable(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(
        browser,
        site_url,
        viewport={"width": 390, "height": 844},
    )
    wait_for_dashboard(page)
    legend_box = page.locator("#forecast-map .map-legend").bounding_box()
    assert legend_box is not None
    assert legend_box["width"] >= 150
    page.close()


def test_mobile_map_frame_matches_projection_aspect_ratio(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(
        browser,
        site_url,
        viewport={"width": 390, "height": 844},
    )
    frame_box = page.locator(".map-frame").bounding_box()
    svg_box = page.locator("#forecast-map").bounding_box()
    assert frame_box is not None and svg_box is not None
    assert frame_box["height"] / frame_box["width"] == pytest.approx(600 / 1050, abs=0.02)
    assert svg_box == pytest.approx(frame_box)
    page.close()


def test_mobile_map_selects_nearest_mean_point_with_a_24px_radius(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(
        browser,
        site_url,
        viewport={"width": 390, "height": 844},
    )
    wait_for_dashboard(page)

    detail = page.locator("#map-point-detail")
    assert detail.is_visible()
    assert float(detail.evaluate("(node) => parseFloat(getComputedStyle(node).fontSize)")) >= 12
    assert "點選平均路徑節點" in detail.text_content()

    result = page.evaluate(
        """async () => {
          const map = await import('/js/map.js');
          const svg = document.querySelector('#forecast-map');
          const detail = document.querySelector('#map-point-detail');
          const storm = {
            members: [],
            mean: {points: [{
              tau_h: 0,
              valid_at: '2026-07-15T00:00:00Z',
              lat: 20,
              lon: 140,
              wind_kt: 50,
              pressure_hpa: 990,
              member_count: 1,
            }]},
          };
          const render = () => map.renderMap(
            svg,
            {type: 'FeatureCollection', features: []},
            storm,
          );
          const dispatch = (offset, pointerType) => {
            const plot = svg.querySelector('g[data-renderer="map"]');
            const circle = plot.querySelector('circle.mean-point');
            const point = new DOMPoint(
              Number(circle.getAttribute('cx')),
              Number(circle.getAttribute('cy')),
            ).matrixTransform(circle.getScreenCTM());
            plot.dispatchEvent(new PointerEvent('pointerdown', {
              bubbles: true,
              clientX: point.x + offset,
              clientY: point.y,
              pointerId: 1,
              pointerType,
              isPrimary: true,
            }));
          };

          render();
          svg.querySelector('circle.mean-point').focus();
          const focusDetail = detail.textContent;
          render();
          dispatch(20, 'touch');
          const nearDetail = detail.textContent;
          render();
          dispatch(30, 'touch');
          const farDetail = detail.textContent;
          dispatch(0, 'mouse');
          return {
            focusDetail,
            nearDetail,
            farDetail,
            mouseDetail: detail.textContent,
            tooltipDisplay: getComputedStyle(svg.querySelector('.map-tooltip')).display,
          };
        }"""
    )

    expected = "預報 0 小時｜20.0°N、140.0°E｜50.0 kt｜990.0 hPa｜1 位成員"
    assert result["focusDetail"] == expected
    assert result["nearDetail"] == expected
    assert "點選平均路徑節點" in result["farDetail"]
    assert "點選平均路徑節點" in result["mouseDetail"]
    assert result["tooltipDisplay"] == "none"
    page.close()


def test_visual_tokens_and_reduced_motion_rule_are_present(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    tokens = page.evaluate(
        """() => {
          const styles = getComputedStyle(document.documentElement);
          return Object.fromEntries([
            '--sea-50', '--sea-100', '--sea-700', '--ocean-900', '--land-200',
            '--track-mist', '--pressure', '--ink-900', '--ink-600', '--line',
            '--warning', '--danger', '--surface'
          ].map((name) => [name, styles.getPropertyValue(name).trim()]));
        }"""
    )
    assert tokens == {
        "--sea-50": "#eaf3f2",
        "--sea-100": "#d6e7e5",
        "--sea-700": "#137c78",
        "--ocean-900": "#083b4c",
        "--land-200": "#c8d6c7",
        "--track-mist": "#79aaa5",
        "--pressure": "#8b5262",
        "--ink-900": "#17313b",
        "--ink-600": "#526b70",
        "--line": "#bdd3d0",
        "--warning": "#a45b18",
        "--danger": "#a13939",
        "--surface": "#ffffff",
    }
    css = page.locator("link[rel='stylesheet']").evaluate(
        "async (link) => await (await fetch(link.href)).text()"
    )
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "linear-gradient" not in css
    assert "box-shadow" not in css
    page.close()


def test_forecast_guidance_uses_the_visually_hidden_utility(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    guidance = page.locator("#forecast-guidance.visually-hidden")
    assert guidance.count() == 1
    assert (
        page.get_by_role("region", name="預報航跡").get_attribute("aria-describedby")
        == "forecast-guidance"
    )
    styles = guidance.evaluate(
        """(node) => ({
          position: getComputedStyle(node).position,
          width: getComputedStyle(node).width,
          height: getComputedStyle(node).height,
          overflow: getComputedStyle(node).overflow,
        })"""
    )
    assert styles == {
        "position": "absolute",
        "width": "1px",
        "height": "1px",
        "overflow": "hidden",
    }
    page.close()


def test_map_and_charts_have_accessible_responsive_svg_shells(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)

    expected_names = {
        "#forecast-map": "西北太平洋集合預報航跡圖",
        "#wind-chart": "平均最大風速預報圖",
        "#pressure-chart": "平均中心氣壓預報圖",
    }
    for selector, name in expected_names.items():
        svg = page.locator(selector)
        assert svg.count() == 1
        assert svg.get_attribute("role") == "img"
        assert svg.get_attribute("viewBox") is not None
        assert svg.locator(":scope > title").count() == 1
        assert svg.locator(":scope > desc").count() == 1
        assert page.get_by_role("img", name=name).count() == 1

    assert page.locator(".plot-placeholder").count() == 0
    page.close()


def test_map_projection_and_geojson_path_helpers(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    result = page.evaluate(
        """async () => {
          const map = await import('/js/map.js');
          const polygon = {
            type: 'Feature',
            geometry: {
              type: 'Polygon',
              coordinates: [[[105, 5], [115, 5], [115, 15], [105, 5]]],
            },
          };
          const multiPolygon = {
            type: 'Feature',
            geometry: {
              type: 'MultiPolygon',
              coordinates: [
                [[[105, 5], [115, 5], [115, 15], [105, 5]]],
                [[[-175, 20], [-170, 20], [-170, 25], [-175, 20]]],
              ],
            },
          };
          const outsideProjection = {
            type: 'Feature',
            geometry: {
              type: 'Polygon',
              coordinates: [[[75, 0], [85, 0], [85, 10], [75, 10], [75, 0]]],
            },
          };
          return {
            northWest: map.projectPoint(95, 55, 1050, 600),
            southEast: map.projectPoint(200, -5, 1050, 600),
            dateline: map.projectPoint(-175, 25, 1050, 600),
            polygon: map.geoJsonToPath(polygon, 1050, 600),
            multiPolygon: map.geoJsonToPath(multiPolygon, 1050, 600),
            outsideProjection: map.geoJsonToPath(outsideProjection, 1050, 600),
          };
        }"""
    )
    assert result["northWest"] == pytest.approx([0, 0])
    assert result["southEast"] == pytest.approx([1050, 600])
    assert result["dateline"] == pytest.approx([900, 300])
    assert result["polygon"].count("M ") == 1
    assert result["polygon"].count("Z") == 1
    assert result["multiPolygon"].count("M ") == 2
    assert result["multiPolygon"].count("Z") == 2
    assert "NaN" not in result["multiPolygon"]
    assert result["outsideProjection"] == ""
    page.close()


def test_map_tracks_use_continuous_longitudes_without_false_wraps(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.goto(site_url)
    result = page.evaluate(
        """async () => {
          const map = await import('/js/map.js');
          const point = (tau_h, lon) => ({
            tau_h,
            valid_at: '2026-07-15T00:00:00Z',
            lat: 20,
            lon,
            wind_kt: 50,
            pressure_hpa: 990,
            member_count: 1,
          });
          const storm = {
            members: [
              {id: 'west', points: [point(0, 81), point(6, 79)]},
              {id: 'dateline', points: [point(0, 179), point(6, -179)]},
            ],
            mean: {points: [point(0, 79)]},
          };
          const svg = document.querySelector('#forecast-map');
          map.renderMap(svg, {type: 'FeatureCollection', features: []}, storm);
          return {
            westProjection: map.projectPoint(79, 20, 1050, 600),
            memberPaths: [...svg.querySelectorAll('path.track-member')]
              .map((path) => path.getAttribute('d')),
            meanCx: svg.querySelector('circle.mean-point').getAttribute('cx'),
          };
        }"""
    )

    assert result["westProjection"] == pytest.approx([-160, 350])
    assert result["memberPaths"] == [
        "M -140 350 L -160 350",
        "M 840 350 L 860 350",
    ]
    assert result["meanCx"] == "-160"
    page.close()


def test_map_draws_members_mean_and_local_land(
    browser: Browser,
    site_url: str,
    cycle_fixture: dict,
) -> None:
    page = dashboard_page(browser, site_url)
    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.evaluate(
        """async (storm) => {
          const map = await import('/js/map.js');
          const land = await (await fetch('/assets/ne_110m_land.geojson')).json();
          map.renderMap(document.querySelector('#forecast-map'), land, storm, {unit: 'kt'});
        }""",
        cycle_fixture["storms"][0],
    )

    assert page.locator("#forecast-map path.land").count() > 0
    assert page.locator("#forecast-map path.track-member").count() == 2
    assert page.locator("#forecast-map path.track-mean").count() == 1
    assert page.locator("#forecast-map circle.mean-point").count() == 3
    assert page.locator("#forecast-map path.graticule").count() > 0
    assert any(url.endswith("/assets/ne_110m_land.geojson") for url in requested_urls)
    expected_host = urlparse(site_url).netloc
    assert all(urlparse(url).netloc == expected_host for url in requested_urls)

    point = page.locator("#forecast-map circle.mean-point").nth(1)
    assert point.get_attribute("tabindex") == "0"
    title = point.locator("title").text_content()
    assert title is not None
    assert "預報 6 小時" in title
    assert "18.9°N、131.3°E" in title
    assert "97.5 kt" in title
    assert "952.5 hPa" in title
    assert "2 位成員" in title
    point.focus()
    assert page.locator("#forecast-map .map-tooltip").get_attribute("visibility") == "visible"
    assert "預報 6 小時" in page.locator("#forecast-map .map-tooltip").text_content()
    page.close()


def test_map_renderer_has_no_fetch_and_replaces_only_owned_layers(
    browser: Browser,
    site_url: str,
    cycle_fixture: dict,
) -> None:
    page = dashboard_page(browser, site_url)
    result = page.evaluate(
        """async (storm) => {
          const map = await import('/js/map.js');
          const svg = document.querySelector('#forecast-map');
          const persistent = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          persistent.classList.add('persistent-test-node');
          svg.append(persistent);
          let fetchCalls = 0;
          const originalFetch = window.fetch;
          window.fetch = () => {
            fetchCalls += 1;
            throw new Error('renderMap must not fetch');
          };
          try {
            const land = {
              type: 'FeatureCollection',
              features: [{
                type: 'Feature',
                geometry: {
                  type: 'Polygon',
                  coordinates: [[[120, 10], [125, 10], [125, 15], [120, 10]]],
                },
              }],
            };
            map.renderMap(svg, land, storm, {unit: 'kt'});
            map.renderMap(svg, land, storm, {unit: 'm/s'});
          } finally {
            window.fetch = originalFetch;
          }
          return {
            fetchCalls,
            persistentNodes: svg.querySelectorAll('.persistent-test-node').length,
            ownedLayers: svg.querySelectorAll('[data-renderer="map"]').length,
            meanPoints: svg.querySelectorAll('circle.mean-point').length,
            pointTitle: svg.querySelector('circle.mean-point title').textContent,
          };
        }""",
        cycle_fixture["storms"][0],
    )
    assert result == {
        "fetchCalls": 0,
        "persistentNodes": 1,
        "ownedLayers": 2,
        "meanPoints": 3,
        "pointTitle": ("預報 0 小時｜18.1°N、132.1°E｜39.9 m/s｜972.5 hPa｜2 位成員"),
    }
    page.close()


def test_map_focus_tooltip_stays_inside_the_projection(
    browser: Browser,
    site_url: str,
    cycle_fixture: dict,
) -> None:
    page = dashboard_page(browser, site_url)
    result = page.evaluate(
        """async (storm) => {
          const map = await import('/js/map.js');
          const svg = document.querySelector('#forecast-map');
          map.renderMap(
            svg,
            {type: 'FeatureCollection', features: []},
            storm,
            {unit: 'kt'},
          );
          const point = svg.querySelector('circle.mean-point');
          point.focus();
          const tooltip = svg.querySelector('.map-tooltip');
          const transform = tooltip.getAttribute('transform');
          const tooltipX = Number(transform.slice('translate('.length).split(' ')[0]);
          return {
            tooltipX,
            visibility: tooltip.getAttribute('visibility'),
            pointTitle: point.querySelector('title').textContent,
          };
        }""",
        cycle_fixture["storms"][1],
    )
    assert "146.0°E" in result["pointTitle"]
    assert result["visibility"] == "visible"
    assert 0 <= result["tooltipX"] <= 1050 - 570
    page.close()


def test_charts_render_axes_units_and_accessible_points(
    browser: Browser,
    site_url: str,
    cycle_fixture: dict,
) -> None:
    page = dashboard_page(browser, site_url)
    result = page.evaluate(
        """async (mean) => {
          const charts = await import('/js/charts.js');
          let fetchCalls = 0;
          const originalFetch = window.fetch;
          window.fetch = () => {
            fetchCalls += 1;
            throw new Error('chart renderers must not fetch');
          };
          try {
            charts.renderWindChart(
              document.querySelector('#wind-chart'), mean.points, {unit: 'kt'}
            );
            charts.renderPressureChart(
              document.querySelector('#pressure-chart'), mean.points
            );
          } finally {
            window.fetch = originalFetch;
          }
          return fetchCalls;
        }""",
        cycle_fixture["storms"][0]["mean"],
    )
    assert result == 0
    for selector in ("#wind-chart", "#pressure-chart"):
        assert page.locator(f"{selector} path.mean-series").count() == 1
        assert page.locator(f"{selector} line.gridline").count() >= 4
        assert page.locator(f"{selector} .axis-tick").count() >= 4
        assert page.locator(f"{selector} circle.series-point").count() == 3
        point = page.locator(f"{selector} circle.series-point").first
        assert point.get_attribute("tabindex") == "0"
        assert point.locator("title").count() == 1

    assert page.locator("#wind-chart").get_by_text("knots", exact=True).is_visible()
    assert page.locator("#pressure-chart").get_by_text("hPa", exact=True).is_visible()
    wind_title = page.locator("#wind-chart circle.series-point title").first.text_content()
    pressure_title = page.locator("#pressure-chart circle.series-point title").first.text_content()
    assert wind_title == "預報 0 小時｜最大風速 77.5 kt"
    assert pressure_title == "預報 0 小時｜中心氣壓 972.5 hPa"
    page.close()


def test_charts_convert_wind_at_render_and_leave_null_gaps(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)
    result = page.evaluate(
        """async () => {
          const charts = await import('/js/charts.js');
          const points = [
            {tau_h: 0, wind_kt: 100, pressure_hpa: 1000},
            {tau_h: 6, wind_kt: null, pressure_hpa: 990},
            {tau_h: 12, wind_kt: 80, pressure_hpa: null},
            {tau_h: 18, wind_kt: 60, pressure_hpa: 980},
          ];
          charts.renderWindChart(
            document.querySelector('#wind-chart'), points, {unit: 'm/s'}
          );
          charts.renderPressureChart(document.querySelector('#pressure-chart'), points);
          return {
            windPath: document.querySelector('#wind-chart path.mean-series').getAttribute('d'),
            pressurePath: document
              .querySelector('#pressure-chart path.mean-series')
              .getAttribute('d'),
            windTitle: document.querySelector('#wind-chart circle.series-point title').textContent,
            windPoints: document.querySelectorAll('#wind-chart circle.series-point').length,
            pressurePoints: document.querySelectorAll('#pressure-chart circle.series-point').length,
          };
        }"""
    )
    assert result["windPath"].count("M ") == 2
    assert result["windPath"].count("L ") == 1
    assert result["pressurePath"].count("M ") == 2
    assert result["pressurePath"].count("L ") == 1
    assert "NaN" not in result["windPath"]
    assert "NaN" not in result["pressurePath"]
    assert result["windTitle"] == "預報 0 小時｜最大風速 51.4 m/s"
    assert result["windPoints"] == 3
    assert result["pressurePoints"] == 3
    assert page.locator("#wind-chart").get_by_text("m/s", exact=True).is_visible()
    page.close()


def test_charts_render_ecmwf_members_below_mean_and_break_gaps(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    storm = json.loads(
        (FRONTEND_FIXTURES / "ifs-ens-2026071500.json").read_text()
    )["storms"][0]
    result = page.evaluate(
        """async (storm) => {
          const charts = await import('/js/charts.js');
          charts.renderWindChart(
            document.querySelector('#wind-chart'),
            storm.mean.points,
            {unit: 'm/s', members: storm.members},
          );
          charts.renderPressureChart(
            document.querySelector('#pressure-chart'),
            storm.mean.points,
            {members: storm.members},
          );
          const summarize = (selector) => {
            const svg = document.querySelector(selector);
            const members = [...svg.querySelectorAll('path.member-series')];
            const mean = svg.querySelector('path.mean-series');
            return {
              memberPaths: members.map((path) => path.getAttribute('d')),
              memberTitles: members.map((path) => path.querySelector('title').textContent),
              memberYCoordinates: members.flatMap((path) => {
                const coordinates = path.getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
                return coordinates.filter((_, index) => index % 2 === 1);
              }),
              meanAfterMembers: members.every(
                (path) => Boolean(path.compareDocumentPosition(mean) & Node.DOCUMENT_POSITION_FOLLOWING)
              ),
            };
          };
          return {wind: summarize('#wind-chart'), pressure: summarize('#pressure-chart')};
        }""",
        storm,
    )

    for chart in (result["wind"], result["pressure"]):
        assert len(chart["memberPaths"]) == 2
        assert chart["meanAfterMembers"] is True
        assert any("cf00" in title for title in chart["memberTitles"])
        assert any("pf01" in title for title in chart["memberTitles"])
        assert all("NaN" not in path for path in chart["memberPaths"])
        assert chart["memberPaths"][0].count("M ") == 2
        assert all(28 <= y <= 270 for y in chart["memberYCoordinates"])
    page.close()


def test_dashboard_controls_initialize_first_available_source_and_newest_cycle(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)

    shell = page.locator(".instrument-shell")
    assert shell.get_attribute("data-current-source") == "gefs"
    assert shell.get_attribute("data-current-cycle") == "2026071500"
    assert page.get_by_label("資料來源").input_value() == "gefs"
    assert page.get_by_label("模式起報時間").input_value() == "2026071500"
    assert page.get_by_label("熱帶氣旋").input_value() == "09W"
    assert page.locator("#source-select option[value='aigfs']").is_disabled()
    assert "09W" in page.locator("#active-storm").text_content()
    assert "資料正常" in page.locator("#source-freshness").text_content()
    assert page.locator("#generated-at").text_content() == "2026-07-15 01:00 UTC"
    attribution = page.locator("#source-attribution a")
    assert attribution.text_content() == "NCEP GEFS"
    assert attribution.get_attribute("href") == "https://nomads.ncep.noaa.gov/"
    assert page.locator("#forecast-map .map-legend").is_visible()
    assert page.locator("#forecast-map path.track-mean").count() == 1
    assert page.get_by_role("status").text_content() == "已載入 NCEP GEFS 的預報。"
    page.close()


@pytest.mark.parametrize("manifest_status", ["ok", "empty"])
def test_dashboard_treats_old_latest_cycle_as_effectively_stale(
    browser: Browser,
    site_url: str,
    manifest_status: str,
) -> None:
    page = dashboard_page(browser, site_url, cycle_fixture=f"expired-{manifest_status}")
    wait_for_dashboard(page)

    assert page.locator("#source-freshness").text_content() == "資料可能過時"
    status = page.get_by_role("status")
    assert status.get_attribute("data-state") == "stale"
    assert "超過更新時限" in status.text_content()
    assert "重新讀取資料" in status.text_content()
    page.close()


def test_source_storm_and_unit_controls_update_the_view(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)

    metric_button = page.get_by_role("button", name="m/s")
    metric_button.click()
    assert metric_button.get_attribute("aria-pressed") == "true"
    assert page.locator("#wind-chart").get_by_text("m/s", exact=True).is_visible()
    assert page.evaluate("localStorage.getItem('cyclone-wind-unit')") == "m/s"
    page.locator("#forecast-map circle.mean-point").nth(1).focus()
    assert "50.2 m/s" in page.locator("#forecast-map .map-tooltip").text_content()

    page.get_by_label("熱帶氣旋").select_option("90W")
    assert page.locator(".instrument-shell").get_attribute("data-current-storm") == "90W"
    assert page.locator("#forecast-map path.track-member").count() == 1

    page.get_by_label("資料來源").select_option("ifs-ens")
    page.locator(".instrument-shell[data-current-source='ifs-ens']").wait_for(timeout=5_000)
    assert page.get_by_label("模式起報時間").input_value() == "2026071500"
    assert page.get_by_label("熱帶氣旋").input_value() == "09W"
    assert "資料可能過時" in page.locator("#source-freshness").text_content()
    status = page.get_by_role("status")
    assert status.get_attribute("data-state") == "stale"
    assert "最後成功資料" in status.text_content()
    page.close()


def test_cycle_controls_preserve_storm_then_fall_back_when_missing(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)
    storm_select = page.get_by_label("熱帶氣旋")
    cycle_select = page.get_by_label("模式起報時間")

    storm_select.select_option("90W")
    cycle_select.select_option("2026071400")
    page.locator(".instrument-shell[data-current-cycle='2026071400']").wait_for(timeout=5_000)
    assert storm_select.input_value() == "90W"

    cycle_select.select_option("2026071300")
    page.locator(".instrument-shell[data-current-cycle='2026071300']").wait_for(timeout=5_000)
    assert storm_select.input_value() == "09W"
    assert page.locator(".instrument-shell").get_attribute("data-current-storm") == "09W"
    page.close()


def test_unit_controls_restore_local_storage_and_keep_visible_keyboard_focus(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("localStorage.setItem('cyclone-wind-unit', 'm/s')")
    install_fixture_routes(page)
    page.goto(site_url)
    wait_for_dashboard(page)

    assert page.get_by_role("button", name="m/s").get_attribute("aria-pressed") == "true"
    assert page.locator("#wind-chart").get_by_text("m/s", exact=True).is_visible()
    for name in ("knots", "m/s"):
        button = page.get_by_role("button", name=name)
        button.focus()
        focus_style = button.evaluate(
            """(node) => ({
              style: getComputedStyle(node).outlineStyle,
              width: getComputedStyle(node).outlineWidth,
            })"""
        )
        assert focus_style["style"] != "none"
        assert focus_style["width"] != "0px"
    page.close()


def test_reload_uses_no_store_and_retains_last_view_on_cycle_error(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script(
        """
        window.__task8FetchCalls = [];
        const task8OriginalFetch = window.fetch.bind(window);
        window.fetch = (input, options) => {
          window.__task8FetchCalls.push({url: String(input), cache: options?.cache ?? null});
          return task8OriginalFetch(input, options);
        };
        """
    )
    install_fixture_routes(page)
    page.goto(site_url)
    wait_for_dashboard(page)
    path_before = page.locator("#forecast-map path.track-mean").get_attribute("d")
    page.evaluate("window.__task8FetchCalls = []")
    page.route(
        "**/data/gefs/2026071500.json",
        lambda route: route.fulfill(status=503, body="暫停服務"),
    )

    page.get_by_role("button", name="重新讀取資料").click()
    status = page.locator("#load-status[data-state='error']")
    status.wait_for(timeout=5_000)
    assert "保留上次可用的預報" in status.text_content()
    assert page.locator(".instrument-shell").get_attribute("data-current-storm") == "09W"
    assert page.locator("#forecast-map path.track-mean").get_attribute("d") == path_before
    calls = page.evaluate("window.__task8FetchCalls")
    assert calls == [
        {"url": f"{site_url}/data/manifest.json", "cache": "no-store"},
        {"url": f"{site_url}/data/gefs/2026071500.json", "cache": "no-store"},
    ]
    page.close()


def test_reload_empty_manifest_retains_view_and_next_reload_recovers(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    install_fixture_routes(page)
    manifest = (FRONTEND_FIXTURES / "manifest.json").read_bytes()
    empty_manifest = b'{"schema_version":1,"generated_at":"2026-07-15T02:00:00Z","sources":[]}'
    manifest_calls = 0

    def serve_manifest(route) -> None:
        nonlocal manifest_calls
        manifest_calls += 1
        route.fulfill(
            status=200,
            body=empty_manifest if manifest_calls == 2 else manifest,
            content_type="application/json",
        )

    page.route("**/data/manifest.json", serve_manifest)
    page.goto(site_url)
    wait_for_dashboard(page)
    shell = page.locator(".instrument-shell")
    expected_selection = {
        "source": page.get_by_label("資料來源").input_value(),
        "cycle": page.get_by_label("模式起報時間").input_value(),
        "storm": page.get_by_label("熱帶氣旋").input_value(),
    }
    expected_paths = {
        "map": page.locator("#forecast-map path.track-mean").get_attribute("d"),
        "wind": page.locator("#wind-chart path.mean-series").get_attribute("d"),
        "pressure": page.locator("#pressure-chart path.mean-series").get_attribute("d"),
    }

    page.get_by_role("button", name="重新讀取資料").click()
    status = page.locator("#load-status[data-state='empty']")
    status.wait_for(timeout=5_000)
    assert status.text_content() == ("重新讀取完成，但目前沒有可用起報時間；保留上次可用的預報。")
    assert "TypeError" not in status.text_content()
    assert "Cannot read properties" not in status.text_content()
    assert shell.get_attribute("data-current-source") == expected_selection["source"]
    assert shell.get_attribute("data-current-cycle") == expected_selection["cycle"]
    assert shell.get_attribute("data-current-storm") == expected_selection["storm"]
    assert page.get_by_label("資料來源").input_value() == expected_selection["source"]
    assert page.get_by_label("模式起報時間").input_value() == expected_selection["cycle"]
    assert page.get_by_label("熱帶氣旋").input_value() == expected_selection["storm"]
    assert page.locator("#forecast-map path.track-mean").get_attribute("d") == expected_paths["map"]
    assert page.locator("#wind-chart path.mean-series").get_attribute("d") == expected_paths["wind"]
    assert (
        page.locator("#pressure-chart path.mean-series").get_attribute("d")
        == expected_paths["pressure"]
    )

    page.get_by_role("button", name="重新讀取資料").click()
    ready = page.locator("#load-status[data-state='ready']")
    ready.wait_for(timeout=5_000)
    assert ready.text_content() == "已載入 NCEP GEFS 的預報。"
    assert manifest_calls == 3
    page.close()


def test_reload_disables_source_cycle_and_reload_until_manifest_arrives(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url)
    wait_for_dashboard(page)
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.__finishManifestReload = null;
          window.fetch = (input, options) => {
            const path = new URL(input, window.location.href).pathname;
            if (path === '/data/manifest.json' && options?.cache === 'no-store') {
              return new Promise((resolve, reject) => {
                window.__finishManifestReload = () => originalFetch(input, options).then(
                  resolve,
                  reject,
                );
              });
            }
            return originalFetch(input, options);
          };
        }"""
    )

    page.get_by_role("button", name="重新讀取資料").click()
    page.wait_for_function("typeof window.__finishManifestReload === 'function'")
    assert page.get_by_label("資料來源").is_disabled()
    assert page.get_by_label("模式起報時間").is_disabled()
    assert page.get_by_role("button", name="重新讀取資料").is_disabled()

    page.evaluate("() => { window.__finishManifestReload(); }")
    page.locator("#load-status[data-state='ready']").wait_for(timeout=5_000)
    assert page.get_by_label("資料來源").is_enabled()
    assert page.get_by_label("模式起報時間").is_enabled()
    assert page.get_by_role("button", name="重新讀取資料").is_enabled()
    page.close()


def test_empty_cycle_explains_available_actions_without_a_blank_plot(
    browser: Browser,
    site_url: str,
) -> None:
    page = dashboard_page(browser, site_url, cycle_fixture="empty")
    status = page.get_by_role("status")
    status.wait_for(timeout=5_000)
    assert status.get_attribute("data-state") == "empty"
    assert "這個起報時間沒有西北太平洋氣旋" in status.text_content()
    assert "可改選其他起報時間或重新讀取資料" in status.text_content()
    assert page.locator("#forecast-map path.land").count() > 0
    assert page.locator("#forecast-map path.track-mean").count() == 0
    assert page.locator("#wind-chart .empty-series").is_visible()
    assert page.locator("#pressure-chart .empty-series").is_visible()
    page.close()


@pytest.mark.parametrize(
    ("variant", "expected_message"),
    [
        ("manifest-unavailable", "目前無法取得預報資料"),
        ("manifest-malformed", "預報資料格式不正確"),
        ("cycle-unavailable", "目前無法取得預報資料"),
        ("cycle-malformed", "預報資料格式不正確"),
    ],
)
def test_runtime_data_errors_are_actionable_and_distinguish_failure_kind(
    browser: Browser,
    site_url: str,
    variant: str,
    expected_message: str,
) -> None:
    page = dashboard_page(browser, site_url, cycle_fixture=variant)
    status = page.locator("#load-status[data-state='error']")
    status.wait_for(timeout=5_000)
    assert expected_message in status.text_content()
    assert "重新讀取資料" in status.text_content()
    assert page.locator("#forecast-map path.track-mean").count() == 0
    page.close()


def test_unit_controls_respect_reduced_motion_and_forced_colors(
    browser: Browser,
    site_url: str,
) -> None:
    page = browser.new_page()
    page.emulate_media(reduced_motion="reduce", forced_colors="active")
    page.goto(site_url)
    button = page.get_by_role("button", name="m/s")
    button.focus()
    styles = button.evaluate(
        """(node) => ({
          outline: getComputedStyle(node).outlineStyle,
          duration: getComputedStyle(node).transitionDuration,
        })"""
    )
    assert styles["outline"] != "none"
    assert styles["duration"] in {"0s", "1e-05s"}
    css = page.locator("link[rel='stylesheet']").evaluate(
        "async (link) => await (await fetch(link.href)).text()"
    )
    assert "@media (forced-colors: active)" in css
    page.close()


def test_site_server_fixture_explicitly_closes_its_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked_servers: list[browser_conftest.ThreadingHTTPServer] = []
    server_class = browser_conftest.ThreadingHTTPServer

    class TrackedHTTPServer(server_class):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            tracked_servers.append(self)

    monkeypatch.setattr(browser_conftest, "ThreadingHTTPServer", TrackedHTTPServer)
    fixture_generator = browser_conftest.site_url.__wrapped__()
    next(fixture_generator)
    fixture_generator.close()

    server = tracked_servers[0]
    try:
        assert server.fileno() == -1
    finally:
        server.server_close()
