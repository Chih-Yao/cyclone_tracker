import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

SETUP_UV_ACTION = "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'none'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


def _load_workflow() -> dict:
    return yaml.load(Path(".github/workflows/update-data.yml").read_text(), Loader=yaml.BaseLoader)


def _assert_workflow_contract(workflow: dict) -> None:
    assert list(workflow) == ["name", "on", "permissions", "concurrency", "jobs"]
    assert workflow["name"] == "更新氣旋預報資料"
    assert workflow["on"] == {
        "schedule": [{"cron": "17 */6 * * *"}],
        "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"] == {
        "group": "cyclone-data-update",
        "cancel-in-progress": "false",
    }

    job = workflow["jobs"]["update-data"]
    assert list(workflow["jobs"]) == ["update-data"]
    assert list(job) == ["runs-on", "timeout-minutes", "steps"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "45"

    expected_steps = [
        {
            "name": "取出程式碼",
            "uses": "actions/checkout@v6",
            "with": {"fetch-depth": "0", "ref": "main"},
        },
        {
            "name": "安裝 uv 與 Python",
            "uses": SETUP_UV_ACTION,
            "with": {"python-version": "3.12", "enable-cache": "true"},
        },
        {"name": "安裝鎖定依賴", "run": "uv sync --frozen --all-groups"},
        {"name": "更新所有資料來源", "run": "uv run cyclone-tracker update"},
        {
            "name": "驗證靜態資料",
            "run": "uv run cyclone-tracker validate public/data",
        },
        {
            "name": "檢查資料變更",
            "id": "changes",
            "run": (
                "git add public/data\n"
                "if git diff --cached --quiet; then\n"
                '  echo "changed=false" >> "$GITHUB_OUTPUT"\n'
                "else\n"
                '  echo "changed=true" >> "$GITHUB_OUTPUT"\n'
                "fi\n"
            ),
        },
        {
            "name": "提交並推送有效資料",
            "if": "steps.changes.outputs.changed == 'true'",
            "run": (
                'git config user.name "github-actions[bot]"\n'
                "git config user.email "
                '"41898282+github-actions[bot]@users.noreply.github.com"\n'
                'git commit -m "data: refresh cyclone forecasts"\n'
                "git pull --rebase origin main\n"
                "git push origin HEAD:main\n"
            ),
        },
    ]
    steps = job["steps"]
    assert [step["name"] for step in steps] == [step["name"] for step in expected_steps]
    assert steps[4]["name"] == "驗證靜態資料"
    assert steps[5]["id"] == "changes"

    git_add_lines = [
        line.strip()
        for step in steps
        for line in step.get("run", "").splitlines()
        if line.strip().startswith("git add")
    ]
    assert git_add_lines == ["git add public/data"]
    assert steps == expected_steps


def test_update_workflow_exactly_locks_safe_static_data_updates() -> None:
    _assert_workflow_contract(_load_workflow())


@pytest.mark.parametrize(
    "mutation",
    ["git-add-all", "git-add-dot", "changes-before-validation", "missing-commit-if"],
)
def test_workflow_contract_rejects_unsafe_mutations(mutation: str) -> None:
    workflow = deepcopy(_load_workflow())
    steps = workflow["jobs"]["update-data"]["steps"]
    if mutation == "git-add-all":
        steps[5]["run"] = steps[5]["run"].replace("git add public/data", "git add -A")
    elif mutation == "git-add-dot":
        steps[5]["run"] = steps[5]["run"].replace("git add public/data", "git add .")
    elif mutation == "changes-before-validation":
        steps[4], steps[5] = steps[5], steps[4]
    else:
        del steps[6]["if"]

    with pytest.raises(AssertionError):
        _assert_workflow_contract(workflow)


def test_vercel_exactly_serves_public_with_cache_and_security_headers() -> None:
    config = json.loads(Path("vercel.json").read_text())

    assert config == {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "framework": None,
        "buildCommand": "",
        "installCommand": "",
        "outputDirectory": "public",
        "cleanUrls": True,
        "trailingSlash": False,
        "headers": [
            {
                "source": "/data/manifest.json",
                "headers": [
                    {
                        "key": "Cache-Control",
                        "value": ("public, max-age=0, s-maxage=300, stale-while-revalidate=3600"),
                    }
                ],
            },
            {
                "source": "/data/:source/:cycle.json",
                "headers": [
                    {
                        "key": "Cache-Control",
                        "value": (
                            "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"
                        ),
                    }
                ],
            },
            {
                "source": "/assets/:path*",
                "headers": [
                    {
                        "key": "Cache-Control",
                        "value": "public, max-age=31536000, immutable",
                    }
                ],
            },
            {
                "source": "/:path*",
                "headers": [
                    {"key": "X-Content-Type-Options", "value": "nosniff"},
                    {"key": "Referrer-Policy", "value": "no-referrer"},
                    {"key": "Content-Security-Policy", "value": CSP},
                ],
            },
        ],
    }
