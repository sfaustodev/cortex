"""Isolamento por projeto — protocolo MCP stdio REAL, subprocessos de verdade.

A promessa do README ("nothing you store here leaks into another project's
briefing") só é verdade se a identidade da tarefa não puder ser resolvida
errado. Hoje ela nasce do cwd e, quando o host lança de `/` ou `$HOME`, o
servidor apenas AVISA no stderr — que ninguém lê — e serve uma memória
global. Estes testes fixam o contrato novo. Escritos ANTES da implementação.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "cortex_server.py"
LATEST = "2025-06-18"


class Server:
    """Cliente do protocolo, com cwd e env controlados — é o cwd que define
    a tarefa, então ele é o sujeito do teste."""

    def __init__(self, cwd, env_extra=None):
        env = os.environ.copy()
        env.pop("CORTEX_DIR", None)
        env.pop("CORTEX_ADOPT", None)
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True,
        )
        self._next_id = 100

    def _read_line(self, timeout=15):
        box = {}

        def target():
            box["line"] = self.proc.stdout.readline()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout)
        if "line" not in box:
            self.kill()
            raise AssertionError("timeout esperando resposta do servidor")
        return box["line"]

    def request(self, method, params=None):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return json.loads(self._read_line())

    def initialize(self):
        resp = self.request("initialize", {
            "protocolVersion": LATEST, "capabilities": {},
            "clientInfo": {"name": "iso", "version": "0"}})
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0",
                        "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()
        return resp

    def call(self, name, arguments=None):
        resp = self.request("tools/call", {"name": name,
                                           "arguments": arguments or {}})
        result = resp["result"]
        return result["content"][0]["text"], bool(result.get("isError"))

    def kill(self):
        self.proc.kill()
        self.proc.wait(10)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass


class IsolationCase(unittest.TestCase):
    def setUp(self):
        self._dirs = []
        self._servers = []

    def tearDown(self):
        for s in self._servers:
            try:
                s.kill()
            except Exception:  # noqa: BLE001
                pass
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def project(self, prefix="proj-"):
        d = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        self._dirs.append(str(d))
        return d

    def server(self, cwd, **env):
        s = Server(cwd, env)
        self._servers.append(s)
        s.initialize()
        return s


class MemoriesDoNotLeakBetweenProjects(IsolationCase):
    SECRET = "segredo-exclusivo-de-A-xyzzy"

    def test_what_a_writes_b_never_sees(self):
        a, b = self.project("iso-a-"), self.project("iso-b-")
        text, err = self.server(a).call(
            "cortex_remember", {"type": "decision", "text": self.SECRET})
        self.assertFalse(err, text)

        srv_b = self.server(b)
        for tool, args in (("cortex_briefing", {}),
                           ("cortex_recall", {"query": "segredo"}),
                           ("cortex_recall", {})):
            text, _ = srv_b.call(tool, args)
            self.assertNotIn(self.SECRET, text,
                             "%s vazou memória de outro projeto" % tool)

    def test_isolation_is_symmetric(self):
        a, b = self.project("iso-sa-"), self.project("iso-sb-")
        self.server(a).call("cortex_remember",
                            {"type": "fact", "text": "coisa-de-A"})
        self.server(b).call("cortex_remember",
                            {"type": "fact", "text": "coisa-de-B-plugh"})
        text, _ = self.server(a).call("cortex_briefing", {})
        self.assertNotIn("plugh", text)


class GlobalDirectoryIsRefused(IsolationCase):
    """Hoje o servidor só avisa no stderr e serve uma memória global — o que
    torna falsa a promessa 'nothing leaks into another project's briefing'."""

    def test_home_as_root_fails_closed_and_creates_nothing(self):
        home = self.project("iso-home-")
        srv = self.server(home, HOME=str(home))
        for tool, args in (("cortex_briefing", {}),
                           ("cortex_recall", {}),
                           ("cortex_remember", {"type": "fact", "text": "x"})):
            text, err = srv.call(tool, args)
            self.assertTrue(err, "%s deveria recusar em $HOME" % tool)
            self.assertIn("CORTEX_DIR", text, "o erro precisa ensinar a saída")
        self.assertFalse((home / ".cortex").exists(),
                         "nada pode ser criado em $HOME")

    def test_filesystem_root_fails_closed(self):
        srv = self.server("/")
        text, err = srv.call("cortex_remember", {"type": "fact", "text": "x"})
        self.assertTrue(err)


class ProjectRootResolution(IsolationCase):
    def test_subdirectory_of_a_repo_shares_the_repo_memory(self):
        repo = self.project("iso-mono-")
        (repo / ".git").mkdir()
        sub = repo / "packages" / "web"
        sub.mkdir(parents=True)

        text, err = self.server(sub).call(
            "cortex_remember",
            {"type": "decision", "text": "decisao-do-monorepo"})
        self.assertFalse(err, text)
        self.assertTrue((repo / ".cortex").exists(),
                        "memória tem que morar na raiz do repo")
        self.assertFalse((sub / ".cortex").exists(),
                         "subpasta não pode ganhar caderno paralelo")

        text, _ = self.server(repo).call("cortex_briefing", {})
        self.assertIn("decisao-do-monorepo", text,
                      "raiz e subpasta compartilham a MESMA memória")

    def test_briefing_still_opens_with_the_db_path(self):
        """Contrato antigo que o isolamento não pode quebrar: a primeira
        linha diz QUAL memória está aberta, então uma tarefa errada é
        visível de imediato."""
        proj = self.project("iso-named-")
        text, _ = self.server(proj).call("cortex_briefing", {})
        first = text.splitlines()[0]
        self.assertIn(str(proj / ".cortex" / "cortex.db"), first)


class MemoryIsStamped(IsolationCase):
    def test_owner_and_gitignore_are_born_with_the_directory(self):
        proj = self.project("iso-birth-")
        self.server(proj).call("cortex_briefing", {})
        cortex = proj / ".cortex"
        self.assertTrue((cortex / "OWNER").is_file(), "OWNER ausente")
        self.assertIn(str(proj), (cortex / "OWNER").read_text())
        self.assertTrue((cortex / ".gitignore").is_file(),
                        ".gitignore ausente — a memória vazaria pro git")
        self.assertEqual((cortex / ".gitignore").read_text().strip(), "*")

    def test_symlinked_root_still_matches_its_owner(self):
        real = self.project("iso-real-")
        link = Path(tempfile.mkdtemp(prefix="iso-link-")) / "alias"
        self._dirs.append(str(link.parent))
        link.symlink_to(real)

        text, err = self.server(link).call(
            "cortex_remember", {"type": "fact", "text": "via symlink"})
        self.assertFalse(err, text)
        text, err = self.server(real).call(
            "cortex_remember", {"type": "fact", "text": "via caminho real"})
        self.assertFalse(err, "mesmo projeto por dois caminhos não é mismatch")


class CopiedMemoryIsReadOnly(IsolationCase):
    def _project_with_memory(self, prefix, text="conteudo-legitimo"):
        proj = self.project(prefix)
        self.server(proj).call("cortex_remember",
                               {"type": "fact", "text": text})
        return proj

    def test_copied_directory_refuses_writes_but_still_reads(self):
        a = self._project_with_memory("iso-own-a-")
        b = self.project("iso-own-b-")
        shutil.copytree(str(a / ".cortex"), str(b / ".cortex"))

        srv = self.server(b)
        text, err = srv.call("cortex_remember", {"type": "fact", "text": "x"})
        self.assertTrue(err, "não pode gravar na memória de outro projeto")
        self.assertIn(str(a), text, "a recusa precisa citar o dono real")
        self.assertIn("CORTEX_ADOPT", text)

        text, err = srv.call("cortex_briefing", {})
        self.assertFalse(err, "leitura continua disponível")
        self.assertIn("conteudo-legitimo", text)

    def test_adoption_is_bound_to_the_owner_value(self):
        a = self._project_with_memory("iso-adopt-a-")
        b = self.project("iso-adopt-b-")
        shutil.copytree(str(a / ".cortex"), str(b / ".cortex"))

        text, err = self.server(b, CORTEX_ADOPT="1").call(
            "cortex_remember", {"type": "fact", "text": "x"})
        self.assertTrue(err, "flag genérica não pode adotar")

        text, err = self.server(b, CORTEX_ADOPT=str(a)).call(
            "cortex_remember", {"type": "fact", "text": "agora-e-de-b"})
        self.assertFalse(err, text)

        text, err = self.server(b).call(
            "cortex_remember", {"type": "fact", "text": "pos-adocao"})
        self.assertFalse(err, "depois de adotada, grava sem env nenhum")

    def test_cortex_dir_pointing_at_another_project_refuses(self):
        a = self._project_with_memory("iso-env-a-")
        b = self.project("iso-env-b-")
        text, err = self.server(b, CORTEX_DIR=str(a)).call(
            "cortex_remember", {"type": "fact", "text": "invasao"})
        self.assertTrue(err, "CORTEX_DIR residual não pode gravar em A")
        self.assertNotIn("invasao",
                         (a / ".cortex" / "cortex.db").read_bytes().decode(
                             "utf-8", "ignore"))

    def test_owner_is_rechecked_on_every_write(self):
        proj = self._project_with_memory("iso-live-")
        srv = self.server(proj)
        owner = proj / ".cortex" / "OWNER"
        owner.write_text(json.dumps({"root": "/algum/outro/projeto"}) + "\n")
        text, err = srv.call("cortex_remember", {"type": "fact", "text": "x"})
        self.assertTrue(err, "processo vivo não grava após o dono mudar")


class ExistingMemoriesKeepWorking(IsolationCase):
    """O cortex já está publicado e em uso: bancos anteriores não têm OWNER.
    Tratá-los como órfãos os deixaria somente-leitura e quebraria toda
    instalação existente. Um .cortex/ na SUA PRÓPRIA raiz não é cópia — é
    onde deveria estar — então é adotado e segue gravável."""

    def test_pre_existing_memory_without_owner_is_adopted_in_place(self):
        proj = self.project("iso-legacy-")
        srv = self.server(proj)
        srv.call("cortex_remember",
                 {"type": "decision", "text": "memoria-anterior-ao-upgrade"})
        srv.kill()
        (proj / ".cortex" / "OWNER").unlink()   # como era antes do upgrade

        srv2 = self.server(proj)
        text, err = srv2.call("cortex_remember",
                              {"type": "fact", "text": "depois-do-upgrade"})
        self.assertFalse(err, "memória pré-existente não pode virar read-only")

        text, _ = srv2.call("cortex_briefing", {})
        self.assertIn("memoria-anterior-ao-upgrade", text,
                      "o conteúdo antigo continua legível")
        self.assertIn("depois-do-upgrade", text)
        self.assertTrue((proj / ".cortex" / "OWNER").is_file(),
                        "a partir daqui passa a ser carimbada")


if __name__ == "__main__":
    unittest.main()
