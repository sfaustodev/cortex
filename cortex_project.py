"""Identidade da tarefa e carimbo de dono (SPEC.md D13-D15).

A memória é escopada pelo diretório do projeto. Quem decide qual é esse
diretório é o PROCESSO — env do humano e cwd do lançamento — nunca o
agente: nenhuma tool aceita parâmetro de projeto, então gravar no lugar
errado é inexprimível. Este módulo não conhece SQLite; o CortexStore não
conhece projetos.
"""
import json
import os
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
        # O humano escolheu explicitamente; a guarda não se aplica (é o
        # próprio remédio que o erro da guarda ensina). O dono continua valendo.
        return OK, start, _canon(configured) / MEMORY_DIRNAME

    home = _home()
    root = start
    current = start
    while True:
        if home is not None and current == home:
            break
        if (current / MEMORY_DIRNAME).exists() or (current / ".git").exists():
            root = current
            break
        if current.parent == current:
            break
        current = current.parent

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
