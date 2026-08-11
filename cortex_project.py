"""Identidade da tarefa e carimbo de dono (SPEC.md D13-D15).

A memória é escopada pelo diretório do projeto. Quem decide qual é esse
diretório é o PROCESSO — env do humano e cwd do lançamento — nunca o
agente: nenhuma tool aceita parâmetro de projeto, então gravar no lugar
errado é inexprimível. Este módulo não conhece SQLite; o CortexStore não
conhece projetos.
"""
import json
import os
import re
import time
from pathlib import Path

OWNER_FILE = "OWNER"
MEMORY_DIRNAME = ".cortex"

OK = "ok"
NO_PROJECT = "no-project"
MISMATCH = "mismatch"


def _canon(path):
    """realpath tolerante: caminho inexistente volta como veio — um OWNER
    pode apontar para uma raiz que já não existe e a comparação ainda
    precisa acontecer."""
    try:
        return Path(path).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return Path(path)


def _main_worktree_root(git_path):
    """Numa worktree vinculada, `.git` é um ARQUIVO com `gitdir: <main>/.git/
    worktrees/<nome>`. Devolve a raiz do repositório PRINCIPAL, ou None se
    isto não for uma worktree.

    Existe porque worktree é descartável — ferramenta de agente cria e remove
    as dela o tempo todo — e uma memória que mora lá dentro morre junto com a
    tarefa que ela deveria preservar.

    Submódulo tem `.git`-arquivo IGUAL, mas aponta para `modules/`: é um
    repositório com identidade própria e fica com a memória dele. Distinguir
    os dois pelo NOME de um componente do caminho não funciona (um submódulo
    em `worktrees/lib` bate qualquer heurística de nome) — a autenticação é
    pelo metadado do git, abaixo.
    """
    try:
        if not git_path.is_file():
            return None
        content = git_path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return None
    if not content.startswith("gitdir:"):
        return None
    gitdir = Path(content.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (git_path.parent / gitdir)

    # Autenticação pelo metadado do git, não pelo NOME de um componente do
    # caminho: nome é escolhido por quem monta o repositório, e um submódulo
    # em `worktrees/lib` já bateu esse padrão. Só linked worktree tem
    # `commondir`; e o `gitdir` de lá aponta de volta para o arquivo que
    # estamos lendo, o que prova que este admin dir é DESTA worktree — um
    # path de repositório reutilizado por outro `git init` não bate.
    commondir_file, backlink = gitdir / "commondir", gitdir / "gitdir"
    if not (commondir_file.is_file() and backlink.is_file()):
        return None
    try:
        if _canon(backlink.read_text(encoding="utf-8").strip()) != _canon(git_path):
            return None
        common = _canon(gitdir / commondir_file.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return None
    if not common.is_dir():
        return None
    if common.name == ".git":
        return _canon(common.parent)
    return _root_from_config(common)


def _root_from_config(common):
    """Common dir fora do layout `<projeto>/.git` — repositório bare ou criado
    com `--separate-git-dir`. Quem sabe onde fica a árvore de trabalho é o
    config do próprio git.

    Sem resposta clara, devolve None de propósito: manter a memória na
    worktree é degradação; apontá-la para o lugar errado é corrupção.
    """
    try:
        config = (common / "config").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    if re.search(r"^\s*bare\s*=\s*true\s*$", config, re.M | re.I):
        return common          # bare não tem árvore de trabalho; o dir é durável
    found = re.search(r"^\s*worktree\s*=\s*(.+?)\s*$", config, re.M)
    if not found:
        return None
    root = _canon(common / found.group(1))
    return root if root.is_dir() else None


def _home():
    try:
        raw = Path.home()
    except Exception:  # noqa: BLE001
        return None
    # HOME vazio faz Path.home() cair no cwd e transformaria todo projeto em
    # "sem projeto": sem home utilizável, a fronteira simplesmente não existe
    return _canon(raw) if str(raw) not in ("", ".") else None


def resolve(env=None, cwd=None):
    """Devolve (mode, project_root, memory_dir).

    Ordem: CORTEX_DIR (escape explícito do humano) → subida a partir do cwd
    até a primeira raiz plausível (`.cortex/` ou `.git/`), parando ANTES de
    $HOME → o próprio cwd. Se o resultado for $HOME ou `/`, não há projeto:
    servir ali seria uma memória global compartilhada por tudo.
    """
    env = os.environ if env is None else env
    try:
        start = _canon(cwd if cwd is not None else os.getcwd())
    except Exception as exc:  # noqa: BLE001  (cwd deletado)
        raise RuntimeError("não consegui resolver o diretório atual (%s)"
                           % exc)

    configured = env.get("CORTEX_DIR")
    if configured:
        # O humano escolheu explicitamente; a guarda de $HOME não se aplica —
        # é o próprio remédio que o erro dela ensina.
        #
        # A identidade É o diretório apontado, NUNCA o cwd. Carimbar o cwd
        # faria a memória pertencer a quem a abriu primeiro: com CORTEX_DIR
        # fixo e cwd variável — precisamente o caso que o README recomenda
        # para hosts que lançam de lugar imprevisível — o segundo lançamento
        # viraria mismatch e a memória ficaria somente-leitura para sempre.
        pinned = _canon(configured)
        return OK, pinned, pinned / MEMORY_DIRNAME

    home = _home()

    # PRIMEIRA passada: procura a marca de worktree em toda a cadeia, antes
    # de olhar para qualquer `.cortex/`. Um resíduo deixado por uma versão
    # com o bug não pode virar a causa da resolução seguinte — em nenhuma
    # profundidade. Testar nível a nível deixava um `.cortex` num
    # subdiretório da worktree vencer o desvio, e o defeito sobrevivia ao
    # próprio conserto uma camada abaixo.
    for current in _upwards(start, home):
        main_root = _main_worktree_root(current / ".git")
        if main_root is not None:
            return _classify(main_root, home)

    # SEGUNDA: a raiz plausível mais próxima.
    root = start
    for current in _upwards(start, home):
        if (current / MEMORY_DIRNAME).exists() or (current / ".git").exists():
            root = current
            break
    return _classify(root, home)


def _upwards(start, home):
    """Do diretório até a fronteira: `$HOME` (exclusivo) ou a raiz do FS."""
    current = start
    while True:
        if home is not None and current == home:
            return
        yield current
        if current.parent == current:
            return
        current = current.parent


def _classify(root, home):
    """`$HOME` ou `/` como raiz não é projeto: servir ali seria uma memória
    global compartilhada por todas as tarefas."""
    if str(root) == os.sep or (home is not None and root == home):
        return NO_PROJECT, root, None
    return OK, root, root / MEMORY_DIRNAME


def ensure_born(memory_dir, project_root):
    """Chamada no boot. Carimba apenas quando NÃO há dono — diretório novo,
    ou memória criada por uma versão anterior. Nunca sobrescreve um carimbo
    existente: é ele que denuncia uma pasta copiada de outro projeto.
    """
    if _owner_root(memory_dir) is None:
        stamp(memory_dir, project_root)


def stranded_memory(cwd, project_root):
    """Banco deixado num diretório que já não é a raiz — tipicamente
    `<worktree>/.cortex` de antes do desvio para o repositório.

    Ignorar em silêncio seria repetir a falha original: o humano abriria um
    briefing vazio sem saber que o histórico está a dois diretórios dali.

    Varre do cwd até (sem incluir) a raiz resolvida, porque lançar de um
    subdiretório da worktree é o caso normal, não a exceção.
    """
    start, root = _canon(cwd), _canon(project_root)
    current = start
    while True:
        if current == root:
            return None
        db = current / MEMORY_DIRNAME / "cortex.db"
        if db.is_file():
            return db
        if current.parent == current:
            return None
        current = current.parent


def is_install_dir(project_root):
    """A raiz resolvida é o diretório onde o próprio servidor está instalado?

    Sem `CORTEX_DIR`, a raiz vem do cwd do lançamento. Um host que lance de
    dentro do diretório de instalação faria a memória nascer ali — e o cache
    de plugin é recriado a cada update, então ela sumiria sem aviso.
    """
    return _canon(project_root) == _canon(Path(__file__).resolve().parent)


def stamp(memory_dir, project_root):
    """Carimbo e .gitignore nascem COM o diretório, antes do banco — um
    briefing numa tarefa nova já materializa arquivo, e ele não pode nascer
    desprotegido nem versionável."""
    memory_dir = Path(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)
    gitignore = memory_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    (memory_dir / OWNER_FILE).write_text(
        json.dumps({"root": str(project_root),
                    "stamped": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime())}) + "\n",
        encoding="utf-8")


def _owner_root(memory_dir):
    path = Path(memory_dir) / OWNER_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("root")
    except Exception:  # noqa: BLE001  (OWNER ilegível = sem dono legível)
        return None
    if not isinstance(raw, str) or not raw:
        return None
    return _canon(raw)


def check_owner(memory_dir, project_root, env=None):
    """(status, owner). MISMATCH ⇒ somente-leitura até adoção explícita.

    Carimbo ausente é ADOTADO em vez de recusado: toda memória criada antes
    desta versão é assim, e um `.cortex/` que está na sua própria raiz não é
    cópia — é onde deveria estar. A proteção passa a valer para tudo que
    nasce daqui em diante.
    """
    env = os.environ if env is None else env
    memory_dir = Path(memory_dir)
    if not memory_dir.exists():
        return OK, None

    owner = _owner_root(memory_dir)
    if owner is None:
        stamp(memory_dir, project_root)
        return OK, None
    if owner == _canon(project_root):
        return OK, owner

    adopt = env.get("CORTEX_ADOPT")
    # Adoção vinculada ao valor: um CORTEX_ADOPT esquecido num .mcp.json só
    # re-adota daquele dono específico, nunca de um mismatch novo.
    if adopt and _canon(adopt) == owner:
        stamp(memory_dir, project_root)
        return OK, _canon(project_root)
    return MISMATCH, owner
