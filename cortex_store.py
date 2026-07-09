"""Armazenamento durável do córtex — SQLite (WAL) + FTS5 com fallback LIKE.

Contrato completo no SPEC.md (§3, §4, D4-D6, D11-D12). Python 3.9+,
stdlib apenas. Toda escrita é BEGIN IMMEDIATE…COMMIT e só retorna
depois do COMMIT — durabilidade contra morte de processo.
"""
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

VALID_TYPES = ("decision", "constraint", "fact", "lesson", "progress", "question")

_TEXT_MAX = 2000
_SIMILARITY_THRESHOLD = 0.5
_BUDGET_MIN, _BUDGET_MAX, _BUDGET_DEFAULT = 500, 20000, 6000
_RECALL_DEFAULT, _RECALL_MAX = 10, 50
_STALE_QUESTION_SESSIONS = 2
_PROGRESS_IN_BRIEFING = 5
_STUB_CHARS = 60
# espaço garantido pra seção "fora do orçamento": header da seção (~24)
# + ~2 stubs (~160) + linha-resumo (~50) — reserve menor deixa as seções
# cheias engolirem o espaço e nenhum stub caber
_TAIL_RESERVE = 250

_SECTION_ORDER = (
    ("constraints", "constraint"),
    ("decisions", "decision"),
    ("questions", "question"),
    ("progress", "progress"),
    ("lessons", "lesson"),
    ("facts", "fact"),
)


class StoreError(Exception):
    """Erro de domínio/validação — vira resultado isError na camada MCP."""


def _sim_tokens(text):
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) >= 3}


def _query_tokens(text):
    return re.findall(r"\w+", text or "")


def _like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _short(text, n):
    return text if len(text) <= n else text[: n] + "…"


def type_name(value):
    # o parâmetro `type` das tools sombreia o builtin dentro dos métodos
    return value.__class__.__name__


class CortexStore:
    def __init__(self, db_path, force_like=False, session=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session = session or uuid.uuid4().hex[:8]
        self.conn = sqlite3.connect(str(self.db_path), timeout=10,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        # dois processos iniciando juntos podem colidir na conversão pra
        # WAL (deadlock de upgrade NÃO invoca o busy handler) — o setup é
        # idempotente, então basta re-tentar
        last_exc = None
        for _ in range(40):
            try:
                self._setup(force_like)
                break
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                last_exc = exc
                time.sleep(0.05)
        else:
            raise last_exc

    def _setup(self, force_like):
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            "  id         INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts         TEXT    NOT NULL,"
            "  session    TEXT    NOT NULL,"
            "  type       TEXT    NOT NULL,"
            "  text       TEXT    NOT NULL,"
            "  tags       TEXT    NOT NULL DEFAULT '[]',"
            "  status     TEXT    NOT NULL DEFAULT 'active',"
            "  supersedes INTEGER"
            ")"
        )
        self.fts_enabled = False
        if not force_like:
            try:
                self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS"
                                  " temp.fts_probe USING fts5(x)")
                self.conn.execute("DROP TABLE temp.fts_probe")
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False
        if self.fts_enabled:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts "
                "USING fts5(text, content='entries', content_rowid='id')")
            n_entries = self.conn.execute(
                "SELECT count(*) FROM entries").fetchone()[0]
            try:
                # count(*) direto na virtual table external-content lê a
                # tabela de CONTEÚDO (bate sempre); o tamanho real do
                # índice vive na shadow table docsize
                n_fts = self.conn.execute(
                    "SELECT count(*) FROM entries_fts_docsize").fetchone()[0]
            except sqlite3.Error:
                n_fts = -1  # shadow indisponível: reconstrói por segurança
            if n_entries != n_fts:
                # índice defasado (ex.: escritas feitas num runtime sem FTS5)
                self.conn.execute(
                    "INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------------
    # escrita

    @staticmethod
    def _norm_tags(tags):
        if tags is None:
            return []
        if isinstance(tags, str) or not isinstance(tags, (list, tuple)):
            raise StoreError(
                'tags deve ser uma LISTA de strings — ex.: ["api"]')
        out = []
        for t in tags:
            # aspas/backslash sairiam do matching exato via JSON (D-recall)
            t = str(t).strip().lower().replace('"', "").replace("\\", "")
            if t and t not in out:
                out.append(t)
        return out

    @staticmethod
    def _norm_supersedes(supersedes):
        if supersedes is None:
            return None
        if isinstance(supersedes, bool):
            raise StoreError(
                "supersedes deve ser o id numérico de uma entrada")
        if isinstance(supersedes, float) and not supersedes.is_integer():
            raise StoreError(
                "supersedes deve ser o id INTEIRO de uma entrada")
        try:
            return int(supersedes)
        except (TypeError, ValueError):
            raise StoreError("supersedes deve ser o id numérico de uma entrada")

    def remember(self, type, text, tags=None, supersedes=None):
        if type not in VALID_TYPES:
            raise StoreError(
                "tipo inválido: %r — use um de: %s"
                % (type, ", ".join(VALID_TYPES)))
        if text is not None and not isinstance(text, str):
            raise StoreError(
                "text deve ser uma string (recebi %s)" % type_name(text))
        text = (text or "").strip()
        if not text:
            raise StoreError("texto vazio — nada para registrar")
        if len(text) > _TEXT_MAX:
            raise StoreError(
                "texto com %d chars excede o máximo de %d — resuma"
                % (len(text), _TEXT_MAX))
        norm_tags = self._norm_tags(tags)
        supersedes = self._norm_supersedes(supersedes)

        ts = datetime.now(timezone.utc).isoformat()
        warnings, superseded_echo, similar = [], None, None
        auto_superseded = []

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if supersedes is not None:
                old = self.conn.execute(
                    "SELECT * FROM entries WHERE id=?", (supersedes,)).fetchone()
                if old is None:
                    raise StoreError(
                        "entrada #%d não existe — nada foi substituído"
                        % supersedes)
                if old["status"] != "active":
                    head = self._chain_head(supersedes)
                    if head is None:
                        raise StoreError(
                            "entrada #%d já foi substituída (arquivamento em "
                            "lote de checkpoints) e a cadeia não tem sucessor "
                            "ativo — registre sem supersedes ou escolha uma "
                            "entrada ativa via cortex_recall" % supersedes)
                    raise StoreError(
                        "entrada #%d já foi substituída por #%d; "
                        "use supersedes=%d" % (supersedes, head, head))
                if old["type"] != type and old["type"] != "question":
                    warnings.append(
                        "tipo divergente: substituindo %s #%d por %s — "
                        "confirme que é intencional"
                        % (old["type"], supersedes, type))
                superseded_echo = {
                    "id": old["id"], "type": old["type"], "text": old["text"],
                }
            else:
                similar = self._find_similar(type, text)

            cur = self.conn.execute(
                "INSERT INTO entries (ts, session, type, text, tags, status,"
                " supersedes) VALUES (?,?,?,?,?,'active',?)",
                (ts, self.session, type, text,
                 json.dumps(norm_tags, ensure_ascii=False), supersedes))
            new_id = cur.lastrowid

            if supersedes is not None:
                self.conn.execute(
                    "UPDATE entries SET status='superseded' WHERE id=?",
                    (supersedes,))
            elif type == "progress":
                # checkpoint é estado corrente: auto-supersede intra-sessão (D12)
                prev = [r["id"] for r in self.conn.execute(
                    "SELECT id FROM entries WHERE type='progress' AND"
                    " status='active' AND session=? AND id<>? ORDER BY id",
                    (self.session, new_id))]
                if prev:
                    self.conn.executemany(
                        "UPDATE entries SET status='superseded' WHERE id=?",
                        [(i,) for i in prev])
                    self.conn.execute(
                        "UPDATE entries SET supersedes=? WHERE id=?",
                        (prev[-1], new_id))
                    auto_superseded = prev

            if self.fts_enabled:
                self.conn.execute(
                    "INSERT INTO entries_fts(rowid, text) VALUES (?,?)",
                    (new_id, text))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

        if similar is not None and similar["id"] in auto_superseded:
            # o aviso apontaria uma entrada que ESTA chamada acabou de
            # arquivar — seguir a instrução falharia
            similar = None
        return {"id": new_id, "type": type, "ts": ts,
                "superseded": superseded_echo, "warnings": warnings,
                "similar": similar}

    def _chain_head(self, entry_id):
        """Sucessor ATIVO da cadeia, ou None se a cadeia termina sem um
        (órfã de arquivamento em lote)."""
        current = entry_id
        for _ in range(100000):
            nxt = self.conn.execute(
                "SELECT id, status FROM entries WHERE supersedes=?"
                " ORDER BY id DESC LIMIT 1", (current,)).fetchone()
            if nxt is None:
                return None
            if nxt["status"] == "active":
                return nxt["id"]
            current = nxt["id"]
        return None

    def _find_similar(self, type, text):
        new_tokens = _sim_tokens(text)
        if not new_tokens:
            return None
        best = None
        rows = self.conn.execute(
            "SELECT id, text FROM entries WHERE type=? AND status='active'",
            (type,))
        for row in rows:
            tokens = _sim_tokens(row["text"])
            if not tokens:
                continue
            score = len(new_tokens & tokens) / len(new_tokens | tokens)
            if score >= _SIMILARITY_THRESHOLD and (
                    best is None or score > best[0]):
                best = (score, row["id"], row["text"])
        if best is None:
            return None
        return {"id": best[1], "text": best[2], "score": round(best[0], 2)}

    # ------------------------------------------------------------------
    # leitura

    def recall(self, query=None, type=None, tags=None, limit=_RECALL_DEFAULT,
               include_superseded=False):
        if type is not None and type not in VALID_TYPES:
            raise StoreError(
                "tipo inválido: %r — use um de: %s"
                % (type, ", ".join(VALID_TYPES)))
        if query is not None and not isinstance(query, str):
            raise StoreError(
                "query deve ser uma string (recebi %s)" % type_name(query))
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise StoreError("limit deve ser um inteiro")
        limit = max(1, min(_RECALL_MAX, limit))

        where, params = [], []
        if not include_superseded:
            where.append("e.status='active'")
        if type is not None:
            where.append("e.type=?")
            params.append(type)
        for tag in self._norm_tags(tags):
            # match exato: as aspas do JSON delimitam o token (D-recall)
            where.append("e.tags LIKE ? ESCAPE '\\'")
            params.append('%"' + _like_escape(tag) + '"%')

        tokens = _query_tokens(query)
        if tokens and self.fts_enabled:
            # sanitização: cada token vira string literal — sintaxe FTS
            # (aspas, parênteses, NOT/OR, *, ^, -) nunca chega ao MATCH
            match = " ".join('"%s"' % t.replace('"', '""') for t in tokens)
            sql = ("SELECT e.* FROM entries_fts JOIN entries e"
                   " ON e.id = entries_fts.rowid WHERE entries_fts MATCH ?")
            for w in where:
                sql += " AND " + w
            sql += " ORDER BY bm25(entries_fts), e.id DESC LIMIT ?"
            rows = self.conn.execute(sql, [match] + params + [limit]).fetchall()
        else:
            sql = "SELECT e.* FROM entries e"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY e.id DESC"
            if tokens:
                # fallback LIKE: todos os tokens (AND), ordem = recência.
                # case-fold em Python — lower() do SQLite é ASCII-only e
                # deixaria 'CAFÉ' inbuscável
                rows = self.conn.execute(sql, params).fetchall()
                lowered = [t.lower() for t in tokens]
                rows = [r for r in rows
                        if all(t in r["text"].lower() for t in lowered)]
                rows = rows[:limit]
            else:
                sql += " LIMIT ?"
                rows = self.conn.execute(sql, params + [limit]).fetchall()
        return [self._row_dict(r) for r in rows]

    @staticmethod
    def _row_dict(r):
        return {
            "id": r["id"], "ts": r["ts"], "session": r["session"],
            "type": r["type"], "text": r["text"],
            "tags": json.loads(r["tags"]), "status": r["status"],
            "supersedes": r["supersedes"],
        }

    # ------------------------------------------------------------------
    # briefing (D5)

    def briefing(self, budget_chars=_BUDGET_DEFAULT):
        try:
            budget = int(budget_chars)
        except (TypeError, ValueError):
            raise StoreError("budget_chars deve ser um inteiro")
        budget = max(_BUDGET_MIN, min(_BUDGET_MAX, budget))

        rows = self.conn.execute(
            "SELECT * FROM entries ORDER BY id").fetchall()
        backend = "FTS5" if self.fts_enabled else "LIKE"
        if not rows:
            empty = (
                "# córtex — %s\n\n"
                "Memória vazia — nenhuma entrada registrada para esta tarefa"
                " ainda.\nRegistre decisões, restrições, lições, progresso e"
                " perguntas com cortex_remember;\nrecupere depois com"
                " cortex_briefing/cortex_recall." % self.db_path)
            return empty[:budget]

        sessions = {r["session"] for r in rows}
        header = ("# córtex — %s · %d entradas · %d sessões · busca: %s"
                  % (self.db_path, len(rows), len(sessions), backend))
        footer = ("_Detalhe e histórico: cortex_recall (query/type/tags/"
                  "include_superseded) · registre com cortex_remember"
                  " (supersedes para revisar)._")

        first_by_session = {}
        for r in rows:
            first_by_session.setdefault(r["session"], r["id"])

        def question_hint(r):
            newer = sum(1 for s, fid in first_by_session.items()
                        if s != r["session"] and fid > r["id"])
            if newer >= _STALE_QUESTION_SESSIONS:
                return " (antiga — confirme se ainda vale ou feche com supersedes)"
            return ""

        active = [r for r in rows if r["status"] == "active"]
        by_type = {}
        for r in active:
            by_type.setdefault(r["type"], []).append(r)

        sections = []
        for name, typ in _SECTION_ORDER:
            entries = by_type.get(typ, [])
            if typ in ("constraint", "question"):
                entries = sorted(entries, key=lambda r: r["id"])
            else:
                entries = sorted(entries, key=lambda r: -r["id"])
            rest = []
            if typ == "progress":
                rest = entries[_PROGRESS_IN_BRIEFING:]
                entries = entries[:_PROGRESS_IN_BRIEFING]
            sections.append((name, entries, rest))

        parts = [header]
        used = len(header)
        budget_eff = budget - (len(footer) + 2)
        budget_sections = budget_eff - _TAIL_RESERVE

        def try_add(lines, ceiling):
            cost = sum(len(l) + 1 for l in lines)
            if used + cost <= ceiling:
                parts.extend(lines)
                return cost
            return None

        # D5: o corte respeita prioridade — a partir do PRIMEIRO estouro,
        # tudo que resta (desta seção e das de prioridade menor) vira stub;
        # senão facts curtos entrariam inteiros enquanto uma constraint
        # longa ficaria invisível
        overflow, truncated = [], False
        for name, entries, rest in sections:
            pending = ["", "## " + name]
            for r in entries:
                if truncated:
                    overflow.append(r)
                    continue
                hint = question_hint(r) if name == "questions" else ""
                line = "- #%d %s%s" % (r["id"], r["text"], hint)
                lines = pending + [line] if pending else [line]
                cost = try_add(lines, budget_sections)
                if cost is None:
                    truncated = True
                    overflow.append(r)
                else:
                    used += cost
                    pending = []
            overflow.extend(rest)

        if overflow:
            counts = {}
            for r in overflow:
                counts[r["type"]] = counts.get(r["type"], 0) + 1
            worst_summary = "- +%d omitidas (cortex_recall type=%s)" % (
                len(overflow), "/".join(sorted(counts)))
            reserve = len(worst_summary) + 1
            section_lines = ["", "## fora do orçamento"]
            section_cost = sum(len(l) + 1 for l in section_lines)
            if used + section_cost + reserve <= budget_eff:
                parts.extend(section_lines)
                used += section_cost
                not_placed = []
                # stubs na ordem de prioridade; parar no primeiro que não
                # cabe (pular pro próximo favoreceria stub curto de fact
                # sobre stub longo de constraint)
                for idx, r in enumerate(overflow):
                    stub = "- #%d [%s] %s" % (
                        r["id"], r["type"], _short(r["text"], _STUB_CHARS))
                    if used + len(stub) + 1 + reserve <= budget_eff:
                        parts.append(stub)
                        used += len(stub) + 1
                    else:
                        not_placed = overflow[idx:]
                        break
                if not_placed:
                    counts = {}
                    for r in not_placed:
                        counts[r["type"]] = counts.get(r["type"], 0) + 1
                    summary = "- +%d omitidas (cortex_recall type=%s)" % (
                        len(not_placed), "/".join(sorted(counts)))
                    parts.append(summary)
                    used += len(summary) + 1
            else:
                mini = "(+%d entradas omitidas — cortex_recall)" % len(overflow)
                cost = try_add(["", mini], budget_eff)
                if cost is not None:
                    used += cost

        text = "\n".join(parts) + "\n\n" + footer
        return text[:budget]
