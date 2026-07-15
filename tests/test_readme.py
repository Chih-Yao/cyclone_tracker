from pathlib import Path


def test_readme_documents_verified_local_and_deployment_workflow() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    required = [
        "# 西北太平洋氣旋集合預報追蹤器",
        "Google 實驗性氣旋資料不包含在 v1",
        "uv sync --frozen --all-groups",
        "uv run cyclone-tracker update",
        "uv run cyclone-tracker update --source gefs",
        "uv run cyclone-tracker validate public/data",
        "uv run pytest",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run python -m http.server 8000 --directory public",
        "http://127.0.0.1:8000",
        "Workflow permissions",
        "Read and write permissions",
        "17 */6 * * *",
        "Framework Preset",
        "Other",
        "Output Directory",
        "public",
        "Production Branch",
        "main",
        "100 kt = 51.4 m/s",
        "不取代中央氣象署或其他官方機構的警報",
        "https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions",
        "https://github.com/astral-sh/setup-uv",
        "https://vercel.com/docs/builds/configure-a-build",
        "https://nomads.ncep.noaa.gov/",
        "https://www.weather.gov/disclaimer",
        "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "https://apps.ecmwf.int/datasets/licences/general/",
        "https://www.naturalearthdata.com/about/terms-of-use/",
    ]
    for item in required:
        assert item in text

    assert "公開 GitHub repository" in text
    assert "若這份 checkout 尚未設定 Git remote" in text
    assert "目前這份本機 checkout 尚未設定 Git remote" not in text
    assert "FNV3" not in text
    assert "docs/private" not in text
