"""Testes de integração do cortex_server — protocolo MCP stdio REAL.

Cada teste fala JSON-RPC 2.0 newline-delimited com um subprocesso de
verdade via stdin/stdout. Contrato sob teste: SPEC.md D8, D12, §6, §7.
Escritos ANTES da implementação (TDD).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "cortex_server.py"
LATEST = "2025-06-18"


class Server:
    """Cliente mínimo do protocolo, com timeout em toda leitura."""

    def __init__(self, cortex_dir):
        env = os.environ.copy()
        env["CORTEX_DIR"] = str(cortex_dir)
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True,
        )
        self.stdout_lines = []
        self._next_id = 100

    def send_raw(self, raw):
        self.proc.stdin.write(raw + "\n")
        self.proc.stdin.flush()

    def send(self, obj):
        self.send_raw(json.dumps(obj))

    def read_line(self, timeout=15):
        box = {}

        def target():
            box["line"] = self.proc.stdout.readline()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout)
        if "line" not in box:
            self.kill()
            raise AssertionError("timeout esperando resposta do servidor")
        self.stdout_lines.append(box["line"])
        return box["line"]

    def read_msg(self, timeout=15):
        return json.loads(self.read_line(timeout))

    def request(self, method, params=None, req_id=None):
        rid = self._next_id if req_id is None else req_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        resp = self.read_msg()
        return resp

    def initialize(self, protocol_version=LATEST):
        resp = self.request("initialize", {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        }, req_id=0)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def call_tool(self, name, arguments=None, req_id=None):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request("tools/call", params, req_id=req_id)

    def tool_text(self, resp):
        return resp["result"]["content"][0]["text"]

    def kill(self):
        self.proc.kill()
        self.proc.wait(10)
        self._close_pipes()

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self._close_pipes()

    def _close_pipes(self):
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass


class ProtocolCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._servers = []

    def start(self):
        s = Server(self.dir)
        self._servers.append(s)
        self.addCleanup(s.kill)
        return s


class TestHandshake(ProtocolCase):
    def test_initialize_ecoa_versao_conhecida(self):
        s = self.start()
        resp = s.initialize("2025-03-26")
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")

    def test_initialize_versao_desconhecida_devolve_mais_recente(self):
        s = self.start()
        resp = s.initialize("1999-01-01")
        self.assertEqual(resp["result"]["protocolVersion"], LATEST)

    def test_initialize_campos_obrigatorios(self):
        s = self.start()
        result = s.initialize()["result"]
        self.assertIn("tools", result["capabilities"])
        self.assertIn("name", result["serverInfo"])
        self.assertIn("version", result["serverInfo"])
        self.assertTrue(result["instructions"].strip())
        self.assertIn("cortex_briefing", result["instructions"])

    def test_ping(self):
        s = self.start()
        s.initialize()
        resp = s.request("ping")
        self.assertEqual(resp["result"], {})


class TestToolsList(ProtocolCase):
    def test_exatamente_tres_tools_com_schemas(self):
        s = self.start()
        s.initialize()
        resp = s.request("tools/list")
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        self.assertEqual(
            set(tools),
            {"cortex_briefing", "cortex_remember", "cortex_recall"},
        )
        for t in tools.values():
            self.assertTrue(t["description"].strip())
            self.assertEqual(t["inputSchema"]["type"], "object")
        self.assertEqual(
            sorted(tools["cortex_remember"]["inputSchema"]["required"]),
            ["text", "type"],
        )


class TestToolFlow(ProtocolCase):
    def test_remember_recall_roundtrip(self):
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_remember", {
            "type": "decision",
            "text": "usar retry com backoff exponencial",
        })
        text = s.tool_text(resp)
        self.assertIn("#1", text)
        self.assertFalse(resp["result"].get("isError", False))
        resp = s.call_tool("cortex_recall", {"query": "backoff"})
        self.assertIn("retry com backoff", s.tool_text(resp))

    def test_arguments_ausente_briefing_funciona(self):
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_briefing")
        content = resp["result"]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIsInstance(content[0]["text"], str)

    def test_validacao_vira_isError_instrutivo(self):
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_remember", {"type": "banana", "text": "x"})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("decision", s.tool_text(resp))

    def test_tool_desconhecida_erro_32602(self):
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_esquecer", {})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_params_nao_dict_erro_32602(self):
        s = self.start()
        s.initialize()
        resp = s.request("tools/call", params="banana")
        self.assertEqual(resp["error"]["code"], -32602)

    def test_arguments_nao_dict_erro_32602(self):
        s = self.start()
        s.initialize()
        resp = s.request("tools/call", {"name": "cortex_briefing",
                                        "arguments": "banana"})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_argumento_de_tipo_errado_isError_instrutivo(self):
        """text numérico / tags string não podem virar 'erro interno' —
        o modelo precisa de mensagem para se autocorrigir (D8)."""
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_remember", {"type": "fact", "text": 123})
        self.assertTrue(resp["result"]["isError"])
        self.assertNotIn("interno", s.tool_text(resp).lower())
        resp = s.call_tool("cortex_remember", {
            "type": "fact", "text": "ok", "tags": "api"})
        self.assertTrue(resp["result"]["isError"])
        self.assertNotIn("interno", s.tool_text(resp).lower())
        resp = s.call_tool("cortex_recall", {"query": 123})
        self.assertTrue(resp["result"]["isError"])
        self.assertNotIn("interno", s.tool_text(resp).lower())

    def test_content_shape_literal(self):
        s = self.start()
        s.initialize()
        resp = s.call_tool("cortex_remember", {"type": "fact", "text": "shape ok"})
        result = resp["result"]
        self.assertIsInstance(result["content"], list)
        self.assertEqual(set(result["content"][0]), {"type", "text"})
        self.assertIn("isError", result)


class TestPersistencia(ProtocolCase):
    """O critério de sucesso do enunciado, como teste executável."""

    def test_kill_e_recuperacao_em_processo_novo(self):
        s1 = self.start()
        s1.initialize()
        s1.call_tool("cortex_remember", {
            "type": "constraint",
            "text": "API só aceita cursor-pagination",
        })
        s1.call_tool("cortex_remember", {
            "type": "decision",
            "text": "retry com backoff exponencial, porque a API derruba rajadas",
        })
        s1.kill()  # SIGKILL: sem shutdown gracioso

        s2 = self.start()
        s2.initialize()
        briefing = s2.tool_text(s2.call_tool("cortex_briefing"))
        self.assertIn("cursor-pagination", briefing)
        self.assertIn("backoff", briefing)

    def test_sessoes_distintas_registradas(self):
        s1 = self.start()
        s1.initialize()
        s1.call_tool("cortex_remember", {"type": "fact", "text": "da primeira sessao"})
        s1.kill()

        s2 = self.start()
        s2.initialize()
        s2.call_tool("cortex_remember", {"type": "fact", "text": "da segunda sessao"})
        recall = s2.tool_text(s2.call_tool("cortex_recall", {}))
        import re
        sessions = set(re.findall(r"s=([0-9a-f]+)", recall))
        self.assertEqual(len(sessions), 2, recall)


class TestRobustezProtocolo(ProtocolCase):
    def test_json_malformado_32700_e_sobrevive(self):
        s = self.start()
        s.initialize()
        s.send_raw("isto nao é json {{{")
        resp = s.read_msg()
        self.assertEqual(resp["error"]["code"], -32700)
        self.assertIsNone(resp["id"])
        self.assertEqual(s.request("ping")["result"], {})

    def test_request_sem_method_erro_32600(self):
        s = self.start()
        s.initialize()
        s.send({"jsonrpc": "2.0", "id": 9})
        resp = s.read_msg()
        self.assertEqual(resp["error"]["code"], -32600)
        self.assertEqual(resp["id"], 9)

    def test_batch_jsonrpc_respondido(self):
        """A revisão 2025-03-26 do MCP (anunciada como suportada) inclui
        batching JSON-RPC."""
        s = self.start()
        s.initialize()
        s.send_raw(json.dumps([
            {"jsonrpc": "2.0", "id": 71, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/inventada"},
            {"jsonrpc": "2.0", "id": 72, "method": "ping"},
        ]))
        resp = s.read_msg()
        self.assertIsInstance(resp, list)
        self.assertEqual({r["id"] for r in resp}, {71, 72})

    def test_metodo_desconhecido_32601_eco_exato_do_id(self):
        s = self.start()
        s.initialize()
        resp = s.request("metodo/inexistente", req_id="abc-123")
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["id"], "abc-123")
        resp = s.request("metodo/inexistente", req_id=7)
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["id"], 7)

    def test_notifications_nunca_respondidas(self):
        s = self.start()
        s.initialize()
        s.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                "params": {"requestId": 1}})
        s.send({"jsonrpc": "2.0", "method": "notifications/inventada"})
        # a PRIMEIRA linha após as notifications tem que ser a resposta
        # do ping — prova que nada foi emitido para as notifications
        s.send({"jsonrpc": "2.0", "id": 42, "method": "ping"})
        resp = s.read_msg()
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"], {})

    def test_stdout_100_por_cento_json(self):
        s = self.start()
        s.initialize()
        s.call_tool("cortex_remember", {"type": "fact", "text": "higiene"})
        s.call_tool("cortex_recall", {"query": "higiene"})
        s.call_tool("cortex_briefing")
        s.send_raw("lixo proposital")
        s.read_msg()  # resposta do -32700
        s.request("ping")
        for line in s.stdout_lines:
            self.assertTrue(line.strip(), "linha vazia no stdout")
            json.loads(line)  # qualquer não-JSON explode aqui


class TestRobustezAmbiente(ProtocolCase):
    def test_dir_nao_gravavel_responde_isError_em_vez_de_morrer(self):
        blocker_file = self.dir / "sou-um-arquivo"
        blocker_file.write_text("ocupa o caminho do CORTEX_DIR")
        s = Server(blocker_file)  # .cortex/ não pode nascer sob um arquivo
        self.addCleanup(s.kill)
        resp = s.initialize()
        self.assertIn("result", resp)  # protocolo continua de pé
        resp = s.call_tool("cortex_briefing")
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("CORTEX_DIR", s.tool_text(resp))

    def test_dois_processos_vivos_no_mesmo_db(self):
        """Duas sessões do Claude Code abertas no mesmo projeto (D6)."""
        s1 = self.start()
        s2 = self.start()
        s1.initialize()
        s2.initialize()
        s1.call_tool("cortex_remember", {"type": "fact", "text": "do processo um"})
        s2.call_tool("cortex_remember", {"type": "fact", "text": "do processo dois"})
        s1.call_tool("cortex_remember", {"type": "fact", "text": "de novo o um"})
        recall = s1.tool_text(s1.call_tool("cortex_recall", {"limit": 10}))
        self.assertIn("do processo um", recall)
        self.assertIn("do processo dois", recall)
        self.assertIn("de novo o um", recall)


class TestPressaoDeGravacaoD12(ProtocolCase):
    def test_rodape_apos_8_calls_sem_remember(self):
        s = self.start()
        s.initialize()
        s.call_tool("cortex_remember", {"type": "fact", "text": "zera contador"})
        for i in range(7):
            resp = s.call_tool("cortex_recall", {"query": "nada"})
        # 7ª chamada: ainda SEM rodapé (threshold é 8 — mata mutante)
        self.assertNotIn("desde a última gravação", s.tool_text(resp))
        # 8ª chamada desde o remember: rodapé presente
        resp = s.call_tool("cortex_recall", {"query": "nada"})
        self.assertIn("gravação", s.tool_text(resp))
        # e o caminho do briefing também carrega o rodapé (D12)
        resp = s.call_tool("cortex_briefing")
        self.assertIn("desde a última gravação", s.tool_text(resp))

    def test_remember_reseta_contador(self):
        s = self.start()
        s.initialize()
        for _ in range(8):
            s.call_tool("cortex_recall", {"query": "nada"})
        s.call_tool("cortex_remember", {"type": "fact", "text": "gravei agora"})
        resp = s.call_tool("cortex_recall", {"query": "nada"})
        self.assertNotIn("desde a última gravação", s.tool_text(resp))


if __name__ == "__main__":
    unittest.main()
