"""Published identity and the README example are part of the release contract."""

import tomllib
from pathlib import Path
from shutil import copyfile


def test_release_identity_license_and_readme_are_declared():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "ghimera"
    assert project["version"] == "0.4.7"
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert "Permission is hereby granted" in (root / "LICENSE").read_text()
    assert project["urls"]["Repository"] == "https://github.com/ginkorea/ghimera"
    assert project["scripts"]["ghimera"] == "ghimera.command:main"


def test_legacy_root_api_refers_to_the_same_implementation():
    import chimera
    import ghimera

    assert chimera.Collector is ghimera.Collector
    assert chimera.ChimeraConfig is ghimera.GhimeraConfig


def test_renamed_package_reads_the_existing_config_schema():
    from ghimera import GhimeraConfig

    root = Path(__file__).resolve().parents[1]
    config = GhimeraConfig.from_toml(root / "examples" / "chimera.toml")
    assert config.model_dump(by_alias=True)["schema"] == "chimera.config/1"


def test_readme_offline_example_runs_against_the_public_api(monkeypatch, capsys, tmp_path):
    root = Path(__file__).resolve().parents[1]
    copyfile(root / "examples" / "chimera.toml", tmp_path / "chimera.toml")
    monkeypatch.chdir(tmp_path)
    readme = (root / "README.md").read_text()
    code = readme.split("```python\n", 1)[1].split("```", 1)[0]
    namespace: dict[str, object] = {"__name__": "__readme_example__"}
    exec(compile(code, str(root / "README.md"), "exec"), namespace)
    assert capsys.readouterr().out.strip() == "frontier_empty"
    assert callable(namespace["main"])


def test_readme_is_standalone_user_documentation():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text().casefold()
    assert "taipan" not in readme
    assert "g39" not in readme
