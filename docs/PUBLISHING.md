# Publicando o cortex

Dois destinos, nesta ordem obrigatória: **PyPI primeiro, MCP Registry
depois**. O registry não hospeda código — ele aponta para um pacote que
já existe, e valida essa posse buscando o projeto no PyPI e procurando
a linha `mcp-name: io.github.sfaustodev/cortex` no README. Publicar o
verbete antes do pacote resulta em 404 na validação.

## Por que PyPI, se o servidor roda do clone

O registry só baixa de registros de pacote reais — `npm`, `pypi`,
`nuget`, `cargo`, `oci`, `mcpb`. **Não existe tipo "git"/"source"**:
qualquer outro valor é rejeitado no publish com `unsupported registry
type`. Um servidor que só existe como clone não entra no registry.

O nome no PyPI é `cortex-mcp-server` — `cortex`, `cortex-mcp` e
`cortex-memory-mcp` já estão ocupados por terceiros. **Nome no PyPI é
first-come e irreversível**: confira antes de reservar.

## Zero a publicado

### 1. PyPI

Preferir *trusted publishing* (OIDC, sem token no repositório):

1. Em <https://pypi.org/manage/account/publishing/>, registrar um
   publisher pendente — projeto `cortex-mcp-server`, dono
   `sfaustodev`, repositório `cortex`, workflow `publish.yml`,
   environment `pypi`.
2. Criar o environment `pypi` em Settings → Environments do repo.
3. Publicar uma tag: `git tag v1.0.0 && git push origin v1.0.0`.

O workflow `.github/workflows/publish.yml` faz o resto: constrói,
publica no PyPI e em seguida no registry.

Manualmente, se preferir:

```bash
uv build
uv publish            # pede o token do PyPI
```

### 2. MCP Registry

O workflow também cobre este passo. Manualmente:

```bash
brew install mcp-publisher       # ou baixar o release do GitHub
mcp-publisher login github       # device flow; trava o namespace io.github.sfaustodev/*
mcp-publisher publish            # lê o ./server.json
```

Validar sem publicar, a qualquer momento:

```bash
curl -s -X POST https://registry.modelcontextprotocol.io/v0.1/validate \
  -H 'Content-Type: application/json' -d @server.json
```

## Depois de publicado

Acrescentar ao README o canal que passa a existir — hoje ele não está
lá, de propósito, porque não funcionaria:

```bash
claude mcp add cortex -- uvx cortex-mcp-server
```

## O que não dá para desfazer

- **Versão publicada é imutável**, nos dois destinos. Metadado errado
  se corrige publicando outra versão; não se edita.
- Despublicar no registry só marca `deleted` — some da listagem, o
  registro histórico permanece.
- O registry está em **preview declarado** ("breaking changes or data
  resets may occur"). Não construa nada crítico sobre o verbete.

## Antes de qualquer publicação

```bash
python3 -m unittest discover -s tests     # inclui a trava de versão
claude plugin validate . --strict
```

A trava de versão existe porque agora são **quatro** arquivos
declarando a mesma coisa: `.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json`, `pyproject.toml` e `server.json`
(server e package). Divergir entre eles depois de publicar é o erro que
não tem volta.
