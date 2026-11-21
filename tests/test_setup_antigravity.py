"""`baas.py setup antigravity` (T3 · #165): el servidor `baas` en `<proyecto>/.agents/mcp_config.json`.

Todo contra el BaaS falso (`dev/fake_baas.py`) y con claves ficticias de desarrollo; nunca se ejecuta `agy`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fake_baas import FakeBaasServer, dev_api_key

from baas_plugin import cli, envfile
from conftest import mode_of

CONFIG = ".agents/mcp_config.json"
posix_only = pytest.mark.skipif(os.name != "posix", reason="los permisos 0600 solo se comprueban en POSIX")


@pytest.fixture
def server() -> Iterator[FakeBaasServer]:
    with FakeBaasServer() as srv:
        yield srv


def save_key(email: str, app_url: str | None) -> str:
    key = dev_api_key(email)
    values = {"BAAS_API_KEY": key}
    if app_url:
        values["BAAS_APP_URL"] = app_url
    envfile.update_env(envfile.BaasPaths.default(), values)
    return key


def run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out + out.err


def setup(project: Path, *extra: str) -> list[str]:
    return ["setup", "antigravity", "--project", str(project), *extra]


def read_config(project: Path) -> dict:
    return json.loads((project / CONFIG).read_text(encoding="utf-8"))


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


# --- el servidor `baas` ---------------------------------------------------------------------


def test_setup_writes_the_baas_server_with_url_and_bearer_header(home, project, server, capsys) -> None:
    key = save_key("analista@demo.test", server.url)
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert read_config(project) == {
        "mcpServers": {
            "baas": {"serverUrl": f"{server.url}/org/demo/mcp", "headers": {"Authorization": f"Bearer {key}"}}
        }
    }
    assert key not in out, "la clave no se imprime nunca"
    assert "demo" in out


def test_setup_ignores_the_file_in_git_and_never_writes_the_global_one(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    assert ".agents/mcp_config.json" in (project / ".gitignore").read_text().splitlines()
    assert not (home / ".gemini").exists(), "solo el mcp_config.json del proyecto, nunca el global"


@posix_only
def test_setup_leaves_the_file_with_permissions_0600(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    assert mode_of(project / CONFIG) == 0o600


@posix_only
def test_setup_tightens_the_permissions_of_an_existing_file_even_if_nothing_else_changes(
    home, project, server, capsys
) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    (project / CONFIG).chmod(0o644)
    assert run(setup(project), capsys)[0] == 0
    assert mode_of(project / CONFIG) == 0o600


def test_setup_keeps_other_servers_settings_and_extra_fields_of_baas(home, project, server, capsys) -> None:
    key = save_key("analista@demo.test", server.url)
    (project / ".agents").mkdir()
    (project / CONFIG).write_text(
        json.dumps(
            {
                "mcpServers": {
                    "otro": {"command": "npx", "args": ["-y", "otro-mcp"], "env": {"X": "1"}},
                    "baas": {
                        "url": "https://viejo.example/mcp",  # alias de serverUrl: dos transportes serían ambiguos
                        "headers": {"X-Otra": "1", "Authorization": "Bearer viejo"},
                        "disabledTools": ["baas_write_document"],
                        "timeoutSeconds": 60,
                    },
                },
                "ajuste": {"a": 1},
            }
        ),
        encoding="utf-8",
    )
    code, out = run(setup(project), capsys)
    assert code == 0, out
    config = read_config(project)
    assert config["ajuste"] == {"a": 1}
    assert config["mcpServers"]["otro"] == {"command": "npx", "args": ["-y", "otro-mcp"], "env": {"X": "1"}}
    assert config["mcpServers"]["baas"] == {
        "serverUrl": f"{server.url}/org/demo/mcp",
        "headers": {"X-Otra": "1", "Authorization": f"Bearer {key}"},
        "disabledTools": ["baas_write_document"],
        "timeoutSeconds": 60,
    }


def test_setup_is_idempotent(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    snapshot = {p: (project / p).read_bytes() for p in (CONFIG, ".gitignore")}
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert {p: (project / p).read_bytes() for p in snapshot} == snapshot
    assert "sin cambios" in out


def test_setup_refuses_invalid_json_and_writes_nothing(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    (project / ".agents").mkdir()
    (project / CONFIG).write_text('{"mcpServers": {', encoding="utf-8")
    code, out = run(setup(project), capsys)
    assert code == 7
    assert "BAAS_PROJECT_UNSAFE" in out
    assert (project / CONFIG).read_text(encoding="utf-8") == '{"mcpServers": {'
    assert not (project / ".gitignore").exists()


@pytest.mark.parametrize("content", ["[]", '{"mcpServers": []}', '{"mcpServers": {"baas": 3}}'])
def test_setup_refuses_a_config_with_the_wrong_shape(home, project, server, capsys, content) -> None:
    save_key("analista@demo.test", server.url)
    (project / ".agents").mkdir()
    (project / CONFIG).write_text(content, encoding="utf-8")
    code, out = run(setup(project), capsys)
    assert code == 7, out
    assert (project / CONFIG).read_text(encoding="utf-8") == content


def test_setup_refuses_to_write_a_tracked_file(home, project, server, capsys) -> None:
    key = save_key("analista@demo.test", server.url)
    git(project, "init", "-q")
    (project / ".agents").mkdir()
    (project / CONFIG).write_text("{}\n", encoding="utf-8")
    git(project, "add", "-f", CONFIG)
    code, out = run(setup(project), capsys)
    assert code == 7
    assert "BAAS_PROJECT_UNSAFE" in out
    assert "git rm --cached" in out
    assert (project / CONFIG).read_text(encoding="utf-8") == "{}\n"
    assert key not in out


def test_setup_refuses_a_missing_project_directory(home, tmp_path, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    code, _ = run(setup(tmp_path / "no-existe"), capsys)
    assert code == 2
    assert not (tmp_path / "no-existe").exists()


# --- la clave ---------------------------------------------------------------------------------


def test_setup_never_passes_the_key_as_an_argument(home, project, server, capsys, monkeypatch) -> None:
    key = save_key("analista@demo.test", server.url)
    spawned: list[object] = []
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        spawned.append((args, kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    assert run(setup(project), capsys)[0] == 0
    assert spawned, "el espía no ve ningún proceso (git): la prueba no comprobaría nada"
    assert key not in repr(spawned)


def test_setup_without_a_key_stops_and_writes_nothing(home, project, server, capsys) -> None:
    code, out = run(setup(project, "--app-url", server.url), capsys)
    assert code == 2
    assert "BAAS_KEY_MISSING" in out
    assert list(project.iterdir()) == []


def test_setup_with_a_revoked_key_is_an_auth_error(home, project, server, capsys) -> None:
    envfile.update_env(envfile.BaasPaths.default(), {"BAAS_API_KEY": "baas_" + "A" * 43, "BAAS_APP_URL": server.url})
    code, _ = run(setup(project), capsys)
    assert code == 5
    assert list(project.iterdir()) == []


# --- `--print` --------------------------------------------------------------------------------


def test_print_shows_what_would_be_written_without_the_key_and_writes_nothing(home, project, server, capsys) -> None:
    key = save_key("analista@demo.test", server.url)
    code, out = run(setup(project, "--print"), capsys)
    assert code == 0, out
    assert key not in out
    assert f"{server.url}/org/demo/mcp" in out
    assert "Bearer <API key>" in out
    assert CONFIG in out
    assert list(project.iterdir()) == []


# --- organización -----------------------------------------------------------------------------


def test_setup_with_several_organizations_asks_to_choose_and_writes_nothing(home, project, server, capsys) -> None:
    save_key("multi@demo.test", server.url)
    code, out = run(setup(project), capsys)
    assert code == 3
    assert "--org" in out
    assert list(project.iterdir()) == []


def test_setup_org_option_selects_the_organization(home, project, server, capsys) -> None:
    save_key("multi@demo.test", server.url)
    code, out = run(setup(project, "--org", "otra"), capsys)
    assert code == 0, out
    assert read_config(project)["mcpServers"]["baas"]["serverUrl"] == f"{server.url}/org/otra/mcp"


def test_setup_follows_the_organization_of_a_project_already_linked_with_baas_init(
    home, project, server, capsys
) -> None:
    save_key("multi@demo.test", server.url)
    (project / ".claude").mkdir()
    (project / ".claude/baas.local.json").write_text(json.dumps({"org": "otra"}), encoding="utf-8")
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert read_config(project)["mcpServers"]["baas"]["serverUrl"] == f"{server.url}/org/otra/mcp"


def test_setup_warns_when_the_organization_is_paused(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    server.state.orgs["demo"].paused = True
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert "pausada" in out


# --- lo que el usuario debe saber -------------------------------------------------------------


def test_setup_says_the_key_is_in_clear_and_that_the_chat_is_not_protected(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert "en claro" in out
    assert "sin anonimizar" in out
    assert "docs/antigravity.md" in out


def test_unknown_tool_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        cli.main(["setup", "no-existe"])


# --- la regla de proyecto (equivalente de la skill `baas-brain`) ----------------------------------

RULE = ".agents/rules/baas.md"
SKILL = Path(__file__).resolve().parents[1] / "skills" / "baas-brain" / "SKILL.md"
RULE_MAX_BYTES = 24_000  # tope por regla en Antigravity: lo que pasa se trunca en silencio


def frontmatter_of(text: str) -> dict:
    assert text.startswith("---\n"), "una regla sin frontmatter se descarta en silencio"
    return yaml.safe_load(text[4 : text.index("\n---", 4)])


def test_setup_writes_an_always_on_rule_within_the_size_limit(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    code, out = run(setup(project), capsys)
    assert code == 0, out
    rule = project / RULE
    assert rule.parent == project / ".agents" / "rules", "solo se leen los .md directos de .agents/rules/"
    text = rule.read_text(encoding="utf-8")
    meta = frontmatter_of(text)
    assert meta["trigger"] == "always_on"
    assert meta["description"].strip()
    assert len(text.encode("utf-8")) <= RULE_MAX_BYTES


def test_the_rule_teaches_every_tool_of_baas_brain_and_to_stop_on_denials(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    text = (project / RULE).read_text(encoding="utf-8")
    tools = set(re.findall(r"baas_[a-z]+(?:_[a-z]+)+", SKILL.read_text(encoding="utf-8")))
    assert len(tools) >= 10
    assert not {t for t in tools if t not in text}, "la regla no enseña alguna tool de la skill baas-brain"
    for code in ("AUTHORIZATION_DENIED", "KILL_SWITCH_ACTIVE"):
        assert code in text
    assert "Detente" in text


def test_the_rule_has_nothing_specific_to_claude_code_and_no_secret(home, project, server, capsys) -> None:
    key = save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    text = (project / RULE).read_text(encoding="utf-8")
    for claude_only in ("/baas-sync", "/baas-init", "CLAUDE_", "mcp__baas__", ".claude", "headersHelper"):
        assert claude_only not in text, claude_only
    assert key not in text
    assert "Bearer" not in text


def test_the_rule_is_idempotent_and_is_not_ignored_in_git(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    first = (project / RULE).read_bytes()
    assert run(setup(project), capsys)[0] == 0
    assert (project / RULE).read_bytes() == first
    ignored = (project / ".gitignore").read_text().splitlines()
    assert ".agents/mcp_config.json" in ignored
    assert not any("rules" in line for line in ignored), "la regla no lleva secretos: se puede versionar"


def test_setup_never_touches_agents_md_or_gemini_md(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    (project / "AGENTS.md").write_text("# Mis reglas\n", encoding="utf-8")
    assert run(setup(project), capsys)[0] == 0
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# Mis reglas\n"
    assert not (project / "GEMINI.md").exists()
    assert not (project / ".agents" / "AGENTS.md").exists()


def test_setup_does_not_overwrite_a_rule_of_the_user_with_the_same_name(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    (project / ".agents" / "rules").mkdir(parents=True)
    mine = "---\ntrigger: manual\n---\nMi regla.\n"
    (project / RULE).write_text(mine, encoding="utf-8")
    code, out = run(setup(project), capsys)
    assert code == 0, out
    assert (project / RULE).read_text(encoding="utf-8") == mine
    assert "AVISO" in out
    assert "no es de BaaS" in out
    assert (project / CONFIG).exists(), "el MCP se configura igualmente"


def test_setup_refreshes_a_rule_it_wrote_before(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    assert run(setup(project), capsys)[0] == 0
    current = (project / RULE).read_text(encoding="utf-8")
    stale = current.replace("baas_whoami", "baas_quien_soy")
    assert stale != current
    (project / RULE).write_text(stale, encoding="utf-8")
    assert run(setup(project), capsys)[0] == 0
    assert (project / RULE).read_text(encoding="utf-8") == current


def test_print_mentions_the_rule_and_writes_nothing(home, project, server, capsys) -> None:
    save_key("analista@demo.test", server.url)
    code, out = run(setup(project, "--print"), capsys)
    assert code == 0, out
    assert RULE in out
    assert "always_on" in out
    assert list(project.iterdir()) == []
