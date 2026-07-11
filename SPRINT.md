# SPRINT — cortex v1.0.0: lançamento open source

**Objetivo:** publicar o cortex como repo público plug-and-play em
`github.com/sfaustodev/cortex`, com instalação de uma colada só.

## Regras duras (hard rules)

1. `cortex_server.py`, `cortex_store.py` e `tests/` são a baia campeã da
   arena — **nenhuma mudança de comportamento** sem TDD (teste vermelho
   antes do código) e suíte 100% verde antes do commit.
2. **Nada sobe para o GitHub sem OK explícito do Fausto.** Preparar ≠ publicar.
3. Qualquer pasta/arquivo/skill com "CFO" no nome é sagrado e intocável.
4. A arena (`~/Projects/cortex`) permanece local — material P2P, cortex-reports
   e demo-admiracao **nunca** entram no repo público.
5. Instalação pública = 1 paste. Complexidade adicional exige justificativa
   registrada em HUMAN.md.
6. Zero dependências é feature de projeto, não acidente.

## Tarefas ordenadas

- [x] Extrair baia campeã (byte a byte) para `~/Projects/cortex-oss`
- [x] Suíte verde na casa nova (162 testes, py3.9.6)
- [x] README EN + seção PT-BR (instalação 1-paste + seção "To the AI")
- [x] LICENSE (MIT) · .gitignore · CLAUDE.md · CI (matrix 3.9–3.13, ubuntu+macos)
- [x] Ajuste de caminhos em docs/DEMO.md (2 linhas)
- [x] Scaffold discipline (este arquivo + DIARY + HUMAN)
- [x] Revisão do Fausto — "pronto aprovado" (2026-07-10)
- [x] `gh repo create sfaustodev/cortex --public` + push — publicado em 2026-07-10
- [x] Tag `v1.0.0` + push — CI matrix disparado no push (badge acende ao concluir)
- [x] CI red triage (Case A) → fix cirúrgico 72763c3 → matriz 10/10 verde, badge aceso
