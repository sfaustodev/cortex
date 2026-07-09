# DEMO — recuperação de informação perdida

O cenário do enunciado, executado DE VERDADE contra o servidor real:
a **sessão A** registra o estado da tarefa e o processo é morto com
`SIGKILL` (sem shutdown gracioso — simulando fim de sessão/crash). A
**sessão B** é um processo novo, com contexto zero, que recupera tudo
com UMA chamada (`cortex_briefing`) e fecha a pendência aberta pela
sessão A com `supersedes`.

Reproduza em qualquer máquina com Python 3.9+:

```bash
python3 demo_cortex.py   # from the repo root
```

## Transcript real (saída do comando acima)

```text
=== diretório da tarefa: /var/folders/.../T/tmpnxiqvyj0 ===

——— SESSÃO A (trabalhando na tarefa) ———

┌─ [sessão A] cortex_remember({"type": "constraint", "text": "A API Flurbo só aceita paginação por cursor — page/offset retornam 400.", "tags": ["flurbo", "api"]})
│  Registrado #1 (constraint).
└─
┌─ [sessão A] cortex_remember({"type": "decision", "text": "Retry com backoff exponencial (base 500ms, máx 3 tentativas), porque a API derruba rajadas acima de 3 req/s.", "tags": ["flurbo", "retry"]})
│  Registrado #2 (decision).
└─
┌─ [sessão A] cortex_remember({"type": "question", "text": "Qual header de autenticação a API Flurbo espera? (docs ambíguas)", "tags": ["flurbo", "auth"]})
│  Registrado #3 (question).
└─
┌─ [sessão A] cortex_remember({"type": "progress", "text": "Cliente HTTP esboçado; paginação por cursor funcionando no endpoint /items."})
│  Registrado #4 (progress).
└─
💀 [sessão A] processo morto com SIGKILL — nada de shutdown gracioso

——— SESSÃO B (processo novo, contexto ZERO) ———

┌─ [sessão B] cortex_briefing({})
│  # córtex — /private/var/folders/.../tmpnxiqvyj0/.cortex/cortex.db · 4 entradas · 1 sessões · busca: FTS5
│
│  ## constraints
│  - #1 A API Flurbo só aceita paginação por cursor — page/offset retornam 400.
│
│  ## decisions
│  - #2 Retry com backoff exponencial (base 500ms, máx 3 tentativas), porque a API derruba rajadas acima de 3 req/s.
│
│  ## questions
│  - #3 Qual header de autenticação a API Flurbo espera? (docs ambíguas)
│
│  ## progress
│  - #4 Cliente HTTP esboçado; paginação por cursor funcionando no endpoint /items.
│
│  _Detalhe e histórico: cortex_recall (query/type/tags/include_superseded) · registre com cortex_remember (supersedes para revisar)._
└─
┌─ [sessão B] cortex_recall({"query": "backoff"})
│  #2 [decision] (active · 2026-07-02T21:47:32.031471+00:00 · s=8125b301)
│    Retry com backoff exponencial (base 500ms, máx 3 tentativas), porque a API derruba rajadas acima de 3 req/s.
│    tags: flurbo, retry
└─
┌─ [sessão B] cortex_remember({"type": "fact", "text": "Auth da Flurbo confirmada: header X-Flurbo-Key (testado contra staging).", "tags": ["flurbo", "auth"], "supersedes": 3})
│  Registrado #5 (fact).
│  Substituiu #3 (question): «Qual header de autenticação a API Flurbo espera? (docs ambíguas)»
└─
┌─ [sessão B] cortex_briefing({})
│  # córtex — /private/var/folders/.../tmpnxiqvyj0/.cortex/cortex.db · 5 entradas · 2 sessões · busca: FTS5
│
│  ## constraints
│  - #1 A API Flurbo só aceita paginação por cursor — page/offset retornam 400.
│
│  ## decisions
│  - #2 Retry com backoff exponencial (base 500ms, máx 3 tentativas), porque a API derruba rajadas acima de 3 req/s.
│
│  ## progress
│  - #4 Cliente HTTP esboçado; paginação por cursor funcionando no endpoint /items.
│
│  ## facts
│  - #5 Auth da Flurbo confirmada: header X-Flurbo-Key (testado contra staging).
│
│  _Detalhe e histórico: cortex_recall (query/type/tags/include_superseded) · registre com cortex_remember (supersedes para revisar)._
└─
💀 [sessão B] processo morto com SIGKILL — nada de shutdown gracioso
```

## O que o transcript demonstra, ponto a ponto

1. **Recuperação pós-morte de processo** (critério de sucesso do
   enunciado): a sessão B nasceu sem NENHUM contexto e recuperou
   constraint, decisão (com o porquê), pendência e progresso em uma
   única chamada. O header do briefing prova a travessia: `2 sessões`.
2. **Anti-contradição**: a pendência #3 foi fechada com
   `supersedes: 3` — o segundo briefing não mostra mais a question
   (nem seção `## questions`), e o fato #5 assumiu o lugar. O
   histórico continua acessível (`include_superseded: true`).
3. **Durabilidade real**: os processos morrem com SIGKILL e nada se
   perde — cada `remember` é commitado (WAL) antes de responder.
4. **Proveniência**: o recall mostra `s=8125b301` — a sessão que gravou
   — e o briefing mostra o caminho do db em uso na primeira linha.

O mesmo cenário roda automatizado na suite
(`tests/test_protocol.py::TestPersistencia`), com asserções.

## Smoke test contra o Claude Code real

Nesta máquina de build a sessão headless aninhada não autentica
(`claude -p` → 401 dentro de outra sessão), então o teste abaixo fica
documentado para execução direta pelo avaliador — são 2 comandos:

```bash
TASK_DIR=$(mktemp -d)
CFG=$(mktemp).json
cat > "$CFG" <<EOF
{"mcpServers": {"cortex": {"command": "python3",
  "args": ["$HOME/.cortex-mcp/cortex_server.py"],
  "env": {"CORTEX_DIR": "$TASK_DIR"}}}}
EOF

# SESSÃO A — registra o estado da tarefa
claude -p "Tarefa: cliente da API Flurbo. Você descobriu: (1) a API só \
aceita paginação por cursor (page/offset dão 400); (2) decisão: retry \
com backoff exponencial porque a API derruba rajadas; (3) pendência: \
qual header de auth usar. Preserve o estado da tarefa como seu \
protocolo mandar e encerre." \
  --mcp-config "$CFG" --strict-mcp-config \
  --allowedTools "mcp__cortex__cortex_briefing,mcp__cortex__cortex_remember,mcp__cortex__cortex_recall"

# SESSÃO B — processo/contexto novos; NÃO menciona o córtex
claude -p "Você é uma sessão nova nesta tarefa; o contexto anterior foi \
perdido. Retome: (a) que paginação a API Flurbo aceita? (b) qual foi a \
decisão sobre retries e por quê? (c) o que ficou pendente?" \
  --mcp-config "$CFG" --strict-mcp-config \
  --allowedTools "mcp__cortex__cortex_briefing,mcp__cortex__cortex_remember,mcp__cortex__cortex_recall"
```

Resultado esperado (e critério de aprovação): a sessão B chama
`cortex_briefing` espontaneamente — induzida pelas `instructions` do
handshake e pela descrição da tool, sem que o prompt cite o córtex — e
responde (a) cursor, (b) backoff exponencial por causa das rajadas,
(c) o header de auth pendente.
