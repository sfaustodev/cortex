#!/usr/bin/env python3
"""Demo executável do córtex — o cenário do enunciado, com processos reais.

Sessão A registra estado da tarefa e MORRE (SIGKILL, sem shutdown).
Sessão B é um processo novo (contexto zero) que recupera tudo via
cortex_briefing e fecha a pendência com supersedes.

Uso: python3 demo_cortex.py [diretório-da-tarefa]
(default: diretório temporário descartável)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "cortex_server.py"


class Session:
    def __init__(self, name, task_dir):
        self.name = name
        env = os.environ.copy()
        env["CORTEX_DIR"] = str(task_dir)
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True,
        )
        self._id = 0
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "demo", "version": "0"},
        })
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params):
        self._id += 1
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method,
             "params": params}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def call(self, tool, args=None):
        resp = self._rpc("tools/call", {"name": tool,
                                        "arguments": args or {}})
        text = resp["result"]["content"][0]["text"]
        print("┌─ [%s] %s(%s)" % (self.name, tool,
                                  json.dumps(args or {}, ensure_ascii=False)))
        for line in text.splitlines():
            print("│  " + line)
        print("└─")
        return text

    def kill(self):
        self.proc.kill()
        self.proc.wait()
        print("💀 [%s] processo morto com SIGKILL — nada de shutdown "
              "gracioso\n" % self.name)


def main():
    if len(sys.argv) > 1:
        task_dir = Path(sys.argv[1])
        task_dir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        tmp = tempfile.TemporaryDirectory()
        task_dir = Path(tmp.name)
        cleanup = tmp
    print("=== diretório da tarefa: %s ===\n" % task_dir)

    print("——— SESSÃO A (trabalhando na tarefa) ———\n")
    a = Session("sessão A", task_dir)
    a.call("cortex_remember", {
        "type": "constraint",
        "text": "A API Flurbo só aceita paginação por cursor — page/offset "
                "retornam 400.",
        "tags": ["flurbo", "api"],
    })
    a.call("cortex_remember", {
        "type": "decision",
        "text": "Retry com backoff exponencial (base 500ms, máx 3 "
                "tentativas), porque a API derruba rajadas acima de 3 req/s.",
        "tags": ["flurbo", "retry"],
    })
    a.call("cortex_remember", {
        "type": "question",
        "text": "Qual header de autenticação a API Flurbo espera? "
                "(docs ambíguas)",
        "tags": ["flurbo", "auth"],
    })
    a.call("cortex_remember", {
        "type": "progress",
        "text": "Cliente HTTP esboçado; paginação por cursor funcionando "
                "no endpoint /items.",
    })
    a.kill()

    print("——— SESSÃO B (processo novo, contexto ZERO) ———\n")
    b = Session("sessão B", task_dir)
    b.call("cortex_briefing")
    b.call("cortex_recall", {"query": "backoff"})
    b.call("cortex_remember", {
        "type": "fact",
        "text": "Auth da Flurbo confirmada: header X-Flurbo-Key (testado "
                "contra staging).",
        "tags": ["flurbo", "auth"],
        "supersedes": 3,
    })
    b.call("cortex_briefing")
    b.kill()

    if cleanup:
        cleanup.cleanup()


if __name__ == "__main__":
    main()
