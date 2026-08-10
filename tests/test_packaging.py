"""Testes do empacotamento para PyPI e do verbete do MCP Registry.

Quatro arquivos passam a carregar a mesma versão (plugin.json,
marketplace.json, pyproject.toml, server.json) — divergir entre eles é o
modo de falha óbvio, e a publicação no registry é IMUTÁVEL: versão
publicada errada não se corrige, só se substitui por outra. Daí a trava.
Escritos ANTES dos arquivos que testam (TDD).
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
SERVER_JSON = ROOT / "server.json"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"

REGISTRY_NAME = "io.github.sfaustodev/cortex"
PYPI_NAME = "cortex-mcp-server"
# O registry só baixa de npm/pypi/nuget/cargo/oci/mcpb — não existe tipo
# "source"/"git". Um servidor que roda do clone precisa de pacote publicado.
SUPPORTED_REGISTRY_TYPES = {"npm", "pypi", "nuget", "cargo", "oci", "mcpb"}


def toml_value(text, key, section):
    """Extrai `key = "valor"` de dentro de [section]. Suficiente para os
    campos escalares que checamos, e roda no Python 3.9 (tomllib é 3.11+)."""
    block = re.split(r"^\[", text, flags=re.M)
    for chunk in block:
        if chunk.startswith(section + "]"):
            found = re.search(r'^%s\s*=\s*"([^"]+)"' % re.escape(key),
                              chunk, flags=re.M)
            return found.group(1) if found else None
    return None


class PyprojectExists(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(PYPROJECT.is_file(), "pyproject.toml ausente")


class Pyproject(unittest.TestCase):
    def setUp(self):
        self.text = PYPROJECT.read_text(encoding="utf-8")

    def test_package_name_is_the_one_reserved_on_pypi(self):
        self.assertEqual(toml_value(self.text, "name", "project"), PYPI_NAME)

    def test_supports_python_39(self):
        self.assertIn("3.9", toml_value(self.text, "requires-python",
                                        "project") or "")

    def test_declares_zero_runtime_dependencies(self):
        """O argumento de venda do repo é 'zero dependencies'. Uma dependência
        que entre aqui desmente o README inteiro."""
        deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", self.text,
                         flags=re.M | re.S)
        self.assertIsNotNone(deps, "dependencies precisa ser declarado")
        self.assertEqual(deps.group(1).strip(), "")

    def test_wheel_ships_both_modules(self):
        """cortex_server importa cortex_store pelo nome — se o wheel levar só
        um dos dois, o console script quebra no import."""
        include = re.search(
            r"^\[tool\.hatch\.build\.targets\.wheel\]\s*\ninclude\s*=\s*\[(.*?)\]",
            self.text, flags=re.M | re.S)
        self.assertIsNotNone(include, "o alvo wheel precisa declarar include")
        shipped = set(re.findall(r'"([^"]+)"', include.group(1)))
        self.assertEqual(shipped, {"cortex_server.py", "cortex_store.py"})

    def test_exposes_a_console_script_entry_point(self):
        entry = toml_value(self.text, PYPI_NAME, "project.scripts")
        self.assertEqual(entry, "cortex_server:main")


class ServerJsonExists(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(SERVER_JSON.is_file(), "server.json ausente")


class ServerJson(unittest.TestCase):
    def setUp(self):
        with SERVER_JSON.open(encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def test_has_the_three_required_fields(self):
        for field in ("name", "description", "version"):
            self.assertIn(field, self.manifest)

    def test_name_is_reverse_dns_with_exactly_one_slash(self):
        self.assertEqual(self.manifest["name"], REGISTRY_NAME)
        self.assertEqual(self.manifest["name"].count("/"), 1)

    def test_description_fits_the_100_char_ceiling(self):
        """O schema do registry rejeita acima de 100 — e a description atual
        do repo no GitHub tem 116."""
        self.assertLessEqual(len(self.manifest["description"]), 100)

    def test_declares_a_downloadable_package(self):
        packages = self.manifest.get("packages", [])
        self.assertEqual(len(packages), 1)
        pkg = packages[0]
        self.assertIn(pkg["registryType"], SUPPORTED_REGISTRY_TYPES)
        self.assertEqual(pkg["registryType"], "pypi")
        self.assertEqual(pkg["identifier"], PYPI_NAME)
        self.assertEqual(pkg["transport"]["type"], "stdio")

    def test_package_version_is_exact_not_a_range(self):
        version = self.manifest["packages"][0]["version"]
        self.assertRegex(version, r"^\d+\.\d+\.\d+")

    def test_documents_cortex_dir_so_hosts_can_pin_the_task(self):
        env = self.manifest["packages"][0].get("environmentVariables", [])
        self.assertIn("CORTEX_DIR", [e["name"] for e in env])


class VersionsAgreeAcrossEveryManifest(unittest.TestCase):
    """Versão publicada no registry é imutável: divergência aqui vira erro
    que não dá para corrigir, só para substituir."""

    def test_all_four_files_carry_the_same_version(self):
        with PLUGIN_JSON.open(encoding="utf-8") as fh:
            plugin = json.load(fh)["version"]
        with MARKETPLACE_JSON.open(encoding="utf-8") as fh:
            market = json.load(fh)["plugins"][0]["version"]
        with SERVER_JSON.open(encoding="utf-8") as fh:
            server = json.load(fh)
        pyproject = toml_value(PYPROJECT.read_text(encoding="utf-8"),
                               "version", "project")
        self.assertEqual(
            {plugin, market, pyproject, server["version"],
             server["packages"][0]["version"]},
            {plugin},
            "plugin.json, marketplace.json, pyproject.toml e server.json "
            "(server e package) precisam declarar a MESMA versão")


class RegistryOwnershipMarker(unittest.TestCase):
    """Para aceitar um pacote PyPI, o registry busca o projeto no PyPI e
    procura este marcador no README — é a prova de que quem publica o
    verbete controla o pacote."""

    def test_readme_carries_the_mcp_name_marker(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("mcp-name: %s" % REGISTRY_NAME, readme)


class ReadmeClaimsMatchReality(unittest.TestCase):
    """A seção 'To the AI reading this' convida a verificar em vez de
    confiar. Então cada número dela vira teste."""

    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_line_count_claim_matches_the_shipped_modules(self):
        """Lê o número que o README afirma e confere contra a contagem real —
        assim a trava sobrevive a um módulo novo em vez de fossilizar."""
        shipped = sorted(p.name for p in ROOT.glob("cortex_*.py"))
        total = sum(len((ROOT / f).read_text(encoding="utf-8").splitlines())
                    for f in shipped)
        claimed = re.search(r"~([\d,.]+) lines", self.readme)
        self.assertIsNotNone(claimed, "o README precisa afirmar a contagem")
        number = int(claimed.group(1).replace(",", "").replace(".", ""))
        self.assertLess(
            abs(total - number), number * 0.15,
            "o README diz ~%d linhas; %s somam %d" % (number, shipped, total))

    def test_file_count_claim_matches_reality(self):
        shipped = sorted(p.name for p in ROOT.glob("cortex_*.py"))
        words = {2: "two files", 3: "three files", 4: "four files"}
        self.assertIn(words[len(shipped)], self.readme,
                      "o README precisa dizer %s (%s)"
                      % (words[len(shipped)], shipped))

    def test_the_suggested_grep_really_returns_nothing(self):
        """O README mandava procurar 'strings de versão de protocolo' que não
        existem: o grep volta vazio."""
        pattern = re.compile(r"http|socket|urllib|requests|telemetry")
        for name in ("cortex_server.py", "cortex_store.py"):
            body = (ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(body),
                              "%s contém termo de rede" % name)
        self.assertNotIn("finds protocol version strings", self.readme)


if __name__ == "__main__":
    unittest.main()
