"""Testes do empacotamento como plugin do Claude Code.

O repo é simultaneamente marketplace e plugin: `.claude-plugin/marketplace.json`
cataloga, `.claude-plugin/plugin.json` descreve e declara o servidor MCP.
Contrato sob teste: os manifestos são JSON válido, carregam os campos que o
Claude Code exige, e o comando do servidor aponta para um arquivo que existe
de verdade dentro do plugin. Escritos ANTES dos manifestos (TDD).
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".claude-plugin"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
MARKETPLACE_JSON = PLUGIN_DIR / "marketplace.json"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"


def load(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class ManifestsExist(unittest.TestCase):
    def test_plugin_manifest_exists(self):
        self.assertTrue(PLUGIN_JSON.is_file(), "%s ausente" % PLUGIN_JSON)

    def test_marketplace_manifest_exists(self):
        self.assertTrue(MARKETPLACE_JSON.is_file(),
                        "%s ausente" % MARKETPLACE_JSON)

    def test_only_the_two_manifests_live_in_claude_plugin(self):
        """Regra do Claude Code: só plugin.json e marketplace.json ali dentro."""
        found = sorted(p.name for p in PLUGIN_DIR.iterdir())
        self.assertEqual(found, ["marketplace.json", "plugin.json"])


class PluginManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = load(PLUGIN_JSON)

    def test_name_is_the_only_required_field_and_is_present(self):
        self.assertEqual(self.manifest.get("name"), "cortex")

    def test_carries_version_and_license(self):
        self.assertIn("version", self.manifest)
        self.assertEqual(self.manifest.get("license"), "MIT")

    def test_declares_the_cortex_mcp_server(self):
        servers = self.manifest.get("mcpServers", {})
        self.assertIn("cortex", servers)

    def test_server_command_targets_a_file_that_exists_in_the_plugin(self):
        server = self.manifest["mcpServers"]["cortex"]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(len(server["args"]), 1)
        arg = server["args"][0]
        self.assertTrue(arg.startswith(PLUGIN_ROOT_VAR),
                        "caminho precisa ser relativo ao plugin: %s" % arg)
        target = ROOT / arg[len(PLUGIN_ROOT_VAR):].lstrip("/")
        self.assertTrue(target.is_file(),
                        "o comando aponta para arquivo inexistente: %s" % target)

    def test_memory_never_lands_inside_the_plugin_directory(self):
        """${CLAUDE_PLUGIN_ROOT} é efêmero: some no update. O banco vive no
        projeto, nunca dentro do plugin."""
        env = self.manifest["mcpServers"]["cortex"].get("env", {})
        base = env.get("CORTEX_DIR", "")
        self.assertNotIn(PLUGIN_ROOT_VAR, base)
        self.assertEqual(base, "${CLAUDE_PROJECT_DIR}")


class MarketplaceManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = load(MARKETPLACE_JSON)

    def test_has_the_three_required_fields(self):
        for field in ("name", "owner", "plugins"):
            self.assertIn(field, self.manifest)

    def test_lists_cortex_sourced_from_the_repo_root(self):
        entries = {p["name"]: p for p in self.manifest["plugins"]}
        self.assertIn("cortex", entries)
        self.assertEqual(entries["cortex"]["source"], "./")

    def test_version_matches_the_plugin_manifest(self):
        entry = next(p for p in self.manifest["plugins"]
                     if p["name"] == "cortex")
        self.assertEqual(entry.get("version"), load(PLUGIN_JSON).get("version"))


class DiscoverabilityContract(unittest.TestCase):
    """O que um agente lendo o repo precisa encontrar para instalar sozinho."""

    def test_readme_documents_the_plugin_install(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("plugin marketplace add sfaustodev/cortex", readme)
        self.assertIn("plugin install cortex@cortex", readme)

    def test_readme_documents_the_scoped_tool_names(self):
        """Instalado como plugin, as tools mudam de nome — quem escreve regra
        de permissão precisa do nome real."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("mcp__plugin_cortex_cortex__cortex_briefing", readme)


if __name__ == "__main__":
    unittest.main()
