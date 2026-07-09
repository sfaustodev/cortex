# HUMAN.md — cortex-oss

## Open questions

### Q-01 · Nome público do repo · raised 2026-07-09 · context: release v1.0.0
**Categoria:** ambiguidade resolvida sozinho
**O que assumi:** repo = `cortex` sob `sfaustodev` (o ecossistema tem `cortex-claude` no PyPI e `cortex-mcp` no npm, mas nome de repo GitHub é por conta — sem conflito técnico).
**Onde isso vive:** README.md (URLs), SPRINT.md
**Pergunta:** mantém `cortex` ou prefere nome mais distintivo?

### Q-02 · Pasta local do repo novo · raised 2026-07-09
**O que assumi:** `~/Projects/cortex-oss` (a arena já ocupa `~/Projects/cortex`).
**Pergunta:** mantém ou renomeia?

### Q-03 · Edições em docs/DEMO.md · raised 2026-07-09
**Categoria:** decisão persistente
**O que assumi:** 2 ajustes de caminho (arena → repo/`~/.cortex-mcp`); resto intacto, inclusive a nota honesta sobre o 401 do smoke test headless.
**Pergunta:** ok manter a nota do 401 num doc público?

### Q-04 · INSTALL.md da baia não copiado · raised 2026-07-09
**Categoria:** ambiguidade resolvida sozinho
**O que assumi:** o README absorve a instalação; dois docs de install competindo confundem. O INSTALL.md original permanece na arena.
**Pergunta:** mantém fora ou copio para docs/ como referência histórica?

### Q-05 · Titular da LICENSE · raised 2026-07-09
**O que assumi:** "Copyright (c) 2026 sfaustodev".
**Pergunta:** quer nome civil no lugar/junto?

### Q-06 · Arquivos sagrados visíveis no repo público · raised 2026-07-09
**Categoria:** decisão persistente
**O que assumi:** SPRINT/DIARY/HUMAN na raiz, públicos — a disciplina auditável faz parte do charme (e do método). Alternativa: movê-los para `.discipline/` ou mantê-los só localmente (gitignore).
**Pergunta:** públicos na raiz, escondidos, ou só locais?

## Resolved

_(vazio — repo recém-nascido)_
