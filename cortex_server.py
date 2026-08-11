#!/usr/bin/env python3
"""córtex — servidor MCP (stdio) de memória de trabalho durável.

JSON-RPC 2.0 newline-delimited. Regras de ferro (SPEC.md D8):
stdout carrega EXCLUSIVAMENTE mensagens do protocolo (uma por linha,
flush após cada); todo diagnóstico vai para stderr; notification
(mensagem sem id) NUNCA é respondida. Python 3.9+, stdlib apenas.
"""
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cortex_project  # noqa: E402
from cortex_store import CortexStore, StoreError, VALID_TYPES  # noqa: E402

SERVER_NAME = "cortex"
SERVER_VERSION = "2.0.2"
SUPPORTED_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_VERSION = SUPPORTED_VERSIONS[-1]
NUDGE_THRESHOLD = 8

INSTRUCTIONS = """córtex é sua memória de trabalho durável desta tarefa — seu contexto é volátil, ela não.
Protocolo:
1. Ao iniciar ou retomar uma sessão, chame cortex_briefing ANTES de planejar ou editar.
2. Registre com cortex_remember NA HORA em que acontecer: decisão tomada (com o porquê), restrição descoberta, lição aprendida (bug corrigido, beco sem saída), marco de progresso, pergunta em aberto.
3. Antes de re-derivar algo que a tarefa já pode ter respondido, chame cortex_recall.
4. Decisão mudou? Grave a nova com supersedes=<id da antiga> — nunca confie na versão velha.
5. Prestes a escrever "vou assumir que…"? Registre a suposição.
6. Antes de declarar um marco concluído ao usuário, grave um progress.
7. Respondeu uma pendência? Feche a question com supersedes.
8. Ao encerrar a tarefa, grave um progress final e feche as questions — o próximo trabalho neste diretório herda esta memória."""

TOOLS = [
    {
        "name": "cortex_briefing",
        "description": (
            "Memória durável desta tarefa — seu contexto é volátil, ela não. "
            "Chame PRIMEIRO ao iniciar ou retomar qualquer sessão de "
            "trabalho, antes de planejar ou editar. Devolve restrições, "
            "decisões vigentes, pendências e progresso registrados por "
            "sessões anteriores desta mesma tarefa."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_chars": {
                    "type": "integer",
                    "description": "Orçamento do digest em chars "
                                   "(default 6000, clamp 500..20000).",
                },
            },
        },
    },
    {
        "name": "cortex_remember",
        "description": (
            "Registre NO MOMENTO em que acontecer: decisão tomada (com o "
            "porquê), restrição descoberta, lição aprendida (bug corrigido, "
            "beco sem saída), marco de progresso, pergunta em aberto, fato "
            "caro de re-derivar. Se está prestes a escrever 'vou assumir "
            "que…', registre a suposição. Decisão mudou? Regrave com "
            "supersedes. Fronteiras: constraint = regra imposta que você "
            "não pode violar; decision = escolha SUA que poderia ter sido "
            "outra (tem porquê); fact = observação verificada do mundo."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(VALID_TYPES),
                    "description": "Classe da entrada (ver fronteiras na "
                                   "descrição da tool).",
                },
                "text": {
                    "type": "string",
                    "description": "Conteúdo, 1..2000 chars. Decisões "
                                   "incluem o porquê.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Etiquetas para filtro exato no recall.",
                },
                "supersedes": {
                    "type": "integer",
                    "description": "Id da entrada que esta substitui "
                                   "(revisão de decisão, fechamento de "
                                   "question).",
                },
            },
            "required": ["type", "text"],
        },
    },
    {
        "name": "cortex_recall",
        "description": (
            "Antes de re-derivar algo que a tarefa já pode ter respondido — "
            "'qual porta?', 'por que escolhemos X?', 'esse bug já apareceu?' "
            "— busque aqui. Sem query, devolve as entradas mais recentes. "
            "Use include_superseded para ver o histórico de uma decisão."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Termos de busca full-text (todos "
                                   "precisam ocorrer).",
                },
                "type": {
                    "type": "string",
                    "enum": list(VALID_TYPES),
                    "description": "Filtra por classe de entrada.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filtro exato de etiquetas (AND).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de resultados (default 10, "
                                   "clamp 1..50).",
                },
                "include_superseded": {
                    "type": "boolean",
                    "description": "Inclui entradas substituídas "
                                   "(histórico).",
                },
            },
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _log(message):
    sys.stderr.write("[cortex] %s\n" % message)
    sys.stderr.flush()


def _send(obj):
    # json.dumps default: ensure_ascii=True e sem newlines internos —
    # uma mensagem por linha, sempre (D8)
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _short(text, n):
    return text if len(text) <= n else text[: n] + "…"


NO_PROJECT_MSG = (
    "córtex sem projeto: o diretório resolvido é %s, que serviria uma memória"
    " global compartilhada por todas as tarefas. Lance o servidor na raiz do"
    " projeto ou defina CORTEX_DIR apontando para ela.")

MISMATCH_MSG = (
    "esta memória pertence a %s — somente-leitura aqui. Se ela é legitimamente"
    " deste projeto (pasta movida ou renomeada), adote uma vez com"
    " CORTEX_ADOPT=%s.")

STRANDED_MSG = (
    "⚠ memória órfã em %s — deixada aqui por uma versão anterior, que gravava"
    " dentro da worktree. Ela NÃO está em uso e some quando a worktree for"
    " removida. Para recuperá-la, copie o BANCO (não a pasta, que carrega o"
    " carimbo de dono) sobre a memória atual enquanto ela ainda estiver vazia."
    " Com as duas populadas, exporte com `sqlite3 <origem> .dump` antes de"
    " decidir o que juntar.")

INSTALL_DIR_MSG = (
    "⚠ a raiz resolvida é o diretório de instalação do próprio córtex (%s)."
    " Uma memória aqui é apagada no próximo update. Lance o servidor a partir"
    " do projeto ou defina CORTEX_DIR.")


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": code, "message": message}}


class CortexServer:
    def __init__(self):
        self.store = None
        self.store_error = None
        self.calls_since_remember = 0
        self._base = None
        self._memory_dir = None
        self._no_project = None
        self._env_warnings = []
        try:
            mode, root, memory_dir = cortex_project.resolve()
        except Exception as exc:  # noqa: BLE001  (ex.: cwd deletado)
            self.store_error = (
                "córtex indisponível: não consegui resolver o diretório-base"
                " (%s). Configure CORTEX_DIR para um diretório do projeto."
                % exc)
            _log(self.store_error)
            return
        if mode == cortex_project.NO_PROJECT:
            # Fail-closed: servir aqui contradiria a única promessa que o
            # córtex faz — uma memória por tarefa.
            self._no_project = NO_PROJECT_MSG % root
            _log(self._no_project)
            return
        self._base = root
        self._memory_dir = memory_dir
        # Avisos de ambiente vão no BRIEFING, não só no stderr: aviso que o
        # agente não lê é o que deixou este servidor servir memória errada
        # por três versões.
        self._env_warnings = []
        if cortex_project.is_install_dir(root):
            self._env_warnings.append(INSTALL_DIR_MSG % root)
        stranded = cortex_project.stranded_memory(os.getcwd(), root)
        if stranded is not None:
            self._env_warnings.append(STRANDED_MSG % stranded)
        for warning in self._env_warnings:
            _log(warning)
        try:
            cortex_project.ensure_born(memory_dir, root)
        except Exception as exc:  # noqa: BLE001  (ex.: caminho ocupado por arquivo)
            # Não é sentença de morte: _ensure_store produz o erro acionável
            # e o protocolo segue de pé para poder respondê-lo.
            _log("não consegui carimbar %s (%s)" % (memory_dir, exc))
        self._ensure_store()

    def _ensure_store(self):
        """Abre (ou re-tenta abrir) o store; falha de startup não pode ser
        sentença de morte — ex.: lock transitório de dois processos
        iniciando juntos."""
        if self.store is not None:
            return True
        if self._base is None:
            return False
        try:
            self.store = CortexStore(self._memory_dir / "cortex.db")
            self.store_error = None
            _log("base aberta em %s (busca: %s, sessão %s)"
                 % (self.store.db_path,
                    "FTS5" if self.store.fts_enabled else "LIKE",
                    self.store.session))
            return True
        except Exception as exc:  # noqa: BLE001
            self.store_error = (
                "córtex indisponível: não consegui abrir a base em %s (%s). "
                "Configure CORTEX_DIR para um diretório gravável do projeto."
                % (self._base, exc))
            _log(self.store_error)
            return False

    # ------------------------------------------------------------------

    def handle(self, msg):
        """Devolve a resposta (dict) ou None (notification)."""
        if not isinstance(msg, dict):
            return _error(None, -32600, "Invalid Request")
        if "id" not in msg:
            return None  # notification: nunca responder (D8)
        rid = msg["id"]
        method = msg.get("method")
        if not isinstance(method, str):
            return _error(rid, -32600,
                          "Invalid Request: method ausente ou não-string")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(rid, -32602, "Invalid params: deve ser um objeto")

        if method == "initialize":
            client_version = params.get("protocolVersion")
            negotiated = (client_version if client_version in
                          SUPPORTED_VERSIONS else LATEST_VERSION)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME,
                               "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            return self._handle_tools_call(rid, params)
        return _error(rid, -32601, "Method not found: %s" % method)

    def _handle_tools_call(self, rid, params):
        name = params.get("name")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(rid, -32602,
                          "Invalid params: arguments deve ser um objeto")
        if name not in TOOL_NAMES:
            return _error(rid, -32602, "Unknown tool: %s" % name)
        if self._no_project is not None:
            return self._tool_text(rid, self._no_project, is_error=True)
        self.calls_since_remember += 1
        if not self._ensure_store():
            return self._tool_text(rid, self.store_error, is_error=True)
        # Dono re-verificado a cada chamada, não só no boot: a pasta pode ser
        # trocada por baixo de um processo vivo.
        owner_status, owner = cortex_project.check_owner(self._memory_dir,
                                                         self._base)
        read_only = owner_status == cortex_project.MISMATCH
        try:
            if name == "cortex_remember":
                if read_only:
                    return self._tool_text(
                        rid, "Gravação recusada: " + MISMATCH_MSG % (owner,
                                                                     owner),
                        is_error=True)
                result = self.store.remember(
                    args.get("type"), args.get("text"),
                    tags=args.get("tags"),
                    supersedes=args.get("supersedes"))
                self.calls_since_remember = 0
                text = self._format_remember(result)
            elif name == "cortex_recall":
                rows = self.store.recall(
                    query=args.get("query"), type=args.get("type"),
                    tags=args.get("tags"), limit=args.get("limit", 10),
                    include_superseded=bool(
                        args.get("include_superseded", False)))
                text = self._format_recall(rows) + self._nudge()
            else:  # cortex_briefing
                text = self.store.briefing(
                    budget_chars=args.get("budget_chars", 6000))
                text += self._nudge()
            if read_only:
                text = "⚠ " + MISMATCH_MSG % (owner, owner) + "\n\n" + text
            if name == "cortex_briefing" and self._env_warnings:
                text = "\n".join(self._env_warnings) + "\n\n" + text
            return self._tool_text(rid, text, is_error=False)
        except StoreError as exc:
            return self._tool_text(rid, "Erro: %s" % exc, is_error=True)
        except Exception:  # noqa: BLE001
            _log(traceback.format_exc())
            return self._tool_text(
                rid, "Erro interno do córtex — detalhes no stderr.",
                is_error=True)

    @staticmethod
    def _tool_text(rid, text, is_error):
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }}

    # ------------------------------------------------------------------

    @staticmethod
    def _format_remember(result):
        lines = ["Registrado #%d (%s)." % (result["id"], result["type"])]
        if result["superseded"]:
            old = result["superseded"]
            lines.append("Substituiu #%d (%s): «%s»"
                         % (old["id"], old["type"], _short(old["text"], 90)))
        for warning in result["warnings"]:
            lines.append("⚠ %s" % warning)
        if result["similar"]:
            sim = result["similar"]
            lines.append(
                "Atenção: parecida com #%d (active): «%s». Se substitui, "
                "regrave com supersedes=%d."
                % (sim["id"], _short(sim["text"], 90), sim["id"]))
        return "\n".join(lines)

    @staticmethod
    def _format_recall(rows):
        if not rows:
            return ("Nada encontrado. Tente outros termos, type=…, "
                    "ou include_superseded=true.")
        blocks = []
        for r in rows:
            head = "#%d [%s] (%s · %s · s=%s)" % (
                r["id"], r["type"], r["status"], r["ts"], r["session"])
            block = head + "\n  " + r["text"]
            if r["tags"]:
                block += "\n  tags: " + ", ".join(r["tags"])
            blocks.append(block)
        return "\n".join(blocks)

    def _nudge(self):
        if self.calls_since_remember >= NUDGE_THRESHOLD:
            return ("\n\n⚠ %d chamadas desde a última gravação — decisões e "
                    "lições não gravadas morrem na próxima compressão. Algo "
                    "a registrar? (cortex_remember)"
                    % self.calls_since_remember)
        return ""


def main():
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    server = CortexServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _send(_error(None, -32700, "Parse error"))
            continue
        try:
            if isinstance(msg, list):
                # batch JSON-RPC (revisão 2025-03-26 do MCP)
                if not msg:
                    _send(_error(None, -32600, "Invalid Request"))
                    continue
                responses = [r for r in (server.handle(m) for m in msg)
                             if r is not None]
                if responses:
                    _send(responses)
            else:
                response = server.handle(msg)
                if response is not None:
                    _send(response)
        except Exception:  # noqa: BLE001
            _log(traceback.format_exc())
            if isinstance(msg, dict) and "id" in msg:
                _send(_error(msg["id"], -32603, "Internal error"))


if __name__ == "__main__":
    main()
