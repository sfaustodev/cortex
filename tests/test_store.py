"""Testes de unidade do CortexStore — SQLite real em diretório temporário.

Contrato sob teste: SPEC.md §3, §4, D4, D5, D6, D11, D12.
Escritos ANTES da implementação (TDD).
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cortex_store import CortexStore, StoreError  # noqa: E402


class StoreCase(unittest.TestCase):
    """Base: um db limpo por teste, com helpers."""

    force_like = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "cortex.db"
        self.store = self.make_store()
        self.addCleanup(self.store.close)

    def make_store(self, session=None):
        s = CortexStore(self.db_path, force_like=self.force_like, session=session)
        return s


class TestRememberBasics(StoreCase):
    def test_ids_crescentes(self):
        r1 = self.store.remember("fact", "primeiro fato")
        r2 = self.store.remember("fact", "segundo fato")
        self.assertEqual(r1["id"], 1)
        self.assertEqual(r2["id"], 2)

    def test_persiste_apos_reabrir(self):
        self.store.remember("decision", "usar sqlite com wal")
        self.store.close()
        reopened = CortexStore(self.db_path, force_like=self.force_like)
        self.addCleanup(reopened.close)
        rows = reopened.recall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "usar sqlite com wal")

    def test_visivel_para_segunda_conexao_imediatamente(self):
        """Commit ANTES da resposta (meta 4): outra conexão vê na hora."""
        self.store.remember("fact", "durabilidade real")
        other = CortexStore(self.db_path, force_like=self.force_like)
        self.addCleanup(other.close)
        self.assertEqual(len(other.recall()), 1)

    def test_tipo_invalido_erro_lista_tipos_validos(self):
        with self.assertRaises(StoreError) as ctx:
            self.store.remember("banana", "texto")
        msg = str(ctx.exception)
        for t in ("decision", "constraint", "fact", "lesson", "progress", "question"):
            self.assertIn(t, msg)

    def test_texto_vazio_erro(self):
        with self.assertRaises(StoreError):
            self.store.remember("fact", "   ")

    def test_texto_acima_de_2000_erro(self):
        with self.assertRaises(StoreError):
            self.store.remember("fact", "x" * 2001)

    def test_texto_exatamente_2000_ok(self):
        r = self.store.remember("fact", "x" * 2000)
        self.assertEqual(r["id"], 1)

    def test_texto_nao_string_erro_instrutivo(self):
        with self.assertRaises(StoreError):
            self.store.remember("fact", 123)

    def test_tags_string_erro_instrutivo(self):
        """tags='api' iteraria char a char e corromperia a base (imutável)."""
        with self.assertRaises(StoreError):
            self.store.remember("fact", "texto ok", tags="api")

    def test_supersedes_bool_erro(self):
        self.store.remember("fact", "primeira entrada")
        with self.assertRaises(StoreError):
            self.store.remember("fact", "segunda", supersedes=True)

    def test_supersedes_float_nao_inteiro_erro(self):
        self.store.remember("fact", "primeira entrada")
        with self.assertRaises(StoreError):
            self.store.remember("fact", "segunda", supersedes=2.9)

    def test_tags_com_aspas_e_backslash_sanitizadas(self):
        """Aspas/backslash quebrariam o matching exato via JSON (D-recall)."""
        self.store.remember("fact", "com tag hostil", tags=['x"y', "a\\b"])
        rows = self.store.recall()
        self.assertEqual(rows[0]["tags"], ["xy", "ab"])
        self.assertEqual(len(self.store.recall(tags=['x"y'])), 1)
        self.assertEqual(len(self.store.recall(tags=["y"])), 0)

    def test_supersedes_inexistente_erro_cita_id(self):
        with self.assertRaises(StoreError) as ctx:
            self.store.remember("fact", "novo", supersedes=99)
        self.assertIn("99", str(ctx.exception))

    def test_tags_normalizadas_lowercase(self):
        self.store.remember("fact", "com tags", tags=["API", " Web "])
        rows = self.store.recall()
        self.assertEqual(rows[0]["tags"], ["api", "web"])

    def test_timestamp_utc_offset(self):
        self.store.remember("fact", "hora certa")
        ts = self.store.recall()[0]["ts"]
        self.assertTrue(ts.endswith("+00:00"), ts)


class TestSupersede(StoreCase):
    def test_antiga_vira_superseded_nova_active(self):
        old = self.store.remember("decision", "usar porta 8080")
        new = self.store.remember("decision", "usar porta 9090", supersedes=old["id"])
        actives = self.store.recall(type="decision")
        self.assertEqual([r["id"] for r in actives], [new["id"]])
        todos = self.store.recall(type="decision", include_superseded=True)
        by_id = {r["id"]: r for r in todos}
        self.assertEqual(by_id[old["id"]]["status"], "superseded")
        self.assertEqual(by_id[new["id"]]["status"], "active")
        self.assertEqual(by_id[new["id"]]["supersedes"], old["id"])

    def test_resposta_ecoa_entrada_substituida(self):
        old = self.store.remember("decision", "usar porta 8080")
        new = self.store.remember("decision", "usar porta 9090", supersedes=old["id"])
        self.assertIsNotNone(new["superseded"])
        self.assertEqual(new["superseded"]["id"], old["id"])
        self.assertIn("8080", new["superseded"]["text"])

    def test_supersede_de_ja_superseded_erro_cita_head(self):
        a = self.store.remember("decision", "v1 da decisao")
        b = self.store.remember("decision", "v2 da decisao", supersedes=a["id"])
        c = self.store.remember("decision", "v3 da decisao", supersedes=b["id"])
        with self.assertRaises(StoreError) as ctx:
            self.store.remember("decision", "v4 renegada", supersedes=a["id"])
        self.assertIn(str(c["id"]), str(ctx.exception))

    def test_nunca_duas_ativas_na_cadeia(self):
        a = self.store.remember("decision", "v1")
        b = self.store.remember("decision", "v2", supersedes=a["id"])
        chain = self.store.recall(type="decision", include_superseded=True)
        ativos = [r for r in chain if r["status"] == "active"]
        self.assertEqual([r["id"] for r in ativos], [b["id"]])

    def test_warning_tipo_divergente(self):
        old = self.store.remember("constraint", "nao usar rede externa")
        new = self.store.remember("progress", "terminei o modulo", supersedes=old["id"])
        self.assertTrue(any("tipo" in w.lower() for w in new["warnings"]), new["warnings"])

    def test_question_fechada_sem_warning_de_tipo(self):
        q = self.store.remember("question", "qual porta usar?")
        new = self.store.remember("decision", "porta 9090, conflito com 8080", supersedes=q["id"])
        self.assertFalse(any("tipo" in w.lower() for w in new["warnings"]), new["warnings"])

    def test_supersede_de_orfa_erro_honesto_sem_loop_morto(self):
        """Órfã de auto-supersede em lote não pode gerar 'use supersedes=N'
        apontando para ela mesma (loop morto)."""
        s = self.make_store(session="sess-x")
        self.addCleanup(s.close)
        q = s.remember("question", "qual formato de log usar?")
        s.remember("progress", "comecei o modulo de logs")           # 2
        s.remember("progress", "logs em json decidido e aplicado",   # 3
                   supersedes=q["id"])
        s.remember("progress", "modulo de logs concluido")           # 4
        # 2 virou superseded em lote, sem sucessor apontando pra ela
        with self.assertRaises(StoreError) as ctx:
            s.remember("progress", "ajuste no modulo de logs", supersedes=2)
        msg = str(ctx.exception)
        self.assertNotIn("use supersedes=2", msg)
        self.assertIn("recall", msg.lower())


class TestDuplicataD11(StoreCase):
    def test_texto_similar_mesmo_tipo_avisa_com_id(self):
        first = self.store.remember("decision", "usar porta 8080 para o servidor local")
        dup = self.store.remember("decision", "usar porta 8080 para servidor local de dev")
        self.assertIsNotNone(dup["similar"])
        self.assertEqual(dup["similar"]["id"], first["id"])

    def test_tipo_diferente_nao_avisa(self):
        self.store.remember("decision", "usar porta 8080 para o servidor local")
        dup = self.store.remember("fact", "usar porta 8080 para servidor local de dev")
        self.assertIsNone(dup["similar"])

    def test_dissimilar_nao_avisa(self):
        self.store.remember("decision", "migrar o banco para postgres na fase dois")
        other = self.store.remember("decision", "usar retry com backoff exponencial")
        self.assertIsNone(other["similar"])

    def test_supersede_explicito_nao_avisa(self):
        first = self.store.remember("decision", "usar porta 8080 para o servidor local")
        upd = self.store.remember(
            "decision", "usar porta 8080 para o servidor local sempre",
            supersedes=first["id"],
        )
        self.assertIsNone(upd["similar"])

    def test_similar_ignora_superseded(self):
        a = self.store.remember("decision", "usar porta 8080 para o servidor local")
        self.store.remember("decision", "usar porta 9090 no lugar", supersedes=a["id"])
        again = self.store.remember("decision", "usar porta 8080 para servidor local de dev")
        sim = again["similar"]
        if sim is not None:
            self.assertNotEqual(sim["id"], a["id"])

    def test_progress_similar_auto_superseded_nao_avisa(self):
        """O aviso não pode apontar entrada que ESTA chamada acabou de
        auto-supersedir — seguir a instrução falharia."""
        s = self.make_store(session="sess-a")
        self.addCleanup(s.close)
        s.remember("progress", "parser implementado e validado agora")
        dup = s.remember("progress", "parser implementado e validado hoje")
        self.assertIsNone(dup["similar"])


class TestProgressAutoSupersedeD12(StoreCase):
    def test_mesma_sessao_auto_supersede(self):
        s = self.make_store(session="sess-a")
        self.addCleanup(s.close)
        p1 = s.remember("progress", "parser implementado")
        p2 = s.remember("progress", "testes do parser verdes")
        ativos = s.recall(type="progress")
        self.assertEqual([r["id"] for r in ativos], [p2["id"]])
        todos = s.recall(type="progress", include_superseded=True)
        by_id = {r["id"]: r for r in todos}
        self.assertEqual(by_id[p1["id"]]["status"], "superseded")

    def test_sessoes_diferentes_preserva(self):
        s1 = self.make_store(session="sess-a")
        self.addCleanup(s1.close)
        s2 = self.make_store(session="sess-b")
        self.addCleanup(s2.close)
        s1.remember("progress", "marco da sessao a")
        s2.remember("progress", "marco da sessao b")
        ativos = s2.recall(type="progress")
        self.assertEqual(len(ativos), 2)

    def test_supersede_explicito_nao_dispara_auto(self):
        s = self.make_store(session="sess-a")
        self.addCleanup(s.close)
        p1 = s.remember("progress", "primeiro marco alcancado")
        p2 = s.remember("progress", "segundo marco alcancado")
        p3 = s.remember("progress", "correcao do segundo marco", supersedes=p2["id"])
        ativos = s.recall(type="progress")
        self.assertEqual([r["id"] for r in ativos], [p3["id"]])
        del p1


class TestRecall(StoreCase):
    def test_query_match_e_nao_match(self):
        self.store.remember("decision", "usar retry com backoff exponencial")
        self.store.remember("fact", "endpoint de staging exige header especial")
        hits = self.store.recall(query="backoff")
        self.assertEqual(len(hits), 1)
        self.assertIn("backoff", hits[0]["text"])
        self.assertEqual(self.store.recall(query="kubernetes"), [])

    def test_query_todos_os_tokens_and(self):
        self.store.remember("fact", "retry no cliente")
        self.store.remember("fact", "backoff no servidor")
        self.store.remember("fact", "retry com backoff no worker")
        hits = self.store.recall(query="retry backoff")
        self.assertEqual(len(hits), 1)
        self.assertIn("worker", hits[0]["text"])

    def test_query_caracteres_hostis_nao_explode(self):
        self.store.remember("lesson", "kill -9 no servidor perde nada")
        self.store.remember("decision", "usar retry (backoff) sempre")
        self.store.remember("fact", "don't panic escrito na capa")
        for q in ('don\'t', "retry (backoff)", "kill -9", '"aspas"',
                  "a NOT b OR c", "prefix*", "col:val", "^inicio", "-neg"):
            self.store.recall(query=q)  # não pode levantar exceção
        self.assertEqual(len(self.store.recall(query="retry (backoff)")), 1)
        self.assertEqual(len(self.store.recall(query="kill -9")), 1)

    def test_query_so_pontuacao_vira_sem_query(self):
        self.store.remember("fact", "alfa")
        self.store.remember("fact", "beta")
        hits = self.store.recall(query="()!!")
        self.assertEqual(len(hits), 2)

    def test_query_nao_string_erro_instrutivo(self):
        self.store.remember("fact", "alfa")
        with self.assertRaises(StoreError):
            self.store.recall(query=123)

    def test_recall_tags_string_erro_instrutivo(self):
        self.store.remember("fact", "alfa", tags=["api"])
        with self.assertRaises(StoreError):
            self.store.recall(tags="api")

    def test_query_case_unicode(self):
        """lower() do SQLite é ASCII-only; case-fold tem que ser correto
        também para não-ASCII, nos DOIS caminhos de busca."""
        self.store.remember("fact", "reuniao sobre o CAFÉ da manha")
        self.assertEqual(len(self.store.recall(query="café")), 1)

    def test_ordenacao_com_query_like_recencia(self):
        if self.store.fts_enabled:
            self.skipTest("ordenação por recência com query é contrato do caminho LIKE")
        self.store.remember("fact", "backoff no cliente antigo")
        self.store.remember("fact", "backoff no worker novo")
        hits = self.store.recall(query="backoff")
        self.assertEqual([h["text"] for h in hits],
                         ["backoff no worker novo", "backoff no cliente antigo"])

    def test_ordenacao_com_query_fts_bm25(self):
        if not self.store.fts_enabled:
            self.skipTest("relevância bm25 é contrato do caminho FTS5")
        padding = " ".join("palavra%d" % i for i in range(120))
        self.store.remember("fact", "backoff " + padding)
        self.store.remember("fact", "backoff backoff backoff")
        hits = self.store.recall(query="backoff")
        self.assertIn("backoff backoff backoff", hits[0]["text"])

    def test_filtro_por_tipo(self):
        self.store.remember("decision", "escolha registrada")
        self.store.remember("fact", "observacao registrada")
        hits = self.store.recall(type="decision")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["type"], "decision")

    def test_filtro_tag_exato_sem_substring(self):
        self.store.remember("fact", "sobre a api", tags=["api"])
        self.store.remember("fact", "sobre rapidez", tags=["rapid"])
        self.store.remember("fact", "sobre api v2", tags=["api-v2"])
        hits = self.store.recall(tags=["api"])
        self.assertEqual(len(hits), 1)
        self.assertIn("sobre a api", hits[0]["text"])

    def test_filtro_tags_and(self):
        self.store.remember("fact", "so web", tags=["web"])
        self.store.remember("fact", "web e api juntas", tags=["web", "api"])
        hits = self.store.recall(tags=["web", "api"])
        self.assertEqual(len(hits), 1)
        self.assertIn("juntas", hits[0]["text"])

    def test_superseded_excluido_por_default(self):
        old = self.store.remember("decision", "usar porta 8080")
        self.store.remember("decision", "usar porta 9090", supersedes=old["id"])
        self.assertEqual(len(self.store.recall(query="porta")), 1)
        self.assertEqual(len(self.store.recall(query="porta", include_superseded=True)), 2)

    def test_sem_query_ordena_por_recencia(self):
        self.store.remember("fact", "mais antiga")
        self.store.remember("fact", "mais nova")
        hits = self.store.recall()
        self.assertEqual(hits[0]["text"], "mais nova")

    def test_limit_clamp(self):
        for i in range(60):
            self.store.remember("fact", "entrada numero %d" % i)
        self.assertEqual(len(self.store.recall(limit=500)), 50)
        self.assertEqual(len(self.store.recall(limit=0)), 1)
        self.assertEqual(len(self.store.recall(limit=5)), 5)


class TestConcorrencia(StoreCase):
    def test_dois_escritores_simultaneos(self):
        errors = []

        def writer(tag):
            try:
                s = CortexStore(self.db_path, force_like=self.force_like)
                for i in range(20):
                    s.remember("fact", "escritor %s item %d" % (tag, i))
                s.close()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.recall(limit=50)), 40)


class TestAtomicidade(StoreCase):
    """Meta 4 + D4: supersede é UMA transação. Falha no meio (aqui: o
    índice FTS some por baixo, via outra conexão) não pode deixar estado
    parcial — nem entrada nova órfã, nem antiga marcada superseded."""

    def test_falha_no_meio_do_supersede_faz_rollback_completo(self):
        import sqlite3
        if not self.store.fts_enabled:
            self.skipTest("caminho de falha usa a virtual table FTS")
        old = self.store.remember("decision", "usar porta 8080 sempre")
        saboteur = sqlite3.connect(str(self.db_path))
        saboteur.execute("DROP TABLE entries_fts")
        saboteur.commit()
        saboteur.close()
        with self.assertRaises(sqlite3.OperationalError):
            self.store.remember("decision", "usar porta 9090 no lugar",
                                supersedes=old["id"])
        reader = CortexStore(self.db_path, force_like=True)
        self.addCleanup(reader.close)
        rows = reader.recall(include_superseded=True)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], "active")


class TestBriefing(StoreCase):
    def test_linha_1_tem_caminho_do_db(self):
        self.store.remember("fact", "qualquer coisa")
        first_line = self.store.briefing().splitlines()[0]
        self.assertIn(str(self.db_path), first_line)

    def test_base_vazia_mensagem_de_arranque(self):
        text = self.store.briefing()
        self.assertIn(str(self.db_path), text)
        self.assertIn("cortex_remember", text)
        self.assertIn("vazia", text.lower())

    def test_mostra_ids(self):
        r = self.store.remember("decision", "usar sqlite")
        self.assertIn("#%d" % r["id"], self.store.briefing())

    def test_ordem_das_secoes(self):
        self.store.remember("fact", "um fato")
        self.store.remember("lesson", "uma licao")
        self.store.remember("progress", "um marco")
        self.store.remember("question", "uma pergunta?")
        self.store.remember("decision", "uma decisao")
        self.store.remember("constraint", "uma restricao")
        b = self.store.briefing()
        order = ["## constraints", "## decisions", "## questions",
                 "## progress", "## lessons", "## facts"]
        idx = [b.index(h) for h in order]
        self.assertEqual(idx, sorted(idx), b)

    def test_secao_vazia_omitida(self):
        self.store.remember("fact", "so um fato")
        b = self.store.briefing()
        self.assertNotIn("## decisions", b)

    def test_superseded_fora(self):
        old = self.store.remember("decision", "usar porta 8080")
        self.store.remember("decision", "usar porta 9090", supersedes=old["id"])
        b = self.store.briefing()
        self.assertNotIn("8080", b)
        self.assertIn("9090", b)

    def test_question_fechada_some(self):
        q = self.store.remember("question", "qual banco usar afinal?")
        self.store.remember("decision", "sqlite, zero deps", supersedes=q["id"])
        self.assertNotIn("qual banco usar afinal?", self.store.briefing())

    def test_intra_secao_decisions_recentes_primeiro(self):
        a = self.store.remember("decision", "decisao antiga sobre cache")
        b_ = self.store.remember("decision", "decisao nova sobre fila")
        b = self.store.briefing()
        self.assertLess(b.index("#%d" % b_["id"]), b.index("#%d" % a["id"]))

    def test_intra_secao_questions_antigas_primeiro(self):
        q1 = self.store.remember("question", "pergunta antiga sobre auth?")
        q2 = self.store.remember("question", "pergunta nova sobre deploy?")
        b = self.store.briefing()
        self.assertLess(b.index("#%d" % q1["id"]), b.index("#%d" % q2["id"]))

    def test_orcamento_respeitado_e_corta_facts_antes_de_constraints(self):
        for i in range(8):
            self.store.remember("constraint", ("restricao critica %d " % i) + "c" * 80)
        for i in range(30):
            self.store.remember("fact", ("fato longo e verboso %d " % i) + "f" * 150)
        budget = 1500
        b = self.store.briefing(budget_chars=budget)
        self.assertLessEqual(len(b), budget)
        self.assertIn("restricao critica 0", b)

    def test_cortados_viram_stubs_com_id(self):
        import re
        self.store.remember("constraint", "unica restricao pequena")
        for i in range(30):
            self.store.remember("fact", ("fato verboso %d " % i) + "f" * 150)
        b = self.store.briefing(budget_chars=2000)
        self.assertRegex(b, r"- #\d+ \[fact\] ",
                         "facts cortados têm que virar stubs '#id [fact] …'")

    def test_prioridade_no_corte_constraint_nunca_invisivel(self):
        """Regressão do review: constraint longa NÃO pode sumir enquanto
        facts triviais entram inteiros (D5)."""
        import re
        big = self.store.remember(
            "constraint", "REGRA CRITICA: nunca tocar producao. " + "c" * 800)
        for i in range(6):
            self.store.remember("fact", "fato trivial numero %d" % i)
        b = self.store.briefing(budget_chars=500)
        self.assertLessEqual(len(b), 500)
        self.assertIn("#%d" % big["id"], b,
                      "constraint cortada precisa aparecer ao menos como stub")
        first_fact_pos = re.search(r"- #\d+ (\[fact\] )?fato trivial", b)
        if first_fact_pos:
            self.assertLess(b.index("#%d" % big["id"]), first_fact_pos.start(),
                            "stub da constraint vem antes de qualquer fact")

    def test_corte_congela_secoes_de_prioridade_menor(self):
        """Depois que uma entrada estoura o orçamento, seções de prioridade
        menor não podem receber linhas INTEIRAS (só stubs)."""
        self.store.remember(
            "decision", "decisao longa e importante sobre arquitetura. " + "d" * 500)
        for i in range(8):
            self.store.remember("fact", "fato curto %d" % i)
        b = self.store.briefing(budget_chars=600)
        for line in b.splitlines():
            if "fato curto" in line:
                self.assertIn("[fact]", line,
                              "fact inteiro apareceu apesar do corte: %r" % line)

    def test_degenerado_constraints_estouram_orcamento(self):
        for i in range(20):
            self.store.remember("constraint", ("restricao %d " % i) + "c" * 200)
        budget = 800
        b = self.store.briefing(budget_chars=budget)
        self.assertLessEqual(len(b), budget)
        self.assertIn("omitida", b)

    def test_budget_clamp(self):
        self.store.remember("fact", "pequeno")
        self.assertLessEqual(len(self.store.briefing(budget_chars=10)), 500)
        b = self.store.briefing(budget_chars=10 ** 9)
        self.assertLessEqual(len(b), 20000)

    def test_conta_sessoes_distintas(self):
        s1 = self.make_store(session="sess-a")
        self.addCleanup(s1.close)
        s2 = self.make_store(session="sess-b")
        self.addCleanup(s2.close)
        s1.remember("fact", "da sessao a")
        s2.remember("fact", "da sessao b")
        s2.remember("fact", "outra da sessao b")
        header = s2.briefing().splitlines()[0]
        self.assertIn("3 entradas", header)
        self.assertIn("2 sessões", header)

    def test_briefing_vazio_respeita_budget(self):
        deep = Path(self._tmp.name)
        for i in range(8):
            deep = deep / ("subnivel-%d-%s" % (i, "x" * 55))
        s = CortexStore(deep / "cortex.db", force_like=self.force_like)
        self.addCleanup(s.close)
        b = s.briefing(budget_chars=500)
        self.assertLessEqual(len(b), 500)

    def test_question_antiga_ganha_hint(self):
        s1 = self.make_store(session="sess-1")
        self.addCleanup(s1.close)
        s1.remember("question", "pendencia esquecida ha eras?")
        for name in ("sess-2", "sess-3"):
            s = self.make_store(session=name)
            self.addCleanup(s.close)
            s.remember("fact", "atividade da " + name)
        b = s1.briefing()
        self.assertIn("antiga", b)

    def test_question_recente_sem_hint(self):
        self.store.remember("question", "pendencia fresquinha?")
        b = self.store.briefing()
        line = [ln for ln in b.splitlines() if "fresquinha" in ln][0]
        self.assertNotIn("antiga", line)

    def test_rodape_aponta_recall(self):
        self.store.remember("fact", "um fato")
        self.assertIn("cortex_recall", self.store.briefing())


class TestFtsFlag(StoreCase):
    def test_flag_de_backend_exposta(self):
        self.assertIsInstance(self.store.fts_enabled, bool)
        if self.force_like:
            self.assertFalse(self.store.fts_enabled)


# ---- Reexecução da suite inteira no caminho LIKE (fallback D6) ----

class TestRememberBasicsLike(TestRememberBasics):
    force_like = True


class TestSupersedeLike(TestSupersede):
    force_like = True


class TestDuplicataD11Like(TestDuplicataD11):
    force_like = True


class TestProgressAutoSupersedeD12Like(TestProgressAutoSupersedeD12):
    force_like = True


class TestRecallLike(TestRecall):
    force_like = True


class TestBriefingLike(TestBriefing):
    force_like = True


class TestFtsFlagLike(TestFtsFlag):
    force_like = True


class TestLikeAbreDbCriadoComFts(unittest.TestCase):
    """Db criado COM FTS5 precisa abrir num runtime SEM (D6)."""

    def test_abre_e_busca(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "cortex.db"
        s1 = CortexStore(db)  # provavelmente com FTS
        s1.remember("fact", "gravado com fts ligado")
        s1.close()
        s2 = CortexStore(db, force_like=True)
        hits = s2.recall(query="fts ligado")
        self.assertEqual(len(hits), 1)
        s2.remember("fact", "gravado no fallback")
        self.assertEqual(len(s2.recall(limit=10)), 2)
        s2.close()
        # reabrir COM FTS: índice defasado é reconstruído e acha a
        # entrada gravada no fallback (D6, rebuild por contagem)
        s3 = CortexStore(db)
        self.addCleanup(s3.close)
        if s3.fts_enabled:
            self.assertEqual(len(s3.recall(query="fallback")), 1)


if __name__ == "__main__":
    unittest.main()
