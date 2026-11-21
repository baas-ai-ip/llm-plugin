"""`docs/antigravity.md` (T3 · #165): lo que documenta coincide con lo que escribe `setup` y no deja ninguna clave."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "antigravity.md"


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text(), flags=re.S)


def test_the_readme_links_the_guide() -> None:
    assert "docs/antigravity.md" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_documented_config_has_the_shape_that_setup_writes() -> None:
    configs = [json.loads(b) for b in blocks("json")]
    config = next(c for c in configs if "baas" in c.get("mcpServers", {}))
    server = config["mcpServers"]["baas"]
    assert set(server) == {"serverUrl", "headers"}
    assert server["headers"] == {"Authorization": "Bearer <API key>"}
    assert server["serverUrl"].startswith("https://")


def test_the_key_is_never_written_in_the_doc() -> None:
    # una API key es `baas_` + 43 caracteres base64url (contrato §3)
    assert not re.search(r"baas_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", text()), "hay una API key literal"


def test_the_doc_gives_the_commands_for_the_extension_and_the_cli() -> None:
    doc = text()
    commands = "\n".join(blocks("bash"))
    assert "baas.py" in commands
    assert "setup antigravity" in commands
    assert "--print" in commands
    for needle in ("Google.google-antigravity", "agy", "/mcp", ".agents/mcp_config.json", ".agents/rules/baas.md"):
        assert needle in doc, needle


def test_the_doc_says_what_antigravity_does_not_cover() -> None:
    doc = text()
    for needle in ("sin anonimizar", "proxy", "AI-IP", "en claro", "~/.gemini/config/mcp_config.json", "24 000"):
        assert needle in doc, needle


def test_the_doc_lists_the_manual_checks_and_the_open_questions() -> None:
    doc = text()
    assert "Lista de comprobación" in doc
    assert "- [ ]" in doc
    for needle in ("carpeta sin confianza", "instructions", "mismo `mcp_config.json`", ".agents/skills"):
        assert needle in doc, needle
