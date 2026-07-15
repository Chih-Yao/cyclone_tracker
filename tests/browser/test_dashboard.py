from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, ConsoleMessage

from cyclone_tracker.models import CycleData, Manifest

from . import conftest as browser_conftest
from .conftest import (
    FRONTEND_FIXTURES,
    dashboard_page,
    install_fixture_routes,
)


def test_frontend_fixtures_match_the_published_schema() -> None:
    Manifest.model_validate_json((FRONTEND_FIXTURES / "manifest.json").read_bytes())
    CycleData.model_validate_json((FRONTEND_FIXTURES / "gefs-2026071500.json").read_bytes())
    CycleData.model_validate_json((FRONTEND_FIXTURES / "empty.json").read_bytes())


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
    assert page.locator("[aria-live='polite']").count() == 1
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
          const cycle = await data.loadCycle(manifest.sources[0].cycles[0].href);
          return {
            source: manifest.sources[0].id,
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
    page.on("request", lambda request: requested_urls.append(request.url))
    page.goto(site_url)

    expected_host = urlparse(site_url).netloc
    assert requested_urls
    assert all(urlparse(url).netloc == expected_host for url in requested_urls)
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
        assert svg.locator("title").count() == 1
        assert svg.locator("desc").count() == 1
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
