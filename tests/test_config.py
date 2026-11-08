import pytest

from bbeye.config import EXAMPLE_CONFIG, Config, ConfigError, load_config, load_secrets


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_example_config_parses_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE_CONFIG)
    assert load_config(path) == Config()


def test_values_and_types(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'owner = "alex"\n[camera]\nsource = "rtsp://cam/stream"\ninterval_s = 1\n'
        '[calendar]\nsource = "ics"\ndesk_keywords = ["desk"]\n'
    )
    config = load_config(path)
    assert config.owner == "alex"
    assert config.camera.source == "rtsp://cam/stream"
    assert config.camera.interval_s == 1.0
    assert config.calendar.desk_keywords == ("desk",)


@pytest.mark.parametrize(
    "text, message",
    [
        ("[camera]\nsorce = 1\n", "Unknown setting"),
        ('[judge]\nkind = "gpt"\n', "must be one of"),
        ('[tracker]\nconfirm_s = "fast"\n', "must be a number"),
        ("[cortex]\nenabled = 1\n", "true or false"),
        ("camera = 3\n", "must be a table"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_secrets_file_fills_gaps_but_environment_wins(tmp_path, monkeypatch):
    path = tmp_path / "secrets.env"
    path.write_text("# comment\nPUSHCUT_API_KEY=from-file\nBBEYE_MCP_TOKEN='quoted'\n")
    monkeypatch.setenv("PUSHCUT_API_KEY", "from-env")
    secrets = load_secrets(path)
    assert secrets["PUSHCUT_API_KEY"] == "from-env"
    assert secrets["BBEYE_MCP_TOKEN"] == "quoted"


def test_bad_secrets_line(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_text("JUSTANAME\n")
    with pytest.raises(ConfigError, match="NAME=value"):
        load_secrets(path)


def test_repo_example_matches_bbeye_init():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    assert example.read_text() == EXAMPLE_CONFIG, (
        "regenerate config.example.toml from bbeye.config.EXAMPLE_CONFIG"
    )


def test_files_saved_with_a_windows_bom_still_load(tmp_path):
    config = tmp_path / "config.toml"
    config.write_bytes(b'\xef\xbb\xbfowner = "alex"\n')
    assert load_config(config).owner == "alex"
    secrets = tmp_path / "secrets.env"
    secrets.write_bytes(b"\xef\xbb\xbfPUSHCUT_API_KEY=abc\n")
    assert load_secrets(secrets)["PUSHCUT_API_KEY"] == "abc"
