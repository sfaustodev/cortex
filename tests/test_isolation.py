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


def _tempdir_outside_any_repo():
    """Um TMPDIR dentro de um repositório git faz os projetos de teste
    herdarem AQUELE repo como raiz — e nove testes falham por um motivo que
    nada tem a ver com o que verificam. Acontece de verdade: é o que a
    ferramenta de review faz ao apontar TMPDIR para dentro do checkout.
    """
    current = Path(tempfile.gettempdir()).resolve()
    for d in (current,) + tuple(current.parents):
        if (d / ".git").exists():
            neutro = Path(os.sep + "tmp")
            if os.access(str(neutro), os.W_OK):
                tempfile.tempdir = tempfile.mkdtemp(
                    prefix="cortex-tests-", dir=str(neutro))
            return


_tempdir_outside_any_repo()


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

    def test_cortex_dir_is_self_owned_and_stable_across_cwds(self):
        """CORTEX_DIR aponta a tarefa; a identidade É esse diretório, não o
        cwd de quem abriu. Carimbar o cwd faria a memória pertencer ao
        primeiro que chegou — e o segundo lançamento, de outro diretório,
        cairia em somente-leitura permanente. É justamente a configuração
        que o README recomenda para hosts de cwd imprevisível."""
        tarefa = self.project("iso-pin-")
        for origem in ("iso-cwd-a-", "iso-cwd-b-"):
            cwd = self.project(origem)
            text, err = self.server(cwd, CORTEX_DIR=str(tarefa)).call(
                "cortex_remember", {"type": "fact", "text": "de-%s" % origem})
            self.assertFalse(err, "lançar de outro cwd travou a memória: %s"
                                  % text)
        owner = json.loads((tarefa / ".cortex" / "OWNER").read_text())
        self.assertEqual(owner["root"], str(tarefa),
                         "o dono tem que ser o diretório apontado, não o cwd")

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


class WorktreeMemoryBelongsToTheRepo(IsolationCase):
    """Numa worktree vinculada, `.git` é um ARQUIVO apontando para o repo
    principal. Tratá-lo como raiz faz a memória nascer dentro de um
    diretório efêmero — e o Claude Code remove worktrees rotineiramente,
    levando junto tudo que a tarefa aprendeu."""

    def _repo_with_worktree(self):
        repo = self.project("iso-wt-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "a.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "tarefa1"
        run("git", "worktree", "add", "-q", str(wt), "-b", "tarefa1")
        return repo, wt

    def test_memory_written_in_a_worktree_lands_in_the_main_repo(self):
        repo, wt = self._repo_with_worktree()
        text, err = self.server(wt).call(
            "cortex_remember",
            {"type": "decision", "text": "decisao-tomada-na-worktree"})
        self.assertFalse(err, text)
        self.assertTrue((repo / ".cortex").exists(),
                        "memória tem que morar no repo principal")
        self.assertFalse((wt / ".cortex").exists(),
                         "nada pode nascer dentro da worktree efêmera")

    def test_memory_survives_removing_the_worktree(self):
        repo, wt = self._repo_with_worktree()
        self.server(wt).call(
            "cortex_remember",
            {"type": "decision", "text": "decisao-que-nao-pode-morrer"})
        for s in list(self._servers):
            s.kill()
        subprocess.run(("git", "worktree", "remove", "--force", str(wt)),
                       cwd=str(repo), check=True, capture_output=True)

        text, _ = self.server(repo).call("cortex_briefing", {})
        self.assertIn("decisao-que-nao-pode-morrer", text,
                      "a memória morreu junto com a worktree")

    def test_worktree_and_main_repo_share_one_memory(self):
        repo, wt = self._repo_with_worktree()
        self.server(repo).call("cortex_remember",
                               {"type": "fact", "text": "gravado-na-main"})
        text, _ = self.server(wt).call("cortex_briefing", {})
        self.assertIn("gravado-na-main", text,
                      "a worktree tem que enxergar a memória do repo")


class ContaminatedWorktreeStillResolvesToTheRepo(IsolationCase):
    """Trava do latch: o walk-up testava `.cortex/` ANTES de `.git`, então um
    diretório criado pelo bug virava a causa da resolução seguinte — o bug
    sobrevivia ao próprio conserto. A marca de worktree tem que vencer."""

    def test_existing_cortex_inside_a_worktree_is_ignored(self):
        repo = self.project("iso-latch-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "a.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "contaminada"
        run("git", "worktree", "add", "-q", str(wt), "-b", "contaminada")
        (wt / ".cortex").mkdir()   # resíduo deixado pela versão com o bug

        text, err = self.server(wt).call(
            "cortex_remember", {"type": "fact", "text": "depois-do-conserto"})
        self.assertFalse(err, text)
        text, _ = self.server(repo).call("cortex_briefing", {})
        self.assertIn("depois-do-conserto", text,
                      "a gravação foi para o .cortex órfão da worktree")


class PluginManifestDoesNotDisableTheResolver(unittest.TestCase):
    """Trava mecânica do incidente: o manifesto injetava
    CORTEX_DIR=${CLAUDE_PROJECT_DIR}, que numa worktree É a worktree — e o
    early-return de CORTEX_DIR fazia toda a resolução (inclusive o desvio de
    worktree) virar código morto. Config que sempre preenche o campo de
    override anula qualquer lógica que rode depois dele."""

    def test_manifest_never_pins_cortex_dir(self):
        manifest = json.loads(
            (SERVER.parent / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8"))
        for name, server in manifest.get("mcpServers", {}).items():
            env = server.get("env", {})
            self.assertNotIn(
                "CORTEX_DIR", env,
                "o manifesto do servidor %r não pode fixar CORTEX_DIR: isso "
                "curto-circuita a resolução de raiz e reintroduz o bug da "
                "worktree" % name)


class SubmoduleIsNotAWorktree(IsolationCase):
    """Submódulo TAMBÉM tem `.git` como arquivo — `gitdir: ../../.git/modules/
    <path>`. Confundi-lo com worktree manda a memória da biblioteca para o
    superprojeto: some do lugar certo e mistura com outro projeto."""

    def _super_with_submodule(self):
        base = self.project("iso-sub-")
        lib, sup = base / "lib", base / "super"
        for d in (lib, sup):
            d.mkdir()
            run = lambda *a, _d=d: subprocess.run(  # noqa: E731
                a, cwd=str(_d), check=True, capture_output=True)
            run("git", "init", "-q")
            run("git", "config", "user.email", "t@t")
            run("git", "config", "user.name", "t")
            (d / "f.txt").write_text("x")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "init")
        subprocess.run(
            ("git", "-c", "protocol.file.allow=always", "submodule", "add",
             "-q", str(lib), "vendor/lib"),
            cwd=str(sup), check=True, capture_output=True)
        return sup, sup / "vendor" / "lib"

    def test_submodule_keeps_its_own_memory(self):
        sup, sub = self._super_with_submodule()
        self.assertTrue((sub / ".git").is_file(), "pré-condição: .git-arquivo")

        text, err = self.server(sub).call(
            "cortex_remember", {"type": "decision", "text": "decisao-da-lib"})
        self.assertFalse(err, text)
        self.assertTrue((sub / ".cortex").exists(),
                        "a memória do submódulo tem que ficar nele")

        text, _ = self.server(sup).call("cortex_briefing", {})
        self.assertNotIn("decisao-da-lib", text,
                         "memória do submódulo vazou para o superprojeto")


class OrphanWorktreeDoesNotResurrectItsRepo(IsolationCase):
    """Worktree cujo repositório principal foi apagado: resolver para o
    caminho morto faria o servidor RECRIAR o diretório que o humano
    deletou, e esconder a memória num fantasma."""

    def test_falls_back_to_the_worktree_when_the_repo_is_gone(self):
        base = self.project("iso-orphan-")
        repo, wt = base / "repo", base / "solta"
        repo.mkdir()
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "f.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        run("git", "worktree", "add", "-q", str(wt), "-b", "solta")
        shutil.rmtree(str(repo))

        text, err = self.server(wt).call(
            "cortex_remember", {"type": "fact", "text": "orfa"})
        self.assertFalse(err, text)
        self.assertFalse(repo.exists(),
                         "o repositório apagado não pode ser recriado")
        self.assertTrue((wt / ".cortex").exists(),
                        "sem repo vivo, a memória fica onde há trabalho")


class StrandedMemoryIsAnnounced(IsolationCase):
    """Achado do trio (codex #3 / muse A4): o desvio de worktree faz o
    servidor IGNORAR um `<worktree>/.cortex` deixado pela versão com o bug.
    Ignorar em silêncio é a falha original de novo — o humano vê briefing
    vazio e não sabe que o histórico existe a dois diretórios dali."""

    def _repo_with_stranded_memory(self):
        repo = self.project("iso-stranded-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "f.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "antiga"
        run("git", "worktree", "add", "-q", str(wt), "-b", "antiga")
        # memória deixada pela v2.0.1: nasceu DENTRO da worktree
        (wt / ".cortex").mkdir()
        (wt / ".cortex" / "cortex.db").write_bytes(b"SQLite format 3\x00")
        return repo, wt

    def test_briefing_points_at_the_memory_left_behind(self):
        repo, wt = self._repo_with_stranded_memory()
        text, err = self.server(wt).call("cortex_briefing", {})
        self.assertFalse(err, text)
        self.assertIn(str(wt / ".cortex"), text,
                      "o briefing tem que dizer ONDE ficou a memória órfã")
        self.assertIn("cortex.db", text)

    def test_no_warning_when_there_is_nothing_left_behind(self):
        repo = self.project("iso-clean-")
        text, _ = self.server(repo).call("cortex_briefing", {})
        self.assertNotIn("órf", text.lower())


class MemoryNeverLandsWhereTheServerLives(IsolationCase):
    """Achado do trio (codex #2/#6): sem CORTEX_DIR a raiz vem do cwd. Se um
    host lançar o servidor de dentro do próprio diretório de instalação (o
    cache do plugin, que é efêmero), a memória nasceria ali e sumiria no
    próximo update — sem ninguém perceber."""

    def test_briefing_refuses_and_explains_in_the_install_dir(self):
        text, err = self.server(SERVER.parent).call("cortex_briefing", {})
        self.assertTrue(err, "não há memória aqui e não vamos criar uma")
        self.assertIn("instala", text.lower(),
                      "a recusa precisa dizer que a raiz é o diretório do "
                      "próprio servidor")
        self.assertIn("CORTEX_DIR", text, "e ensinar a saída")
        self.assertFalse((SERVER.parent / ".cortex").exists())


class ConcurrentWritesFromRepoAndWorktree(IsolationCase):
    """Achado do trio (codex #5 / muse A5): depois deste PR, main e worktree
    compartilham o MESMO banco — o caminho concorrente virou o default, e o
    teste de compartilhamento era sequencial."""

    def test_both_processes_write_without_losing_entries(self):
        repo = self.project("iso-conc-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "f.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "par"
        run("git", "worktree", "add", "-q", str(wt), "-b", "par")

        srv_main, srv_wt = self.server(repo), self.server(wt)
        for i in range(12):   # intercalado: as duas sessões vivas ao mesmo tempo
            for srv, tag in ((srv_main, "main"), (srv_wt, "wt")):
                text, err = srv.call(
                    "cortex_remember",
                    {"type": "fact", "text": "%s-entrada-%d" % (tag, i)})
                self.assertFalse(err, "escrita concorrente recusada: %s" % text)

        text, _ = srv_main.call("cortex_recall", {"limit": 50})
        for tag in ("main", "wt"):
            self.assertIn("%s-entrada-11" % tag, text,
                          "entrada de %s se perdeu no banco compartilhado" % tag)


class WorktreeIsAuthenticatedByGitMetadata(IsolationCase):
    """Rodada 2 do trio (codex itens 1 e 5, provados de novo por mim).

    Reconhecer worktree pelo NOME de um componente do caminho é insustentável:
    o nome é escolhido por quem monta o repositório. Um submódulo em
    `worktrees/lib` batia o padrão e a memória ia parar DENTRO de
    `.git/modules`. E um path de repositório reutilizado por outro `git init`
    fazia a worktree antiga contaminar o projeto novo.

    A autenticação passa a ser o metadado do próprio git: o admin dir de uma
    linked worktree tem `commondir` e um `gitdir` que aponta de volta para o
    `.git` que estamos lendo. Submódulo não tem `commondir`.
    """

    def _git(self, cwd, *args):
        subprocess.run(("git",) + args, cwd=str(cwd), check=True,
                       capture_output=True)

    def _repo(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self._git(path, "init", "-q")
        self._git(path, "config", "user.email", "t@t")
        self._git(path, "config", "user.name", "t")
        (path / "f.txt").write_text("x")
        self._git(path, "add", "-A")
        self._git(path, "commit", "-qm", "init")
        return path

    def test_submodule_named_worktrees_is_not_mistaken(self):
        base = self.project("iso-auth-sub-")
        lib = self._repo(base / "lib")
        sup = self._repo(base / "super")
        subprocess.run(
            ("git", "-c", "protocol.file.allow=always", "submodule", "add",
             "-q", str(lib), "worktrees/lib"),
            cwd=str(sup), check=True, capture_output=True)
        sub = sup / "worktrees" / "lib"

        text, err = self.server(sub).call(
            "cortex_remember", {"type": "fact", "text": "da-lib"})
        self.assertFalse(err, text)
        self.assertTrue((sub / ".cortex").exists(),
                        "submódulo tem que ficar com a memória dele")
        self.assertFalse((sup / ".git" / "modules" / ".cortex").exists(),
                         "memória nasceu dentro de .git/modules")

    def test_recreated_repo_path_does_not_capture_the_worktree(self):
        base = self.project("iso-auth-recreated-")
        orig, side = base / "orig", base / "side"
        self._repo(orig)
        self._git(orig, "worktree", "add", "-q", str(side), "-b", "s")
        shutil.rmtree(str(orig))
        novo = self._repo(orig)   # MESMO path, repositório diferente

        text, err = self.server(side).call(
            "cortex_remember", {"type": "fact", "text": "da-worktree-orfa"})
        self.assertFalse(err, text)
        self.assertFalse((novo / ".cortex").exists(),
                         "worktree órfã contaminou o repositório novo")
        self.assertTrue((side / ".cortex").exists())


class EnvironmentWarningsReachEveryTool(IsolationCase):
    """Rodada 2 (codex item 3): o aviso só ia no briefing, mas o protocolo
    recomenda o briefing — não o impõe. Um agente que chame remember de
    primeira não via nada."""

    def test_install_dir_refuses_writes_and_creates_nothing(self):
        srv = self.server(SERVER.parent)
        text, err = srv.call("cortex_remember", {"type": "fact", "text": "x"})
        self.assertTrue(err, "gravar no diretório de instalação tem que "
                             "ser recusado: some no próximo update")
        self.assertIn("CORTEX_DIR", text)
        self.assertFalse((SERVER.parent / ".cortex").exists(),
                         "nada pode nascer no diretório de instalação")


class StrandedMemoryFoundFromSubdirectory(IsolationCase):
    """Rodada 2 (codex item 2A): lançar de `wt/packages/web` não via a
    memória órfã em `wt/.cortex` — e lançar de subdiretório é o caso normal."""

    def test_warning_survives_launch_from_a_subdirectory(self):
        repo = self.project("iso-stranded-sub-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "f.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "antiga"
        run("git", "worktree", "add", "-q", str(wt), "-b", "antiga")
        (wt / ".cortex").mkdir()
        (wt / ".cortex" / "cortex.db").write_bytes(b"SQLite format 3\x00")
        sub = wt / "packages" / "web"
        sub.mkdir(parents=True)

        text, _ = self.server(sub).call("cortex_briefing", {})
        self.assertIn(str(wt / ".cortex"), text,
                      "memória órfã invisível ao lançar de subdiretório")


class ConcurrentWritesAreActuallyConcurrent(IsolationCase):
    """Rodada 2 (codex item 6): o teste anterior era sequencial — cada
    chamada bloqueava até responder, então zero pares simultâneos. E
    conferia 2 de 24 entradas."""

    def test_simultaneous_writes_from_both_roots_all_land(self):
        repo = self.project("iso-realconc-")
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=str(repo), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (repo / "f.txt").write_text("x")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        wt = repo / ".claude" / "worktrees" / "par"
        run("git", "worktree", "add", "-q", str(wt), "-b", "par")

        srv_main, srv_wt = self.server(repo), self.server(wt)
        rodadas, erros = 10, []
        barreira = threading.Barrier(2)

        def escreve(srv, tag):
            for i in range(rodadas):
                barreira.wait(timeout=30)      # dispara os dois no mesmo instante
                try:
                    text, err = srv.call(
                        "cortex_remember",
                        {"type": "fact", "text": "%s-%d" % (tag, i)})
                    if err:
                        erros.append("%s-%d: %s" % (tag, i, text))
                except Exception as exc:       # noqa: BLE001
                    erros.append("%s-%d: %r" % (tag, i, exc))

        threads = [threading.Thread(target=escreve, args=(s, t))
                   for s, t in ((srv_main, "main"), (srv_wt, "wt"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(erros, [], "escritas simultâneas recusadas")
        text, _ = srv_main.call("cortex_recall", {"limit": 50})
        faltando = [n for tag in ("main", "wt") for n in
                    ["%s-%d" % (tag, i) for i in range(rodadas)]
                    if n not in text]
        self.assertEqual(faltando, [],
                         "entradas perdidas no banco compartilhado: %s"
                         % faltando)


if __name__ == "__main__":
    unittest.main()
