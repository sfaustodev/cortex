# DIARY — cortex-oss

## 2026-07-11 — CI red: triagem (Case A) e fix
**Done:**
- Primeira run do CI: vermelha nas 10 pernas. Protocolo ci-red-triage acionado → Case A (commit ee3b1a8, autoria desta sessão).
- Causa-raiz: teste de budget constrói caminho de ~562 chars; SQLite de fábrica impõe MAX_PATHNAME=512 — o SQLite da Apple (3.51.0, máquina local) tolera, por isso nunca acusou na arena.
- Reprodução E validação do fix em Linux com SQLite stock (container) ANTES de propor; fix aprovado pelo Fausto ("tem minha benção").
- Fix cirúrgico 72763c3: range(8)→range(5) apenas na linha 547 — os outros dois range(8) do arquivo criam ENTRADAS (semântica diferente) e ficaram intocados.
- **CI verde: 10/10 pernas em 36s. Badge aceso.**
**Lesson:**
- sed global quase alterou 2 testes saudáveis — para edição pontual em arquivo de teste com padrões repetidos: âncora única + assert count==1, sempre.
**Next session should start with:** publicar os carinhos da Mycorrhiza (commit local 2c3d7a1 aguarda OK do Fausto).

## 2026-07-10 — publicação
**Tickets touched:** —
**Done:**
- "Pronto aprovado" recebido do Fausto — aprovação em bloco do repo como montado.
- `gh repo create sfaustodev/cortex --public --source . --push` executado: https://github.com/sfaustodev/cortex no ar, branch main, visibilidade PUBLIC.
- Topics adicionados: mcp, mcp-server, claude-code, memory, ai-agents, sqlite, python.
- HUMAN.md: Q-01..Q-06 resolvidas em bloco (aprovação cobre os defaults assumidos).
- Tag `v1.0.0` anotada e enviada; CI matrix (3.9–3.13 × ubuntu/macos) disparado pelo push.
**In flight:**
- Badge de CI: aguarda conclusão da primeira run.
**Blocked:** —
**Files changed:** SPRINT.md, DIARY.md, HUMAN.md (registro pós-publicação).
**Next session should start with:** conferir badge verde; se Fausto topar, mover material P2P da arena para o repo p2p-desk; próximo do catálogo: revisão de instalação do Mycorrhiza-MCP.

## 2026-07-09 — nascimento do repo público
**Tickets touched:** —
**Done:**
- Catálogo de `~/Projects/` concluído; diagnóstico da arena (`~/Projects/cortex`): repo local-only, sem remote, contendo o experimento de 3 baias.
- Campeão identificado via histórico de commits: **ultracode** (Python 3.9+, stdlib pura, ~1.000 linhas).
- Suíte rodada na baia e na cópia: **162 testes, 0 falhas, 2 skips** (py3.9.6 local).
- Sweep de privacidade na baia: limpo (nenhum segredo, caminho pessoal, nome ou referência a NaaS/P2P — os hits de grep eram a palavra "token" em código de tokenização).
- Repo `~/Projects/cortex-oss` montado: código byte a byte + README (EN + PT-BR, com seção "To the AI reading this" e instalação 1-paste que também faz upgrade) + LICENSE MIT + .gitignore + CLAUDE.md + CI matrix (3.9–3.13 × ubuntu/macos) + docs/SPEC.md + docs/DEMO.md (2 caminhos ajustados) + scaffold discipline.
- `gh` autenticado como `sfaustodev` — publicação a um comando de distância.
**In flight:**
- Aguardando revisão do Fausto e o "publica" explícito.
**Blocked:**
- Publicação (regra dura nº 2 do SPRINT) — depende do OK.
**Files changed:** todos (repo novo).
**Next session should start with:** ler HUMAN.md (Q-01..Q-06); com o OK, rodar `gh repo create` + push + tag v1.0.0 e conferir o badge.
