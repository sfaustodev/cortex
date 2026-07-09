# CLAUDE.md — cortex

MCP server de memória de trabalho durável. Duas regras de casa e um contrato.

## Comandos

- Testes: `python3 -m unittest discover -s tests` (162 testes, stdlib pura — sem pip, sem venv)
- Demo de ressurreição: `python3 demo_cortex.py`

## Regras de casa

1. `cortex_server.py` e `cortex_store.py` são a implementação campeã da arena
   (ver README § Origins). Mudança de comportamento exige TDD: teste vermelho
   ANTES do código, suíte verde antes do commit.
2. Zero dependências é um feature, não uma dívida. PRs que adicionam pip
   packages precisam de justificativa extraordinária.

## Se o cortex estiver instalado na SUA sessão

Chame `cortex_briefing` antes de planejar; `cortex_remember` na hora em que a
decisão/lição acontecer (com o porquê); `supersedes` para revisar em vez de
contradizer. O protocolo completo chega pelo handshake MCP.
