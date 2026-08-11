# SPEC — córtex (baia ultracode)

Servidor MCP de **memória de trabalho durável** para um agente de código
executando UMA tarefa longa. Sobrevive à compressão de contexto e a
reinícios de sessão; local-only; zero dependências externas.

> Este design passou por um painel adversarial de 3 revisores
> independentes (lentes: conformidade com o enunciado, ergonomia de
> agente LLM, arquitetura/protocolo) ANTES da implementação. O §10
> registra o que o painel mudou. Os testes foram escritos antes do
> código (evidência: histórico de commits — testes vermelhos commitados
> antes da implementação).

---

## 1. O problema em uma frase

O contexto do agente é RAM volátil: quando comprime ou reinicia, o
agente re-deriva o que já sabia, contradiz decisões antigas e reintroduz
bugs corrigidos. O córtex é o disco: um lugar barato de escrever durante
o trabalho e de ler de volta **em uma chamada** quando a RAM zera.

### Metas

1. **Rehidratação em 1 chamada**: sessão nova recupera o estado mental da
   tarefa com um único tool call (`cortex_briefing`).
2. **Escrita sem fricção**: registrar uma decisão custa um tool call com
   2 campos obrigatórios.
3. **Anti-contradição**: decisões revisadas substituem as antigas de
   forma explícita (supersede), nunca coexistem como verdades paralelas —
   e o sistema defende essa meta mesmo quando o agente ERRA (D11, D12).
4. **Durabilidade real**: kill -9 no servidor não perde nada que foi
   confirmado ao agente (commit ANTES da resposta; queda de energia está
   fora do escopo — a meta é morte de processo/sessão).

### Não-metas

- Memória entre PROJETOS diferentes (escopo real: um diretório/worktree
  por tarefa — trade-off assumido em D7).
- Busca semântica por embeddings (D9).
- Captura automática de contexto (hooks/transcripts) como REQUISITO —
  o core funciona com MCP puro; reforços opcionais de configuração do
  usuário são documentados no INSTALL.md (D1).

---

## 2. Decisões de design (numeradas, com justificativa)

### D1 — O comportamento do agente é induzido em camadas: descrições de tools (garantido) → instructions (provável) → reforços opcionais

**Problema real:** o requisito difícil não é armazenar — é fazer o agente
*lembrar de usar* a memória, sem modificar o Claude Code.

**Decisão:** três camadas, da mais garantida à opcional:

1. **Descrições de tools como gatilhos comportamentais** ("chame ISTO
   quando ACONTECER aquilo") — único canal que comprovadamente permanece
   no contexto do agente durante toda a sessão, inclusive após
   compressão. A descrição de `cortex_briefing` é redigida assumindo que
   ela é o único canal garantido.
2. **Campo `instructions` do `initialize`** — hosts MCP (Claude Code
   incluído) o injetam no contexto do sistema; carrega o protocolo de
   uso completo. Tratado como provável, não garantido.
3. **Reforços opcionais documentados no INSTALL.md** — uma linha no
   CLAUDE.md do projeto e/ou um hook `PreCompact`. *Configurar* o
   Claude Code (settings do usuário) não é *modificar* o Claude Code; o
   requisito 3 do enunciado proíbe o segundo. O core NÃO depende desses
   reforços — eles cobrem o momento pré-compactação, que nenhum
   mecanismo MCP puro alcança.

**Justificativa:** cada camada cobre a falha da anterior; nenhuma exige
mudança no host. O cenário degradado (host ignora instructions) mantém o
sistema funcional só com as descrições.

### D2 — Exatamente 3 tools

`cortex_briefing`, `cortex_remember`, `cortex_recall`. Nada mais.

**Justificativa:** cada tool extra é uma decisão a mais que o agente
precisa tomar sob pressão de contexto — e fricção de decisão é a
principal causa de memória não usada. Operações que pareceriam pedir
tools próprias são absorvidas:

- *editar/corrigir* → `cortex_remember` com `supersedes` (D4);
- *resolver pergunta aberta* → registrar a resposta (fato/decisão) com
  `supersedes` apontando para a pergunta;
- *checkpoint de progresso* → `cortex_remember` com `type=progress`.

### D3 — Entradas tipadas (6 tipos), não texto livre

`decision · constraint · fact · lesson · progress · question`

**Justificativa:** os tipos são a taxonomia mínima dos danos que a perda
de contexto causa:

| tipo | dano que evita |
|---|---|
| `decision` | contradizer decisão antiga |
| `constraint` | violar regra do projeto/usuário |
| `fact` | re-derivar o que já se sabia |
| `lesson` | reintroduzir bug já corrigido / repetir beco sem saída |
| `progress` | refazer trabalho já feito / não saber onde parou |
| `question` | esquecer pendência aberta |

Os tipos dão ao briefing uma ordem de prioridade objetiva (D5). A
fronteira decision/constraint/fact é ensinada na própria descrição da
tool (o erro de classificação tem custo assimétrico via D5):
*constraint = regra que você não pode violar (imposta de fora); decision
= escolha SUA que poderia ter sido outra (tem porquê); fact = observação
verificada do mundo.*

### D4 — Histórico imutável de CONTEÚDO + supersede (nunca editar/apagar texto)

Corrigir ou revisar = gravar entrada nova com `supersedes=<id>`. A
antiga muda `status` para `superseded` e permanece consultável com
`include_superseded`. Updates permitidos no esquema: APENAS `status`
(supersede) e o vínculo `supersedes` gravado pelo auto-supersede do D12
— texto, tipo e tags jamais mudam.

Regras de supersede:

- insert da nova + update da antiga ocorrem **na mesma transação**
  (crash não pode deixar duas verdades ativas);
- supersedir entrada JÁ superseded → erro instrutivo apontando o head
  ativo da cadeia ("#12 já foi substituída por #30; use supersedes=30");
  se a cadeia terminar sem sucessor ativo (órfã de arquivamento em lote
  do D12), o erro é honesto e aponta o `cortex_recall` — nunca uma
  instrução auto-referente impossível de seguir;
- supersede entre tipos diferentes é permitido (question→fact é o fluxo
  natural de fechar pendência); a entrada nova é classificada pelo tipo
  DELA; tipos divergentes fora do caso `question→*` geram **warning** no
  resultado (supersedir constraint com progress é quase sempre engano);
- a resposta ECOA a entrada substituída ("Registrado #34, substituiu
  #12: «usar porta 8080…»") — o agente detecta id errado no mesmo turno.

**Justificativa:** (a) a *mudança* de uma decisão é informação — "usamos
X, depois migramos pra Y porque Z" evita que uma sessão futura reproponha
X; (b) o agente nunca decide "edito ou crio?", só grava por cima;
(c) append-de-conteúdo é o modelo mais barato de tornar crash-safe.

### D5 — Briefing com orçamento, prioridade por dano e stubs do que ficou de fora

`cortex_briefing` devolve um digest markdown com orçamento
(`budget_chars`: default 6000, mín. 500, máx. 20000 — chars como proxy
consciente de tokens). Estrutura:

- **linha 1: caminho do db em uso** (defesa contra "memória errada", D7)
  + nº de entradas e de sessões distintas;
- seções por prioridade de dano:
  `constraints > decisions ativas > questions abertas > progress
  recente (5) > lessons > facts`;
- **toda entrada exibe seu `#id`** (pré-requisito do fluxo de supersede);
- ordenação intra-seção: constraints em ordem de inserção; questions da
  mais antiga pra mais nova (pendência velha é a mais esquecida);
  demais seções por recência decrescente (o mais novo sobrevive ao corte);
- questions com ≥2 sessões mais novas que a dela ganham sufixo
  "(antiga — confirme se ainda vale ou feche com supersedes)";
- ao estourar o orçamento, corta na ordem inversa de prioridade, e o que
  foi cortado vira **stub de uma linha** (`#id [type] primeiros ~60
  chars…`) — um índice do que existe, não uma contagem morta; se nem os
  stubs cabem, resume em "+N omitidas (cortex_recall type=X)";
- **o corte é monotônico na prioridade**: a partir da PRIMEIRA entrada
  que não cabe, todas as restantes (da mesma seção e das de prioridade
  menor) viram stubs — sem isso, facts curtos entrariam inteiros
  enquanto uma constraint longa ficava invisível, o oposto do contrato.
  Os stubs saem na mesma ordem de prioridade e param no primeiro que
  não cabe (uma reserva fixa no orçamento garante seção de stubs + ~2
  stubs + resumo);
- caso degenerado: constraints sozinhas estouram o orçamento → também
  truncam (recência) com o mesmo aviso — o orçamento é o contrato duro;
- base vazia → mensagem de arranque com o caminho do db;
- rodapé aponta `cortex_recall` para o restante.

**Justificativa:** o briefing compete pelo mesmo recurso escasso que ele
protege — a janela de contexto. Um dump completo mataria o paciente com
o remédio. Constraints primeiro porque violá-las é o dano mais caro;
facts por último porque são recuperáveis — mas recuperáveis DE VERDADE
só se o agente souber que existem, daí stubs em vez de contagens.

### D6 — SQLite (WAL) + FTS5 com fallback LIKE

Uma base por tarefa em `$CORTEX_DIR/cortex.db` (D7). Regras concretas:

- `sqlite3.connect(path, timeout=10)` + `PRAGMA busy_timeout=5000` +
  `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` (suficiente
  para morte de processo; queda de energia fora do escopo — meta 4);
- `isolation_level=None` (autocommit) com transações explícitas
  `BEGIN IMMEDIATE … COMMIT` em toda escrita — o write lock é adquirido
  na entrada (elimina o deadlock de upgrade em WAL) e **a resposta da
  tool só é emitida depois do COMMIT retornar** (invariante testável);
- corrida de inicialização (2 processos subindo juntos) NÃO é coberta
  pelo busy_timeout no caso da conversão pra WAL (deadlock de upgrade
  não invoca o busy handler — provado por probe): o setup é idempotente
  e roda num laço de retry; além disso o servidor re-tenta abrir o
  store a cada tool call (falha transitória de startup nunca é sentença
  de morte). Coberto por teste real de dois processos vivos;
- **detecção de FTS5 no OPEN** (novo OU existente): probe
  `CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x)` em try/except.
  Com FTS5: tabela externa `entries_fts(text, content='entries')`
  mantida por **código de aplicação, não por triggers** — o insert no
  índice acontece na MESMA transação do insert em `entries`. Triggers
  foram considerados e rejeitados: ficam no schema do db, então um
  runtime SEM FTS5 que abrisse um db criado COM FTS5 falharia em
  QUALQUER insert (o trigger toca a virtual table → "no such module").
  Sem FTS5 (inclusive db criado COM e aberto SEM): busca degrada para
  `LIKE` case-insensitive sem tocar a virtual table, e escritas seguem
  normais (nenhum trigger no caminho). Ao reabrir com FTS5, uma
  checagem de contagem (`entries` × `entries_fts`) detecta índice
  defasado e dispara `INSERT INTO entries_fts(entries_fts)
  VALUES('rebuild')` — barato no corpus de uma tarefa. Como `text` é
  imutável e nada é deletado (D4), insert + rebuild são as únicas
  operações de índice que existem;
- `tags` fica FORA do índice FTS (match em tag não infla relevância de
  busca textual; filtro de tag é exato, D-recall).

**Justificativa:** SQLite é o único storage da stdlib com ACID real
(meta 4). JSON/JSONL em arquivo exigiria locking e escrita atômica à
mão — reinventar mal o que o SQLite faz certo. WAL permite duas sessões
do Claude Code no mesmo projeto; append-only (D4) evita conflito de
edição entre elas (escritas concorrentes são inserts independentes;
supersede concorrente da mesma entrada: o primeiro vence, o segundo
recebe o erro instrutivo de cadeia do D4).

### D7 — Escopo da tarefa = diretório resolvido explicitamente (cwd como default honesto)

Resolução do diretório-base, nesta ordem:

1. `CORTEX_DIR` (env) — caminho absoluto, forma robusta;
2. cwd do processo — correto quando o servidor é registrado no escopo
   do projeto (INSTALL.md manda fazer exatamente isso).

Guardas de sanidade no startup: se o dir resolvido é `/` ou `$HOME`, ou
não é gravável, o servidor loga aviso claro em **stderr** e (no caso
não-gravável) responde as tools com `isError` explicativo em vez de
morrer mudo. O briefing SEMPRE mostra o caminho do db na primeira linha
— humano e agente detectam "estou lendo a memória errada" em um relance.

> **Revisto por D13-D15 (v2.0.0).** O aviso em stderr provou-se
> insuficiente: ninguém o lê, e enquanto isso o servidor serve uma
> memória global — o que torna FALSA a promessa de que nada vaza para o
> briefing de outro projeto. `/` e `$HOME` passam a ser fail-closed. A
> resolução também deixa de ser o cwd cru (D13) e a memória passa a ser
> carimbada (D14).

**Trade-off assumido:** o escopo real é "um diretório/worktree", que é
um *proxy* de "uma tarefa" — o padrão de trabalho que o enunciado
descreve (tarefas longas ≈ branch/worktree). Duas tarefas sequenciais no
mesmo diretório compartilham memória; a mitigação é o protocolo de
encerramento nas instructions (fechar questions, gravar progress final
"tarefa concluída") + o padrão worktree-por-tarefa/`CORTEX_DIR`
documentado no INSTALL.md. Um "ciclo de vida de tarefa" com tool própria
foi considerado e rejeitado: violaria D2 pagando fricção permanente para
resolver um caso raro.

### D8 — Zero dependências: protocolo MCP implementado à mão (stdio)

Python 3.9+ stdlib apenas. JSON-RPC 2.0 sobre stdio, mensagens JSON
delimitadas por newline. Regras de ferro:

- **stdout é sagrado**: carrega EXCLUSIVAMENTE mensagens JSON-RPC, uma
  por linha (`json.dumps` sem newlines internos — invariante testada),
  cada uma seguida de `flush()` explícito (stdout de pipe é
  block-buffered em Python; sem flush o host trava no initialize).
  Todo log/diagnóstico/traceback vai para **stderr**;
- **mensagem SEM `id` = notification: NUNCA respondida** — conhecida
  (`notifications/initialized`, `notifications/cancelled`) processa/
  ignora; desconhecida, ignora em silêncio. Mensagem COM `id` e método
  desconhecido → erro `-32601` ecoando o id com o MESMO tipo (string ou
  número — sem coerção);
- JSON malformado → resposta `-32700` com `id: null` (JSON-RPC 2.0) e o
  servidor segue vivo; request sem `method` (ou não-string) → `-32600`;
  `params`/`arguments` que não sejam objeto → `-32602`;
- **batch JSON-RPC** (array de mensagens) é atendido com um array de
  respostas — a revisão `2025-03-26` do MCP, anunciada como suportada,
  inclui batching;
- **negociação de versão**: versões suportadas `2024-11-05`,
  `2025-03-26`, `2025-06-18`; se a do cliente está na lista, ecoa; senão
  responde a mais recente suportada (as features usadas — tools/list,
  tools/call com content[] — são estáveis entre elas);
- `initialize` result: `protocolVersion`, `capabilities: {"tools": {}}`,
  `serverInfo {name, version}`, `instructions`;
- `tools/call`: `arguments` ausente → `{}`. Resultado de sucesso com
  shape literal `{"content": [{"type": "text", "text": …}],
  "isError": false}` (content é ARRAY de objetos, nunca string);
- **fronteira de erros**: erro de protocolo (JSON inválido, método
  desconhecido, tool inexistente) → erro JSON-RPC (`-32700`/`-32601`/
  `-32602`); erro de domínio/validação DENTRO de tool conhecida (tipo
  inválido, texto vazio…) → resultado `isError: true` com mensagem
  instrutiva (volta ao MODELO, que se autocorrige e re-chama);
- métodos atendidos: `initialize`, `ping` (result `{}`), `tools/list`,
  `tools/call`; timestamps gravados com
  `datetime.now(timezone.utc).isoformat()` (produz `+00:00`, ordenável
  lexicograficamente; nunca re-parseado — 3.9 não lê sufixo `Z`).

**Justificativa:** instalar = clonar + 1 comando `claude mcp add`. Sem
npm/pip, sem rede, sem supply chain — "local-only" no sentido forte. O
risco do protocolo à mão é mitigado em dois níveis: testes de integração
que falam o protocolo REAL contra o processo real (handshake, list,
call, kill-restart, notifications, ids string/número, stdout 100% JSON)
+ smoke test contra o Claude Code real documentado no DEMO.md.

### D9 — Busca lexical, não embeddings

**Justificativa:** o corpus é minúsculo (uma tarefa ≈ 10²–10³ entradas
curtas), tipado e etiquetado — o cenário onde FTS + filtro por tipo +
recência ganha de similaridade vetorial em precisão E em custo. Embeddings
exigiriam modelo local (dependência pesada) ou API (viola local-only), e
introduziriam não-determinismo nos testes. A estrutura (D3) já faz o
trabalho semântico grosso.

### D10 — Proveniência por sessão

Cada entrada registra `session` (id aleatório curto por processo do
servidor) e timestamp UTC. O briefing mostra de quantas sessões a
memória veio — evidência de que a informação atravessou sessões, e
insumo para o hint de "question antiga" (D5).

### D11 — Defesa contra o erro mais provável: supersedes esquecido (detecção de duplicata na escrita)

Todo `cortex_remember` SEM `supersedes` compara o texto novo com as
entradas ATIVAS do mesmo tipo (similaridade Jaccard sobre tokens
alfanuméricos lowercase de ≥3 chars). Similaridade ≥ 0.5 → o resultado
anexa: *"Atenção: parecida com #12 (active): «…». Se substitui, regrave
com supersedes=12."* Nada é bloqueado. O aviso é suprimido quando a
entrada apontada acabou de ser arquivada pelo auto-supersede do D12 —
uma instrução que falharia se seguida é pior que nenhuma.

**Justificativa:** pós-compressão o agente NÃO lembra que já gravou
"retry com backoff" — a gravação saiu do contexto junto com tudo — e
grava de novo, às vezes com decisão contrária. Sem defesa, isso cria as
verdades paralelas que a meta 3 proíbe, inflando justamente as seções
que o orçamento nunca corta. O motor de busca já existe; usá-lo na
escrita converte o esquecimento (inevitável) em correção no turno
seguinte (barata). Determinístico e testável.

### D12 — Pressão de gravação: o servidor lembra o agente de escrever

O servidor conta tool calls desde o último `remember` bem-sucedido
(estado em memória do processo). A partir de 8 chamadas sem gravação,
as respostas de `briefing`/`recall` ganham um rodapé de uma linha:
*"N chamadas desde a última gravação — decisões e lições não gravadas
morrem na próxima compressão."*

Além disso, entradas de `progress` da MESMA sessão se auto-supersedem
(gravar checkpoint novo marca o checkpoint ativo anterior da sessão como
superseded) — checkpoint é estado corrente por natureza; marcos entre
sessões permanecem.

**Justificativa:** o momento de maior perda é pré-compactação, que o
agente não detecta. As respostas de tools são o único canal que
continua chegando ao contexto NOVO depois da compressão — cada interação
vira um lembrete mecânico, sem depender de prosa que pode ser comprimida.
O auto-supersede de progress evita que o ruído de checkpoints ensine o
agente a ignorar o briefing.

---

### D13 — A raiz do projeto é resolvida, não assumida

O cwd cru quebra em dois casos reais. Em monorepo, abrir a sessão em
`repo/packages/web` num dia e em `repo/` no outro criava **dois**
cadernos para a mesma tarefa — e o briefing vazio faz o agente
re-derivar tudo, que é exatamente a falha que o produto existe para
impedir. E quando o host lança de `/` ou `$HOME`, o cwd aponta para um
lugar onde a memória seria global.

Resolução nova, a partir de `realpath(cwd)`: subir até o primeiro
ancestral que contenha `.cortex/` ou `.git/`, **parando antes de
`$HOME`**; nada encontrado, o próprio cwd. Se o resultado for `$HOME` ou
`/`, o servidor entra em **modo sem-projeto**: toda tool responde
`isError` com a instrução de correção, e nada é criado no disco.
`CORTEX_DIR` continua sendo o escape explícito do humano e não passa
pela guarda — é o próprio remédio que a mensagem de erro ensina.

Uma worktree do git é um caso à parte: seu `.git` é um *arquivo* que
aponta para o repositório principal. Tratá-la como raiz faz a memória
nascer numa cópia de trabalho **descartável** — ferramenta de agente cria
e remove worktree o tempo todo — e a tarefa perde exatamente o que
deveria ter preservado. Então a worktree resolve para o repositório dono,
lendo o `gitdir:` do arquivo. Custo aceito: duas worktrees em paralelo
compartilham a memória do repo. As entradas já carregam sessão, então a
mistura é legível; perder tudo não é.

A ordem das checagens é política, não detalhe: a marca de worktree é
testada **antes** de `.cortex/`. Um diretório deixado por uma versão com
defeito não pode virar a causa da resolução seguinte — senão o bug
sobrevive ao próprio conserto, e limpar o código não liberta as
worktrees já marcadas.

A âncora é o REPOSITÓRIO, não a árvore principal. Em `--separate-git-dir`
e em repo bare, "onde está a árvore principal?" pode não ter resposta em
lugar nenhum do disco — o config não registra `core.worktree`, e o próprio
`git worktree list` nomeia o dir comum como principal. "Qual é o
repositório?" sempre responde: o common dir, que o `git worktree remove`
não toca. A memória de uma worktree vinculada ancora ali (`B.git/.cortex`).
Custo assumido: nesses dois layouts ela mora dentro do git dir, fora da
vista — previsível valeu mais que visível, e uma regra é mais sustentável
que duas. Worktree-por-branch sobre repo bare é fluxo mainstream que, sem
isso, ficava amnésico.

Corolário de embalagem: **o manifesto do plugin não define `CORTEX_DIR`**.
Fixá-lo em `${CLAUDE_PROJECT_DIR}` parecia prudente e desligava tudo isto
— numa worktree essa variável É a worktree, e o early-return do escape
explícito transformava toda a resolução em código morto. Config que
sempre preenche o campo de override anula qualquer lógica que rode depois
dele. Há um teste de contrato travando isso.

**Trade-off assumido:** a guarda é breaking. Quem hoje roda com cwd em
`$HOME` e uma memória global "funcionando" passa a receber erro. É
deliberado: essa configuração já era descrita como errada, e servir ali
mente sobre o escopo. O conserto é uma variável de ambiente.

### D14 — Carimbo de dono: a pasta copiada é somente-leitura

`.cortex/` é uma pasta comum e vai junto num `cp -r` ou num template de
projeto. Sem carimbo, a memória do projeto A passa a receber escrita do
projeto B sem que ninguém perceba.

Na criação do diretório nascem, junto, um `.gitignore` com `*` (a
memória nunca entra no git por acidente — antes isso era instrução
manual no README) e um `OWNER` com o caminho canônico da raiz. A cada
chamada de tool — não só no boot, porque a pasta pode ser trocada sob um
processo vivo — o dono é reconferido. Divergiu: **somente-leitura**.
`briefing` e `recall` seguem funcionando, com aviso citando o dono real;
`remember` recusa.

Adoção é **vinculada ao valor**: `CORTEX_ADOPT=<caminho do dono
divergente>`, nunca uma flag genérica. Uma variável esquecida num
`.mcp.json` só re-adotaria daquele dono específico já morto — jamais
destrava um mismatch novo. Toda comparação usa `realpath` nos dois lados
(o `/tmp` → `/private/tmp` do macOS geraria falso mismatch).

### D15 — Memória sem carimbo é adotada, não recusada

Divergência deliberada em relação ao desenho greenfield desta trava: lá,
memória sem dono é órfã e fica somente-leitura. Aqui **não pode ser** —
o córtex já está publicado e em uso, e todo banco existente é anterior
ao carimbo. Recusá-los quebraria toda instalação no upgrade.

Um `.cortex/` que está na sua própria raiz resolvida não é cópia: é onde
deveria estar. Então, na ausência de `OWNER`, ele é carimbado e segue
gravável. A proteção do D14 passa a valer para tudo que nasce a partir
daqui.

**Custo assumido:** uma pasta copiada *antes* desta versão é
indistinguível de uma legítima e será adotada no primeiro contato. É o
preço de não quebrar quem já usa — e o modelo de ameaça é acidente, não
adversário.

## 3. Modelo de dados

```sql
CREATE TABLE entries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT    NOT NULL,              -- UTC ISO-8601 (+00:00)
  session    TEXT    NOT NULL,              -- id do processo que gravou
  type       TEXT    NOT NULL,              -- um dos 6 tipos (D3)
  text       TEXT    NOT NULL,              -- imutável após insert
  tags       TEXT    NOT NULL DEFAULT '[]', -- JSON array, lowercase
  status     TEXT    NOT NULL DEFAULT 'active',  -- active | superseded
  supersedes INTEGER                        -- id da entrada substituída
);
-- com FTS5 disponível (D6):
CREATE VIRTUAL TABLE entries_fts USING fts5(
  text, content='entries', content_rowid='id'
);
-- índice mantido por código (mesma transação do insert), SEM triggers
-- (D6 explica por quê); rebuild automático quando defasado
```

IDs são inteiros pequenos e sequenciais de propósito: o agente precisa
digitá-los em conversas (`supersedes: 12`) — `#12` é utilizável, um UUID
não.

Filtro de tags: matching EXATO via `tags LIKE '%"<tag>"%'` sobre o JSON
normalizado (lowercase, trim) — zero dependência da extensão JSON1, sem
falso-positivo de substring (`api` não casa `rapid` nem `api-v2`, porque
as aspas delimitam o token no JSON).

## 4. API das tools

### `cortex_briefing`

Opcional: `budget_chars` (int, default 6000, clamp [500, 20000]).
Retorna o digest do D5. Base vazia → instrução de arranque.

Descrição (gatilho; redigida como canal único garantido): *"Memória
durável desta tarefa — seu contexto é volátil, ela não. Chame PRIMEIRO
ao iniciar ou retomar qualquer sessão de trabalho, antes de planejar ou
editar. Devolve restrições, decisões vigentes, pendências e progresso
registrados por sessões anteriores desta mesma tarefa."*

### `cortex_remember`

| campo | tipo | obrig. | notas |
|---|---|---|---|
| `type` | enum (D3) | sim | inválido → isError listando os válidos |
| `text` | string | sim | 1..2000 chars pós-trim; vazio ou não-string → isError |
| `tags` | array de string | não | lowercase/trim, sem `"`/`\` (preserva o matching exato via JSON); string em vez de array → isError (iterar char a char corromperia a base) |
| `supersedes` | int | não | inexistente ou já-superseded → isError instrutivo; bool/float → isError (coerção silenciosa acertaria a entrada errada) |

Validação de TIPO de argumento acontece na borda do store: qualquer
argumento malformado vira `isError` instrutivo (o modelo se autocorrige)
— nunca um "erro interno" opaco.

Retorna `Registrado #<id> (<type>)`, ecoando a entrada substituída
quando houver (D4), warning de tipo divergente (D4), aviso de duplicata
provável (D11). Commit ANTES da resposta (D6).

Descrição (gatilho): *"Registre NO MOMENTO em que acontecer: decisão
tomada (com o porquê), restrição descoberta, lição aprendida (bug
corrigido, beco sem saída), marco de progresso, pergunta em aberto,
fato caro de re-derivar. Se está prestes a escrever 'vou assumir que…',
registre a suposição. Decisão mudou? Regrave com `supersedes`.
constraint = regra imposta que você não pode violar; decision = escolha
sua com porquê; fact = observação verificada."*

### `cortex_recall`

| campo | tipo | obrig. | notas |
|---|---|---|---|
| `query` | string | não | sanitizada (nunca crua no MATCH); sem query → recentes |
| `type` | enum | não | filtro |
| `tags` | array | não | filtro exato, AND |
| `limit` | int | não | default 10, clamp [1, 50] |
| `include_superseded` | bool | não | default false |

Sanitização de query (FTS): tokeniza o texto do agente e embrulha cada
token em aspas duplas (escapando `"` por duplicação), AND implícito —
aspas, parênteses, hífens, `NOT/OR`, `*` etc. viram texto literal, nunca
sintaxe. Caminho LIKE: escapa `%` e `_` (ESCAPE) e exige TODOS os tokens
(AND, consistente com o FTS).

Ordenação: com query — relevância bm25 no caminho FTS; **recência no
caminho LIKE** (divergência documentada e testada: asserções de
relevância são específicas de FTS). Sem query — recência. No caminho
LIKE o case-fold roda em **Python** (o `lower()` do SQLite é ASCII-only
e deixaria "CAFÉ" inbuscável).

Cada resultado: `#id [type] (status, ts, sessão) texto — tags`.

Descrição (gatilho): *"Antes de re-derivar algo que a tarefa já pode ter
respondido — 'qual porta?', 'por que escolhemos X?', 'esse bug já
apareceu?' — busque aqui. Use include_superseded para ver o histórico de
uma decisão."*

## 5. `instructions` do servidor (o protocolo de uso)

Texto injetado no host no `initialize` (resumo do conteúdo real):

> córtex é sua memória de trabalho durável desta tarefa — seu contexto é
> volátil, ela não. Protocolo: (1) ao iniciar/retomar sessão, chame
> `cortex_briefing` antes de trabalhar; (2) registre com
> `cortex_remember` na hora: decisões (com porquê), restrições, lições,
> progresso, perguntas; (3) antes de re-derivar algo, `cortex_recall`;
> (4) decisão mudou → nova entrada com `supersedes`; (5) prestes a
> escrever "vou assumir que…" → registre a suposição; (6) antes de
> declarar um marco concluído ao usuário, grave um `progress`;
> (7) respondeu uma pendência → feche a `question` com `supersedes`;
> (8) ao ENCERRAR a tarefa, grave um `progress` final e feche as
> questions — o próximo trabalho neste diretório herda esta memória.

## 6. Fluxo típico (o cenário do enunciado)

1. **Sessão A**: agente trabalha, grava `constraint` ("API só aceita
   cursor-pagination"), `decision` ("retry com backoff exponencial,
   porque X"), `progress`, uma `question` aberta.
2. Contexto comprime ou a sessão morre. O processo do servidor morre
   junto — irrelevante: tudo commitado.
3. **Sessão B** (contexto zero): host reconecta o MCP, injeta
   `instructions`; agente chama `cortex_briefing` (gatilho na descrição
   da tool mesmo sem instructions) e recebe constraints, decisões
   vigentes e a pergunta aberta COM seus #ids; responde a pergunta
   gravando a resposta com `supersedes`; segue sem contradizer a sessão A.

## 7. Plano de testes (escritos ANTES da implementação)

**Camada store (`tests/test_store.py`)** — unidade, SQLite real em
diretório temporário, parametrizada nos DOIS caminhos de busca
(FTS5 e LIKE forçado):

- remember: ids crescentes; persiste após reabrir; validações (tipo
  inválido, texto vazio/2000+/não-string, tags string-em-vez-de-lista,
  supersedes inexistente/bool/float, supersedes de entrada
  já-superseded → erro citando o head; cadeia órfã → erro honesto sem
  loop morto); tags lowercase e sanitizadas (`"`/`\`);
- atomicidade (meta 4): sabotagem real no meio do supersede (índice FTS
  derrubado por outra conexão) → rollback completo, nunca estado parcial;
- convivência: dois escritores em threads E dois processos servidores
  vivos no mesmo db; retry de inicialização;
- supersede: atômico (nunca duas ativas na cadeia), eco da substituída,
  warning de tipo divergente (menos question→*), question fechada some
  do briefing;
- duplicata (D11): texto similar do mesmo tipo → aviso com #id; tipos
  diferentes ou dissimilar → sem aviso;
- progress auto-supersede intra-sessão; entre sessões preserva (D12);
- recall: match/não-match; caracteres especiais FTS (aspas, parênteses,
  hífen, apóstrofo, operadores) sem exceção nos dois caminhos; filtro
  por tipo; tag exata sem falso-positivo de substring; limit clamp;
  superseded excluído por default; recência sem query; case-fold
  unicode nos dois caminhos; ordenação COM query nos dois caminhos
  (bm25 no FTS, recência no LIKE); query/tags não-string → erro
  instrutivo;
- briefing: caminho do db na linha 1; #ids visíveis; ordem de seções;
  ordem intra-seção (decisions recentes primeiro, questions antigas
  primeiro); orçamento corta facts→stubs antes de constraints; caso
  degenerado (constraints estouram sozinhas); stubs com #id+prefixo;
  base vazia; contagem de sessões; hint de question antiga; clamp do
  budget; timestamps `+00:00` ordenáveis como string.

**Camada protocolo (`tests/test_protocol.py`)** — integração,
subprocesso real via stdin/stdout:

- handshake: versão conhecida ecoada; desconhecida → mais recente
  suportada; capabilities/serverInfo/instructions;
- tools/list: exatamente 3 tools, schemas com required corretos;
- roundtrip remember→recall no mesmo processo;
- **critério do enunciado**: gravar → MATAR o processo → processo novo
  no mesmo dir → briefing recupera;
- dois processos sequenciais → `session` distintos no recall;
- JSON malformado → `-32700` id null e servidor segue vivo; método
  desconhecido com id string E id numérico → `-32601` com eco EXATO do
  id; request sem method → `-32600`; batch → array de respostas;
  notifications (conhecida e inventada) → NADA no stdout;
- tool desconhecida → `-32602`; `arguments` ausente → default `{}`;
  `params`/`arguments` não-objeto → `-32602`;
- validação dentro de tool → `isError: true` com mensagem instrutiva
  (inclusive argumentos de tipo errado — nunca "erro interno" opaco);
- diretório-base não-gravável → servidor fica de pé e responde tools
  com isError explicativo; dois processos servidores simultâneos
  convivem no mesmo db;
- shape: content é array de `{type:'text',text}`; TODA linha do stdout
  da conversa inteira parseia como JSON (higiene D8);
- rodapé de pressão de gravação: ausente na 7ª call, presente na 8ª
  (fronteira exata) e também no caminho do briefing (D12).

Runner: `python3 -m unittest discover -s tests` (stdlib, zero deps).

## 8. Entregáveis e evidência

| entregável | conteúdo | evidência do critério de sucesso |
|---|---|---|
| `SPEC.md` | este documento | decisões justificadas (req. 4) |
| testes | §7, commitados VERMELHOS antes da implementação | TDD auditável no git |
| implementação | `cortex_store.py` + `cortex_server.py` | suite verde |
| `INSTALL.md` | `claude mcp add` exato (escopo projeto), `CORTEX_DIR`, `.gitignore` do `.cortex/`, reforços opcionais (linha no CLAUDE.md, hook PreCompact) | smoke test `/mcp` |
| `DEMO.md` | cenário do §6 executado DE VERDADE: sessão A grava → processo morre → sessão B (contexto zero) recupera via briefing e age coerente | transcript real; se viável, contra o Claude Code real |

## 9. Riscos e mitigações

| risco | mitigação |
|---|---|
| Agente não usa a memória | D1 em camadas; briefing barato; D12 (pressão de gravação) |
| Agente ERRA o uso (id errado, sem supersedes, tipo errado) | D4 (eco + warnings), D11 (duplicata), descrições ensinam fronteiras (D3) |
| Perda pré-compactação | D12 (rodapé), gatilho de fim-de-marco nas instructions, hook opcional no INSTALL.md |
| Briefing incha e come contexto | D5 (orçamento duro + stubs + ponteiro pro recall) |
| Host incompatível com protocolo à mão | regras de ferro D8 + testes de protocolo + smoke real no DEMO |
| stdout contaminado / sem flush | D8 (stderr-only, flush por mensagem, teste "stdout 100% JSON") |
| Query do agente derruba o FTS | sanitização obrigatória (§4) testada com inputs hostis |
| FTS5 ausente (ou db migrado entre runtimes) | detecção no OPEN + fallback LIKE testado (D6) |
| Duas sessões simultâneas | WAL + busy_timeout + BEGIN IMMEDIATE (D6); append-only (D4) |
| cwd do host imprevisível | D7: guardas de sanidade, caminho no briefing, INSTALL.md manda escopo de projeto |
| Base cresce sem limite | escopo por dir (D7), auto-supersede de progress (D12), briefing orçado, recall paginado |

## 10. Registro de revisão (painel adversarial pré-implementação)

3 revisores independentes (juiz / ergonomia de agente / arquitetura)
criticaram a v1 deste SPEC. Mudanças aceitas: higiene de stdout+flush e
sanitização de FTS MATCH como regras de ferro (2 blockers); notifications
nunca respondidas + eco exato de id; algoritmo de negociação de versão;
guardas de cwd + caminho do db no briefing; DDL de triggers FTS +
detecção no open; busy_timeout + BEGIN IMMEDIATE + commit-antes-de-
responder; D1 rebaixado de "instructions garante" para camadas com
degradação; D11 (duplicata na escrita) e D12 (pressão de gravação +
auto-supersede de progress) criados; briefing ganhou #ids, stubs, ordem
intra-seção, hint de question antiga, caso degenerado de constraints e
clamp de budget; supersede ganhou atomicidade explícita, eco, warning de
tipo e erro instrutivo de cadeia; tags com matching exato; entregáveis
INSTALL/DEMO planejados no SPEC (§8). Rejeições conscientes: tool de
ciclo-de-vida de tarefa (viola D2; trade-off documentado em D7);
embeddings (D9 mantida).

**Segunda rodada (review adversarial pós-implementação, 4 lentes + 2
céticos por finding, tudo provado por probe antes de corrigir):** corte
do briefing virou monotônico na prioridade (constraint longa ficava
invisível enquanto facts triviais entravam inteiros); corrida REAL de
inicialização entre dois processos (deadlock de upgrade na conversão
WAL não invoca busy handler — descoberta pelo teste novo de dois
processos) → retry idempotente + re-abertura preguiçosa; cadeia órfã
do arquivamento em lote → erro honesto; validação de tipo de argumento
na borda (text/query/tags/supersedes) → isError instrutivo; tags
sanitizadas contra `"`/`\`; case-fold unicode no fallback LIKE; clamp
do briefing de base vazia; batch JSON-RPC; -32600/-32602 para requests
malformados; aviso de duplicata suprimido quando aponta entrada
auto-arquivada; dois testes vácuos fortalecidos (sessões, stubs) e
gaps de cobertura fechados (atomicidade com sabotagem real, fronteira
do nudge, briefing-nudge, ordenação com query, dir não-gravável).
