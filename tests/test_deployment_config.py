import json
from pathlib import Path

import yaml


def test_update_workflow_is_scheduled_manual_locked_and_write_scoped() -> None:
    workflow = yaml.load(
        Path(".github/workflows/update-data.yml").read_text(), Loader=yaml.BaseLoader
    )

    assert workflow["on"]["schedule"] == [{"cron": "17 */6 * * *"}]
    assert workflow["on"]["workflow_dispatch"] is not None
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"] == {
        "group": "cyclone-data-update",
        "cancel-in-progress": "false",
    }

    job = workflow["jobs"]["update-data"]
    assert job["timeout-minutes"] == "45"
    steps = job["steps"]
    run_text = "\n".join(step.get("run", "") for step in steps)
    assert "uv sync --frozen --all-groups" in run_text
    assert "uv run cyclone-tracker update" in run_text
    assert "uv run cyclone-tracker validate public/data" in run_text
    assert "git add public/data" in run_text
    assert "git diff --cached --quiet" in run_text
    assert "git push origin HEAD:main" in run_text


def test_vercel_serves_public_with_expected_cache_and_security_headers() -> None:
    config = json.loads(Path("vercel.json").read_text())

    assert config["outputDirectory"] == "public"
    assert config["framework"] is None
    assert config["buildCommand"] == ""
    assert config["installCommand"] == ""

    rules = {rule["source"]: rule["headers"] for rule in config["headers"]}
    manifest_cache = next(
        item["value"] for item in rules["/data/manifest.json"] if item["key"] == "Cache-Control"
    )
    cycle_cache = next(
        item["value"]
        for item in rules["/data/:source/:cycle.json"]
        if item["key"] == "Cache-Control"
    )
    asset_cache = next(
        item["value"] for item in rules["/assets/:path*"] if item["key"] == "Cache-Control"
    )
    security = {item["key"]: item["value"] for item in rules["/:path*"]}

    assert manifest_cache == "public, max-age=0, s-maxage=300, stale-while-revalidate=3600"
    assert cycle_cache == "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"
    assert asset_cache == "public, max-age=31536000, immutable"
    assert security["X-Content-Type-Options"] == "nosniff"
    assert security["Referrer-Policy"] == "no-referrer"
    assert "connect-src 'self'" in security["Content-Security-Policy"]
