# Plataforma de Forms da Keiko — Review de Arquitetura

Data: 2026-09-13. Escopo: o subsistema Form + YAML + Estado (`app/views/form.py`,
`app/views/form_state.py`, `app/views/manager.py`, `app/views/summary_card.py`,
`app/views/composition.py`, `app/views/edit.py`, `app/views/remove.py`,
`app/components/*`, `app/services/moderations.py`, `app/languages/form/*.yml`) e tudo
que se conecta nele. Só review: nenhum código foi alterado.

Como ler as etiquetas de evidência: **Fato** (o código ou os testes mostram),
**Inferência forte** (várias pistas apontam para o mesmo lado), **Hipótese** (plausível,
não confirmado). As referências `arquivo:linha` são da branch atual
(`rukasudev/adding-metrics`, HEAD `6cf2016`).

Versão detalhada em inglês, com o mesmo conteúdo: `docs/form-platform-architecture-review.md`.

> **Nota de baseline (2026-09-13).** Esta review foi traçada na branch da worktree em
> `6cf2016`. O `origin/main` (`65f7ec1`) está 175 arquivos à frente e já muda várias
> afirmações sobre o "estado atual" (correção do event loop, `on_timeout`, ids de sessão,
> traces e journeys, painel do manager em Components V2). A **Parte II do arquivo em
> inglês** reavalia cada afirmação afetada contra o `main` e responde sete perguntas de
> acompanhamento (erros históricos, observabilidade, continuidade da UX, forms
> multicondicionais, prontidão para builder de YAML, estilo de código, caminho sem
> restrição de esforço). Traduzida abaixo como Parte II.

---

## 1. Resumo executivo

**Em uma frase:** a ideia do produto está certa, a forma como ela roda por dentro é que
está errada.

**A ideia (manter).** Uma feature é um arquivo YAML que combina peças genéricas; o bot
transforma isso numa conversa guiada com o admin; no fim sai um documento de configuração
salvo. Sete features já funcionam assim e a suíte de testes offline prova que dá para
dirigir tudo sem Discord de verdade.

**O runtime (trocar).** O motor é uma classe `Form` que herda de `discord.ui.View` e tem
1.173 linhas. Essa mesma classe faz cinco trabalhos ao mesmo tempo: lê a definição,
guarda o estado da sessão, desenha as telas, recebe os cliques e salva no banco. Além
disso ela hospeda regras de features específicas. O estado fica em atributos mutáveis
desse objeto e em dicionários dentro das views filhas; todo callback altera esse estado
diretamente; desenhar a tela é um efeito colateral de mudar o estado; não existe
identidade de sessão, nem número de versão, nem status de ciclo de vida, nem separação
entre "decidir o que acontece" e "fazer acontecer no Discord e no Mongo".

Isso explica, na prática, os três problemas que você relatou:

- **Mudar um form quebra outro** porque comportamento novo só tem um lugar para morar:
  helpers compartilhados por todos os forms.
- **Voltar, clicar duas vezes e estado velho** acontecem porque nada protege o estado.
- **Falhas parciais** deixam a guild num estado misto porque salvar é uma sequência de
  chamadas sem fronteira.

**Duas causas de ghost click estão fora do motor e são baratas de corrigir.** Elas devem
ser as primeiras coisas a fazer:

1. Chamadas síncronas de rede e banco rodam dentro do event loop do bot, e existe um
   `time.sleep(15)` literal no caminho de "streamer ficou online". Enquanto o loop está
   travado, nenhuma interação de nenhum servidor é respondida nos 3 segundos que o
   Discord exige. O usuário vê "This interaction failed".
2. As views vivem 30 minutos em memória, sem `on_timeout` e sem sobreviver a deploy.
   Depois disso os botões continuam desenhados, mas ninguém está escutando.

**Recomendação: Opção B.** Extrair um motor determinístico (recebe definição, sessão e
evento; devolve nova sessão e lista de efeitos), colocar o Discord como adapter, manter
YAML como linguagem de definição, migrar um form por vez. Fazer antes o endurecimento da
Opção A (trava contra clique duplo, finalização no timeout, destravar o loop, validar o
YAML) porque é barato e reduz risco de tudo que vem depois. Não construir agora uma
plataforma de workflow com sessões persistidas (Opção C): o produto não precisa de forms
que sobrevivam a restart, e o custo não compra nenhum bug que você tem hoje.

---

## 2. O que um Form é, de verdade

**Em uma frase:** um Form é uma sessão de configuração guiada, curta, de um único admin,
que termina em um documento salvo.

Olhando os sete YAMLs e seus services:

- **Conversa guiada.** Um admin, numa sequência de mensagens efêmeras, responde uma lista
  curta de perguntas e confirma. O resultado é um documento por (guild, feature) mais zero
  ou mais ações de domínio (assinatura na Twitch/YouTube, agendamento de lembrete,
  re-hospedagem de arquivo).
- **Curta e de um ator só.** Views expiram em 30 minutos (`app/views/form.py:71`);
  mensagens são efêmeras (`app/services/moderations.py:121`); nada sobrevive a restart.
  Só quem abriu consegue clicar.
- **Dois modos da mesma sessão.** *Setup* (roda todos os passos e chama `_finish`) e
  *Gerenciar* (pausar, despausar, desativar, histórico, e reexecuções parciais do wizard
  para *editar um passo*, *adicionar item*, *remover item*). Hoje os dois modos são feitos
  reinstanciando `Form` com passos filtrados (`app/views/edit.py:21,121`,
  `app/components/buttons.py:318-324`).
- **Composições** (uma lista de itens, cada item configurado por um sub-wizard) são a
  única estrutura além de uma lista plana de passos; três dos sete forms usam.

Então o objeto de domínio não é "uma tela" nem "um motor de workflow". É uma
**máquina de estados sobre uma definição**: `(definição, sessão, evento) → (sessão', efeitos)`.
Todo o resto (embeds, LayoutViews, modais, documentos no Mongo, chamadas à Twitch) é
projeção ou consequência dessa transição.

---

## 3. Premissas que valem ser questionadas

**Em uma frase:** várias decisões existem porque foram o caminho mais rápido na época, não
porque são as certas.

| Premissa embutida no código | Manter? | Por quê |
|---|---|---|
| YAML é a representação principal do form | **Manter para definição**, parar para comportamento | Definição-como-dado é o motivo de sete features caberem em ~1.1k linhas de YAML. Mas o YAML já vaza comportamento: acesso reflexivo `from: interaction, attr: guild.name` (`app/views/summary_card.py:237-244`), `reset-on-change` chamando validadores por nome (`:326-345`), `condition.not_in` com três grafias de `false` (`reminders_birthday.yml:287-292`). |
| Um Form *é* uma view do Discord | **Trocar** | `Form(discord.ui.View)` (`form.py:47`) amarra a vida da sessão à vida de um componente do Discord, obriga toda mudança de estado a passar por um callback de interação, e torna o núcleo intestável sem uma superfície de Discord. |
| Handlers alteram o estado diretamente | **Trocar** | `self.responses.pop()` (`form.py:1073`), `self.view.response[...] = ...` (`form_state.py:93-94`), `state[mode_state_key] = "default"` (`summary_card.py:468`), `del self.cogs[option]["values"][int(index)]` (`remove.py:117`). Não há ponto único de mutação, não há invariantes. |
| Desenhar tela e avançar o fluxo são a mesma chamada | **Trocar** | Os métodos `show_*` decidem o próximo passo e emitem chamadas ao Discord no mesmo lugar (`form.py:545-557`, `1094-1114`). |
| Lógica de feature pode entrar no motor quando falta um hook | **Trocar** | `pre_finish_step` (`form.py:905-941`), `_start_preview_pregeneration` (`:159-168`), hidratação de mês/data (`:506-512`, `:966-974`), `COMPOSITION_COMMANDS_LIST` / `COMPOSITION_MAX_LENGTH` / `COMMAND_KEY_TO_COMPOSITION_KEY` em `app/constants.py:110-126` consumidos por `form.py:694-699` e `manager.py:308-311`. |
| O documento salvo é derivado da lista de respostas com uma tag `style` | **Repensar** | `{chave: {style, values}}` (`form.py:411-422`) mistura apresentação com armazenamento e gera quatro helpers de "desembrulhar value/values" (`docs/form-configuration.md` §7). |
| Editar = rodar o wizard filtrado | **Manter a ideia, tornar explícita** | É boa ideia de produto (um caminho de código só). Deveria ser um *modo de sessão* com semântica própria de commit, não um `Form` com `command_key=""` cujo pai enfia a mão em `edited_form_view.composition_index` (`reminders_birthdays.py:184-185`). |
| Composição é um `Form` aninhado | **Repensar** | `Form("", locale, steps, cogs=...)` (`composition.py:95`) mais injeção de atributos (`all_cogs`, `composition_responses`, `prefilled_step_keys`) é a construção mais frágil do código. |
| Discord dentro do núcleo | **Trocar** | `SelectDefaultValue` em `form_state.py:105-117`; `discord.ButtonStyle` em `form.py:574-579`; `interaction.message.flags.components_v2` em `form.py:1126`. |
| Sessão não precisa de identidade | **Trocar** | Sem id, sem revisão, sem status. Todo problema de confiabilidade abaixo volta para cá. |

---

## 4. Arquitetura a partir do zero

**Em uma frase:** poucos conceitos, cada um com dono claro, e uma regra só para mudar
estado.

### 4.1 Vocabulário (só o que o produto precisa)

Pense assim: a **definição** é a receita, a **sessão** é o bolo sendo feito, o **evento**
é cada gesto do cozinheiro, o **motor** decide o que o gesto significa, e os **efeitos**
são o que precisa acontecer no mundo (mostrar tela, salvar, chamar API).

| Conceito | O que representa | Por que existe | Dono | Config ou runtime | Plataforma ou feature |
|---|---|---|---|---|---|
| **FormDefinition** | A descrição compilada, imutável e versionada de um form: passos em ordem, tipos de passo, campos, condições, referências a extensões, textos nos dois idiomas. | Tudo que o motor precisa para rodar sem ler YAML em runtime. | Plataforma (compilador) | Config | Plataforma |
| **Step** | Uma pergunta ou tela. Tem `kind` (texto, escolha única, canais, cargos, usuários, multi-select, card, composição, info, revisão), `key` e uma spec do tipo. | Unidade de navegação e de resposta. | Plataforma | Config | Plataforma |
| **FormSession** | O estado de uma execução: `session_id`, `definition_ref` (chave + versão), `mode` (setup, edit(subconjunto), add_item, remove_item), `status`, `cursor` (passo atual), `answers` (`Dict[step_key, Answer]`), `revision`, `origin` (guild, usuário, locale), `opened_at`, `expires_at`, `last_event_id`. | O único estado autoritativo. | Plataforma (motor) | Runtime | Plataforma |
| **Answer** | O valor confirmado de um passo: `raw` (valor de máquina) e `parts` (para passos com várias partes). Rótulos de exibição são calculados na hora de desenhar, não guardados. | Elimina a ambiguidade `value` / `_raw_value` / `style` / `hidden`. | Plataforma | Runtime | Plataforma |
| **Event** | Algo que aconteceu: `Started`, `Answered(step_key, payload)`, `Back`, `Cancel`, `DiscardConfirmed`, `KeepEditing`, `ReviewConfirmed`, `ItemAdded`, `ItemRemoved`, `Expired`. Carrega `event_id` (id da interação do Discord) e `expected_revision` (a revisão que a tela foi desenhada). | A única entrada que pode mudar uma sessão. | Adapter cria, motor consome | Runtime | Plataforma |
| **Decision / Transição** | `engine.decide(definição, sessão, evento) -> (sessão', [Effect])`. Pura. Rejeita evento com `expected_revision` velha ou `event_id` já aplicado. | Torna mudança de estado testável e idempotente. | Plataforma | Runtime | Plataforma |
| **Effect** | Descrição de um efeito que o adapter deve executar: `Render(tela)`, `OpenModal(spec)`, `ShowError(chave)`, `Commit(sessão)`, `RunDomainAction(nome, payload)`, `Finalize(mensagem)`. Dados, não chamadas. | Separa decidir de fazer; permite reportar falha contra um passo conhecido. | Motor emite, adapter executa | Runtime | Plataforma |
| **Screen** | Modelo abstrato de tela: título, descrição, rodapé, campos, componentes (`Choice`, `ChannelPicker`, `RolePicker`, `UserPicker`, `TextInputs`, `Card(seções)`, `Buttons`). Sem tipos do Discord. | Tela derivada do estado; testável sem Discord. | Motor (via tipo de passo) | Runtime (derivado) | Plataforma |
| **Tipo de passo (step kind)** | Um par registrado: `render(step, sessão) -> Screen` e `parse(step, payload) -> Answer` ou erro de validação. | O ponto de extensão para novas formas de UI. | Registro da plataforma | Config | Plataforma |
| **Validator** | Função pura `(valor, contexto) -> ok` ou chave de erro; declara o que precisa (`needs: [guild_config, external:twitch]`) para o adapter buscar antes. | Regras de feature sem mudar o motor. | Registro; implementações da feature | Referência no YAML | Feature |
| **Transform** | `serialize(partes) -> guardado`, `hydrate(guardado) -> partes`. Já existe (`app/services/transforms.py`). | Respostas com várias partes. | Registro | Referência no YAML | Plataforma |
| **Formatter** | `(valor, style, locale) -> str`. Já existe (`format_values_by_style`). | Resumos e painel de gerenciamento. | Registro | Referência no YAML | Plataforma |
| **FeatureModule** | O único objeto que uma feature contribui: `to_document(respostas)`, `from_document(doc) -> respostas`, `commit(sessão, ctx)` (persistência + ações de domínio), `on_disable`, `on_item_added`, `on_item_removed`, `summary(doc, locale)`. Substitui `persistence_callback`, `lifecycle_callbacks`, `settings_provider`, `pre_finish_step` e as listas de constantes. | O único lugar onde lógica de feature vive. | Feature | Runtime | Feature |
| **SessionStore** | `Dict[session_id, FormSession]` em memória com TTL; persistência opcional depois. | Um dono para sessões; permite expiração, serialização por sessão, observabilidade. | Plataforma | Runtime | Plataforma |
| **DiscordAdapter** | Converte `Interaction -> Event`, executa `Effect`s (enviar / editar / substituir / modal / followup), cuida de ids de mensagem, tokens, regras de efêmero, transição embed ↔ LayoutView, timeouts. | Tudo que é do Discord num lugar só. | Plataforma (adapter) | Runtime | Plataforma |

De propósito **não** entram: grafos genéricos de workflow, guards de transição como
conceito separado (condição no passo basta), log persistido de eventos, carregamento
de plugins. Nenhum dos sete forms precisa.

### 4.2 Fluxo de dados e controle

```
arquivo YAML ──► validação de schema ──► validação semântica ──► FormDefinition (congelada, versionada)
                                                                        │
  /comando ou botão ──► DiscordAdapter.start(command_key, mode) ────────┤
                                                                        ▼
                                                SessionStore.create(FormSession)
                                                                        │
  Interação ──► DiscordAdapter.to_event(interaction, session) ────────► Event
                                                                        │
                                                                        ▼
                          Engine.decide(definição, sessão, evento) ──► (sessão', efeitos)
                                                                        │
                          SessionStore.commit(sessão')  ◄───────────────┤   (revision += 1)
                                                                        │
                          DiscordAdapter.execute(efeitos) ◄─────────────┘
                             ├─ Render(tela)        → editar / substituir mensagem
                             ├─ OpenModal(spec)     → response.send_modal
                             ├─ ShowError(chave)    → followup efêmero
                             ├─ Commit(sessão)      → FeatureModule.commit → Mongo / Twitch / lembretes
                             └─ Finalize(tipo)      → tira componentes, embed final
```

### 4.3 Separar decisão pura de efeito colateral: vale a pena aqui?

Sim, por quatro motivos que aparecem no código de hoje:

1. **Testabilidade.** Hoje, testar "voltar restaura o multi-select" exige dirigir uma
   superfície falsa de Discord (`tests/behavioral/harness/`, sete módulos). Com motor
   puro vira `assert decide(defn, s, Back()).session.answers == {...}`.
2. **Idempotência e dedup.** Um `decide` puro rejeita replay de `event_id` e
   `expected_revision` velha antes de tocar no Discord. Hoje o primeiro sinal de clique
   duplo é um erro `40060` do Discord ou um documento duplicado no Mongo.
3. **Falha parcial.** Efeitos são dados em ordem; o adapter registra qual efeito falhou
   em qual revisão e decide se tenta de novo, compensa ou finaliza com tela de erro.
   Hoje `_finish` (`form.py:846-903`) faz assinatura → moderations → insert do cog →
   insert de evento → editar mensagem sem nenhuma fronteira.
4. **Isolamento de feature.** Features contribuem efeitos (`RunDomainAction`) e lógica de
   commit, nunca caminhos de código no motor. `if self.command_key == ...` fica
   impossível por construção, não por convenção.

Custo: uma camada a mais, e o adapter precisa respeitar o contrato "responder uma vez em
3 segundos" do Discord, porque não pode mais responder de dentro de um `show_*`. O
harness já modela exatamente esse contrato
(`tests/behavioral/harness/fake_interaction.py:19-36`), então o custo é conhecido.

### 4.4 Fonte da verdade

- **Autoritativo:** a `FormSession` no `SessionStore` (memória, processo único). A
  `revision` é a versão.
- **Derivado, reconstruível:** toda mensagem e componente do Discord (de
  `render(step, sessão)`); o painel de gerenciamento (do documento salvo via
  `FeatureModule.summary`); rótulos e valores formatados (respostas + locale).
- **Persistente, verdade separada:** o documento de configuração por (guild, feature) no
  Mongo, mais a cópia no Redis (TTL de 30 dias, `app/services/cache.py:29-43`). A sessão é
  uma *proposta* até o `Commit` dar certo; depois o documento é a verdade e a sessão fecha.
- **Nunca fonte da verdade:** `custom_id`s, ids de mensagem, YAML em runtime, variáveis
  de closure, o cache do Redis (invalidado na escrita, `app/services/cogs.py:11,42,51`).

Reconstrução do derivado: qualquer tela é `screen = kind.render(step, session)`; se a
mensagem sumiu ou a edição falhou, o adapter reenvia a mesma tela. Reconstruir o painel é
`summary(load_document(guild, feature))`.

### 4.5 Ciclo de vida

Status do ciclo de vida é separado do cursor (qual passo). Mantenha separados: o cursor
responde "onde na definição", o status responde "essa sessão ainda aceita eventos".

```
            start
              │
              ▼
          ┌────────┐  Answered/Back/Cancel(manter)  ┌───────────┐
          │ ACTIVE │◄──────────────────────────────►│ AWAITING  │  (modal ou picker aberto)
          └───┬────┘                                └───────────┘
              │ ReviewConfirmed
              ▼
        ┌────────────┐  commit ok      ┌───────────┐
        │ COMMITTING │────────────────►│ COMPLETED │
        └─────┬──────┘                 └───────────┘
              │ commit falhou
              ▼
        ┌────────┐      DiscardConfirmed          ┌───────────┐
        │ FAILED │   ACTIVE ─────────────────────►│ CANCELLED │
        └────────┘      Expired / restart         └───────────┘
                     ACTIVE ─────────────────────► EXPIRED
```

Por transição:

| Transição | Gatilho | Pré-condições | Revisão | Efeitos | Persistência | Discord | Falha | Idempotência |
|---|---|---|---|---|---|---|---|---|
| `∅ → ACTIVE` | comando / botão | admin, guild, definição válida | 0 | Render(primeira tela) | nenhuma | send efêmero | embed de erro | uma sessão por id de interação |
| `ACTIVE → ACTIVE` (resposta) | componente / submit de modal | `expected_revision == revision`; `event_id` inédito; parse ok | +1 | Render(próxima) ou ShowError | nenhuma | edit / replace | tela de erro mantém revisão antiga | replay do mesmo `event_id` → no-op, redesenha atual |
| `ACTIVE → ACTIVE` (voltar) | Back | existe passo anterior | +1 | Render(anterior com resposta antiga) | nenhuma | edit | — | idem |
| `ACTIVE → CANCELLED` | DiscardConfirmed | status ACTIVE | +1 | Finalize(descartado) | nenhuma | tira componentes | — | idem |
| `ACTIVE → COMMITTING` | ReviewConfirmed | tudo obrigatório respondido | +1 | Commit(sessão) | FeatureModule.commit | defer | ver FAILED | segundo confirm rejeitado (status ≠ ACTIVE) |
| `COMMITTING → COMPLETED` | commit voltou | — | +1 | Finalize(enabled/edited/added…) + evento de auditoria | feito | edita embed final | edit falha → followup | — |
| `COMMITTING → FAILED` | commit lançou exceção | — | +1 | Finalize(erro) | nada garantido; o módulo reporta o que escreveu | embed de erro | log com id da sessão | tentar de novo = nova sessão |
| `ACTIVE → EXPIRED` | timeout / restart | — | +1 | Finalize(expirado) | nenhuma | tira componentes (se alcançável) | melhor esforço | — |

Sessão `COMPLETED`, `CANCELLED`, `FAILED` ou `EXPIRED` rejeita todo evento que mudaria
estado e responde com "este form já fechou".

### 4.6 Projetar para ambiente hostil

Assuma: clique duplo, clique em mensagem velha, restart no meio do form, edição no
Discord falhando, escrita no Mongo falhando, handler lento. Mecanismos mínimos,
justificados por falhas que este código realmente tem:

| Garantia | Mecanismo | Justificado por |
|---|---|---|
| O mesmo evento lógico é aplicado uma vez | `event_id` = id da interação, lembrado por sessão (últimos N) | clique duplo no Done do card / Confirm da revisão (§13) |
| Uma tela velha não altera uma sessão mais nova | `expected_revision` dentro do `custom_id` (`k:<sessão>:<rev>:<ação>`), rejeitado se `< revision` | LayoutViews redesenhadas com os mesmos `custom_id`s (§13) |
| Dois eventos da mesma sessão nunca se cruzam | `asyncio.Lock` por sessão no adapter | discord.py despacha cada interação como task própria |
| Sessão fechada nunca muda | checagem de status no `decide` | Confirm depois que `_finish` começou |
| O event loop nunca bloqueia | todo I/O síncrono via `asyncio.to_thread` ou cliente async | `time.sleep(15)` no loop (§13, confirmado) |
| Expiração é visível | `on_timeout` → evento `Expired` → Finalize | morte silenciosa em 30 min (§13, confirmado) |
| Painéis de gerenciamento concorrentes não se sobrescrevem | campo `revision` no documento com `$inc`, ou operadores de array (`$push`/`$pull`) para listas de itens | `$set` do documento inteiro a partir de snapshot em memória (§13) |

Não justificado hoje: locks distribuídos, log de eventos, exactly-once com o Discord,
concorrência otimista em mensagens do Discord, coordenação multi-worker (processo único
`Bot`, `app/bot.py:15`, um serviço no `docker-compose.yml`).

### 4.7 Modelo de extensão

Extensões **podem**:
- registrar um **tipo de passo** (`render` + `parse`), um **validator**, um **transform**,
  um **formatter**, um **tipo de seção de card**;
- fornecer um **FeatureModule** para uma chave de comando (mapeamento de documento, commit,
  reações de ciclo de vida, resumo);
- declarar dados que precisam pré-carregados (`needs`), para o adapter buscar antes do
  `decide`.

Extensões **não podem**:
- alterar uma `FormSession` (recebem respostas, devolvem valores ou efeitos);
- chamar o Discord (devolvem fragmentos de `Screen` ou efeitos; o adapter desenha);
- ler o documento de outra feature;
- fazer branch pela chave do comando dentro de código de plataforma (a plataforma nunca
  passa a chave para tipos de passo ou validators; passa a spec do passo e o contexto da
  sessão).

Propriedade a garantir: form novo = um YAML + um `FeatureModule` (só se a persistência não
for o documento genérico) + testes. Nenhuma edição no motor.

### 4.8 Contrato do YAML

O YAML **deve** expressar: metadados e versão do form; passos em ordem com `kind`,
`key`, textos (dois idiomas) e spec do tipo (opções, selects, campos, seções, designs);
`required`, `unique`, `max_length`, `condition` (só igualdade/inclusão sobre resposta
anterior); referências por nome a validators, transforms, formatters, tipos de seção;
defaults; composição (`items: {min, max, unique_by, steps}`).

O YAML **não deve** expressar: caminhos de atributo em objetos de runtime
(`from: interaction, attr: guild.name` → trocar por um conjunto fechado de valores de
contexto nomeados, ex. `context: server_name`); regras de reset que chamam validators
(vira opção do tipo de passo: `depends_on: month` + o validator do próprio passo);
comportamento de persistência; qualquer nome Python fora de um registro; qualquer
callable.

Pipeline: `YAML → schema (modelo tipado, campo desconhecido rejeitado) → checagens
semânticas (chaves únicas, condições apontam para chaves anteriores, registros resolvem,
dois idiomas presentes, limites de composição presentes) → FormDefinition (congelada) →
registro por (command_key, versão)`. Roda no import (falha o startup) e no CI (um teste
que carrega todos os arquivos). `tests/behavioral/test_form_yaml_contracts.py:82-179` já
faz a metade semântica sobre dicts crus; é a semente desse pipeline.

### 4.9 Contrato público da plataforma

Público (quem faz feature precisa conhecer): o schema do YAML; o protocolo
`FeatureModule`; os registros (`tipos de passo`, `validators`, `transforms`,
`formatters`, `tipos de seção`) e como adicionar um; o formato `Answer` que o módulo
recebe; os helpers de teste (`run(definição, eventos) -> sessão, efeitos`; o driver
behavioral existente para checagens de adapter).

Privado (código de feature nunca toca): internos de `FormSession`, `Engine.decide`,
`SessionStore`, `DiscordAdapter`, esquema de `custom_id`, estratégia de substituição de
mensagem, transições LayoutView/embed, contabilidade de revisão e dedup, timeouts.

### 4.10 Discord como adapter

Dá para testar a maior parte do motor sem importar Discord? No alvo, sim: `Engine`,
`FormSession`, `parse` dos tipos de passo, validators, transforms, formatters e
`FeatureModule.to_document`/`from_document` não importam nada de `discord`. `render`
devolve `Screen`; só o adapter importa `discord`. O acoplamento que sobra (semântica de
ChannelSelect / RoleSelect / UserSelect, limite de 5 inputs por modal, limites de
Components V2 de 40 componentes / 4000 caracteres) vira **restrição no modelo Screen**,
verificada por testes do adapter (`tests/behavioral/contracts/test_components_v2_limits.py`
já faz isso para cards).

### 4.11 Experiência ideal de desenvolvimento

1. Escrever `app/languages/form/<chave>.yml` (validado ao salvar pelo teste de CI).
2. Reusar tipos de passo e validators; se precisar de validator novo, adicionar uma
   função pura ao registro.
3. Se o documento genérico não basta, implementar `FeatureModule` para a chave
   (`to_document`, `commit`, `summary`).
4. Escrever testes de motor: `run(defn, [Started, Answered(...), ..., ReviewConfirmed])` e
   verificar respostas finais e o payload do efeito `Commit`.
5. Opcionalmente um cenário de adapter com o harness existente.
6. Deploy.

Comparando com hoje (`docs/form-configuration.md` §7 lista): adicionar constante,
talvez estender os dicts `COMPOSITION_*`, registrar em
`ExecuteCommandButton.COMMAND_SERVICES`, escrever um módulo de service com o formato
`manager()`, saber qual dos quatro hooks (`persistence_callback` / `settings_provider` /
`lifecycle_callbacks` / `pre_finish_step`) usar, saber que `Form.cogs` pode ser lista ou
dict dependendo de quem chamou, saber que `responses` pode ter entradas escondidas, e
saber que `Form._callback` precisa ser chamado com uma interação ainda não respondida.

---

## 5. Invariantes da arquitetura

**Em uma frase:** são as leis que o sistema deveria garantir sozinho; hoje quase todas
são violadas.

| # | Invariante | Como o alvo garante | Estado atual |
|---|---|---|---|
| I1 | Uma sessão tem exatamente um objeto de estado autoritativo, com identidade e revisão crescente. | `FormSession` no `SessionStore`; todo `decide` devolve revisão nova. | **Violado.** Respostas em `Form.responses` *e* `FormStateManager.responses_by_step` *e* `responses_by_step_raw` (`form_state.py:13-14`, `form.py:68`), mais dicts `view.response` em cada view filha, mais `SummaryCardView.state`. Sem id, sem revisão. |
| I2 | Só uma transição (`decide`) muda o estado da sessão. | Sessão imutável fora do motor (dataclass congelada; o store substitui). | **Violado.** 14 pontos de mutação direta em `form.py`, `form_state.py`, `buttons.py`, `summary_card.py`, `composition.py`, `remove.py`. |
| I3 | Sessão fechada (concluída / cancelada / falha / expirada) rejeita eventos. | Checagem de status primeiro no `decide`. | **Violado.** Sem status; `_finish` pode rodar duas vezes; `ConfirmActionView.confirm` para a si mesma mas a view de origem aceita cliques até `stop()` em `discard` (`confirm_action.py:62-70`). |
| I4 | A mesma interação não produz a mesma transição duas vezes. | Dedup por `event_id` na sessão. | **Violado.** Sem dedup; discord.py agenda cada interação como task própria. |
| I5 | Uma tela desenhada na revisão *r* não altera sessão na revisão *r' > r*. | `expected_revision` no `custom_id`, checado no `decide`. | **Violado.** `custom_id`s são aleatórios (padrão do discord.py) ou determinísticos e reusados a cada redesenho (`card_done`, `card_customize_N`, `picker_back`, `design_<key>`, `prev_page`). |
| I6 | Desenhar é função pura de (definição, sessão). | `Screen = kind.render(step, session)`. | **Violado.** Desenho lê `self.step_embed` alterado no lugar (`form.py:682-684`, `1036-1040`), `_using_layout_view` (`:70`) e `interaction.message.flags` (`:1126`). |
| I7 | Código de feature não altera estado do motor. | Features recebem cópias; devolvem valores / efeitos. | **Violado.** `edit_birthday_save` lê `manager_view.edited_form_view.composition_index` (`reminders_birthdays.py:184-185`); `add_birthdays_manager_item` altera `manager_view.cogs[...]["values"]` (`:520-525`); `pre_finish_step` faz append em `self.responses` (`form.py:941`). |
| I8 | Definição inválida nunca vira form ativo. | Compilar no startup; campo desconhecido rejeitado; teste no CI. | **Parcial.** Testes de contrato existem (`tests/behavioral/test_form_yaml_contracts.py`) mas o loader não valida nada (`utils.py:75-82`); action desconhecida vira no-op silencioso (`form.py:1065-1066`); tipo de seção desconhecido é só um warn e pulado (`summary_card.py:864-870`). |
| I9 | Toda sessão aponta para uma versão da definição. | `definition_ref = (chave, versão)` na sessão. | **Violado.** Sem campo de versão; `parse_form_yaml_to_dict` é cacheado por processo (`utils.py:75`), então a definição é "o que estava no disco no primeiro load". |
| I10 | O núcleo não depende de um fluxo de produto específico. | A plataforma nunca recebe chave de comando. | **Violado.** `form.py:161`, `:694-699`, `:701-702`, `:916-941`, `:966`, `:506`; `manager.py:250-253`, `:308-311`, `:446-449`; `constants.py:110-126`. |
| I11 | Efeitos executam em ordem declarada com relato de falha por efeito. | Lista de `Effect` executada pelo adapter com log. | **Violado.** Sequências inline sem fronteira (`form.py:851-903`, `manager.py:239-293`). |
| I12 | O event loop nunca é bloqueado por I/O. | Clientes síncronos em `to_thread`; sem `time.sleep`. | **Violado.** `requests`, `pymongo`, `redis` síncronos em todo lugar; `time.sleep(15)` em `notifications_twitch.py:189` roda no `bot.loop` (`webhooks/twitch.py:25`). |

Onde não dá para garantir estruturalmente: I12 não se garante por tipos em Python; precisa
de regra de lint (proibir `requests.` e `time.sleep` fora de `app/integrations/*` e exigir
`to_thread` lá) mais uma métrica de lag do event loop.

---

## 6. Norte da arquitetura

**Em uma frase:** três camadas com dependência de mão única: features → plataforma;
adapters → plataforma + features; plataforma → nada interno.

### 6.1 Componentes e fronteiras

```
┌──────────────────────────── plataforma (sem import de discord) ──────────────────────────────┐
│                                                                                              │
│  definitions/                     engine/                          extensions/               │
│  ├─ schema.py   (YAML tipado)     ├─ session.py  (FormSession)     ├─ step_kinds/            │
│  ├─ compiler.py (→ Definition)    ├─ events.py   (tipos de Event)  ├─ validators.py          │
│  └─ registry.py (chave,versão)    ├─ effects.py  (tipos de Effect) ├─ transforms.py          │
│                                   ├─ screen.py   (modelo Screen)   ├─ formatters.py          │
│                                   ├─ engine.py   (decide)          └─ sections.py            │
│                                   └─ store.py    (SessionStore)                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                     ▲                                  ▲
                     │ Definition, Event                 │ Screen, Effect
                     │                                  │
┌────────────────────┴──────────────────────────────────┴──────────────────────────────────────┐
│  adapters/discord/                                                                            │
│  ├─ interactions.py  (Interaction → Event; codec de custom_id; lock por sessão; dedup)        │
│  ├─ renderer.py      (Screen → embed+View | LayoutView; limites CV2)                          │
│  ├─ executor.py      (Effect → response/followup/edit/replace/modal; timeout → Expired)       │
│  └─ entrypoints.py   (send_form / send_manager para cogs e botões)                            │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                     ▲
                     │ protocolo FeatureModule
┌────────────────────┴──────────────────────────────────────────────────────────────────────────┐
│  features/ (um módulo por chave de comando)                                                   │
│  block_links, default_roles, welcome_messages, notifications_twitch, notifications_youtube,   │
│  stream_elements, reminders_birthday                                                          │
│  cada um: to_document / from_document / commit / on_disable / on_item_* / summary             │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Cogs chamam só `adapters.discord.entrypoints`.

### 6.2 Quem é dono de cada estado

| Estado | Dono | Tempo de vida |
|---|---|---|
| `FormDefinition` | `definitions.registry` | processo |
| `FormSession` | `engine.store` | ≤ expiração (30 min) |
| Ids de mensagem do Discord de uma sessão | `adapters.discord` (metadado da sessão, privado do adapter) | sessão |
| Documento de configuração salvo | `features/<chave>` via `app/data` | durável |
| Cache Redis do documento | `app/services/cache` | 30 dias, invalidado na escrita |
| Contexto pré-carregado (cargos da guild, lookups externos) | adapter, por evento | um `decide` |

### 6.3 Modelo de efeitos colaterais

`Commit` é o único efeito com consequência durável. `FeatureModule.commit` devolve um
`CommitResult` listando o que escreveu (ids de documento, assinaturas externas criadas),
para que o `Finalize` e o evento de auditoria relatem exatamente o que aconteceu, e uma
falha no meio registre o que *já foi* escrito. Ações que não podem duplicar (assinar na
Twitch) ficam idempotentes dentro da feature (já é o caso do tratamento de 409,
`notifications_twitch.py:228-234`).

### 6.4 Modelo de erro

- **Erro de validação** → efeito `ShowError(chave)`; sessão não muda (mesma revisão).
- **Evento velho / duplicado** → `Rejected(motivo)`; adapter redesenha a tela atual com
  aviso curto; log em info.
- **Falha do adapter** (edit/send do Discord lança) → log com id da sessão + efeito;
  adapter tenta uma vez via followup; estado já commitado.
- **Falha de commit** → sessão `FAILED`; `Finalize(erro)`; auditoria com o `CommitResult`
  até ali.
- **Expiração** → `EXPIRED`; componentes removidos se a mensagem ainda for alcançável.

---

## 7. Experiência de desenvolvimento — antes e depois (resumo)

O fluxo está em §4.11. Reduções mensuráveis: arquivos tocados para um form simples caem
de 5–6 (YAML, constants, módulo de service, `COMMAND_SERVICES`, cog, arquivos de idioma)
para 3 (YAML, cog, arquivos de idioma); convenções escondidas que o dev precisa saber caem
de ~10 (listadas em §4.11) para 2 (schema do YAML, protocolo `FeatureModule`);
conhecimento de Discord necessário para uma feature cai a zero.

---

## 8. Arquitetura atual (como roda de verdade)

**Em uma frase:** tudo passa por dentro de `Form`, e `Form` passa por dentro de tudo.

Caminho de execução traçado, primeiro setup:

```
cog (app/cogs/.../*.py)            @keiko_command + @keiko_admin_only (app/decorators.py:14-46)
  → service.manager(interaction, guild_id)          app/services/<chave>.py
    → cache.get_cog_data_or_populate(...)            app/services/cache.py:29-43
    → send_command_form_message(interaction, key, persistence_callback?)   moderations.py:104-121
      → Form(command_key, locale)                    form.py:57-73  (View, timeout=1800, Confirm+Cancel)
        → parse_form_yaml_to_dict(key)  (cacheado)   utils.py:75-82
        → FormStateManager(list(steps))              form_state.py:10-16
      → interaction.response.send_message(embed=intro, view=form, ephemeral=True)
  usuário clica Confirm na intro
  → ConfirmButton.callback == Form._callback         buttons.py:14-21 → form.py:1165-1173
    → @_update_form_step                              form.py:96-122
        _handle_after_step → self.view.get_response() → _save_step_response   form.py:199-308
        state.advance(); pula `form`; while _should_skip_step(): advance
        self.step_embed = parse_form_dict_to_embed(step)                       embed.py:9-34
    → get_action_by_type(action)                      form.py:1044-1066
      → show_<tipo>: cria view filha, self.view = ...; _send_view / _send_layout_view / send_modal
        _send_view: fill_* do FormStateManager, senão parse_cogs_* de self.cogs; botão Voltar;
                    followup.edit_message(...) ou delete+followup.send em transições de LayoutView
  view filha coleta entrada em view.response / view.responses / view.state
  Confirm da filha → Form._callback de novo (loop)
  último passo `resume` → show_resume: Editar / Adicionar / Remover / Preview / Confirm(_finish) / Cancel   form.py:687-707
  Confirm → Form._finish                              form.py:846-903
    defer → pre_finish_step (branches de feature) → _parse_responses_to_cog
    → persistence_callback(...) | update_moderations_by_guild + insert_cog_by_guild
    → insert_cog_event → edit_original_response(embed final, view=self limpa)
```

Modo gerenciar: `send_command_manager_message` (`moderations.py:124-172`) monta uma view
`Manager` (`manager.py:56-82`); Editar → `EditCommand` (`edit.py`) → novo `Form(cogs=doc)`
filtrado para um passo → `Manager.update_command` (`manager.py:98-156`); Adicionar →
`AddItemButton` cria `Form` filtrado para o passo de composição (`buttons.py:311-324`) →
`Manager.add_item_callback` (`manager.py:316-375`); Remover → `RemoveItem` (`remove.py`)
altera o documento em memória e depois `Manager.remove_item_callback`
(`manager.py:442-491`).

Composições: `show_composition` → `FormComposition` (`composition.py`) → `Form("", locale,
sub_steps, cogs=item_ou_lista)` aninhado por item (`:95`), com atributos injetados
`all_cogs`, `composition_responses`, `prefilled_step_keys`; ao terminar, o item é
mesclado em `FormComposition.responses`, que pode ser a mesma lista do documento do
manager (`composition.py:41,49`).

### 8.1 Cinco forms reais traçados (os desvios são a prova do drift)

| Form | Formato | Desvios do caminho genérico |
|---|---|---|
| **block_links** (simples) | multi_select → options → modal → resume | Nenhum no motor. Persistência genérica. Cog lido via cache com `manager=True`. |
| **welcome_messages** (UI customizada) | channels(select) → design_select (LayoutView) → file_upload (condicional) → button → modal(5 campos, com chave + concat) → resume(preview) | `_start_preview_pregeneration` fixo nessa chave (`form.py:161`); callback do `PreviewButton` fixo em `send_welcome_message_preview` (`:702`); respostas do modal misturam campos com chave e `__concat__` (`modals.py:94-117`); `ModalValidations` divide a classe com validators sem relação. |
| **notifications_twitch** (composição + externo) | composition[channels(select) → modal(validado) → button → modal(3 inputs, enumerate)] → resume | Branch twitch em `pre_finish_step` (`form.py:919-936`); `_handle_subscription` lê `self.view.form_view.cogs` (`:949`); `disable` / `remove_item` do manager têm `if command_key` (`manager.py:250-253`, `:446-449`); máximo de itens em `constants.py:122-126`; validator faz HTTP síncrono dentro do submit do modal (`modals.py:737`). |
| **reminders_birthday** (mais customizado) | configuration_card → options(escondido, auto_confirm) → composition[user_select → configuration_card(transform)] (condicional) → resume | `persistence_callback`, `settings_provider`, quatro `lifecycle_callbacks` (`reminders_birthdays.py:36-63`); hidratação no motor trata `date`/`month` como caso especial (`form.py:506-512`, `:966-974`); feature lê `edited_form_view.composition_index` (`reminders_birthdays.py:184`); modelo de persistência próprio (`app/data/birthdays.py`) mas o manager ainda recebe um documento sintético (`birthday_manager_cog_data`) para contar itens; títulos em inglês voltados ao usuário em `to_summary_composition` (`birthdays.py:151-183`). |
| **stream_elements_commands** (o mais simples, com efeito escondido) | modal(validado) → resume | `pre_finish_step` faz append de uma resposta sintética com título fixo em inglês `"Channel ID"` (`form.py:938-941`), chamando HTTP depois do usuário confirmar. |

O docstring da regressão em
`tests/behavioral/regressions/test_manager_summary_regressions.py:15-26` registra o
incidente clássico: um helper compartilhado (`parse_settings_with_database_values`) foi
mudado para aniversários e `default_roles` perdeu silenciosamente o resumo no painel.
É a falha "mudar um form quebra outro" em uma frase: **o único lugar para pôr
comportamento é um helper que todos os forms usam.**

---

## 9. Ciclo de vida em runtime hoje (YAML → interação → estado → persistência → tela)

| Etapa | Dono hoje | Onde o estado vive | Observações |
|---|---|---|---|
| Parse do YAML | `parse_form_yaml_to_dict` (`utils.py:75-82`) | `functools.cache` (processo) | Sem schema, sem versão, só `steps`. |
| Representação em runtime | os mesmos dicts crus | compartilhados por toda instância de `Form` | `filter_steps` troca a lista (`form.py:436`); os dicts em si são compartilhados; `summary_card` faz deepcopy antes de alterar (`:690`). |
| Inicialização de estado | `Form.__init__` + `FormStateManager` | atributos da View | `cogs` pode ser `None`, dict (edição) ou lista (contexto de item de composição). |
| Desenho | `parse_form_dict_to_embed` + `show_*` + views filhas | `self.step_embed`, itens das views filhas | Embed alterado no lugar para options/roles (`form.py:682-684`, `1036-1040`). |
| Roteamento de interação | `ViewStore` do discord.py por id de mensagem + `custom_id` | discord.py | Ids determinísticos em LayoutViews; texto localizado como id em Pausar/Despausar/Desativar (`buttons.py:139,175,187`). |
| Dispatch | `_update_form_step` + `get_action_by_type` | — | Action desconhecida → no-op silencioso. |
| Validação | `CustomModal.on_submit` (`modals.py:119-131`), `SelectConfirmButton` (`select_views.py:24-33`), `OptionsView._confirm_callback` (`options.py:93-101`), `on_done` do card (`summary_card.py:881-890`) | por componente | Quatro lugares diferentes, três canais de erro diferentes (embed efêmero, texto efêmero, `channel.send`). |
| Mutação de estado | `_save_step_response` e afins | `Form.responses` (+ state manager) | Branch por tipo de action, `_upsert_response` por chave. |
| Persistência | `_finish` / `Manager.*` / `persistence_callback` / lifecycle callbacks | Mongo via `app/data`, invalidação de cache | Escrita do documento inteiro. |
| Atualização de mensagem | inline em cada `show_*` / callback | Discord | Três estratégias: edit, followup edit, delete+resend. |
| Próxima interação | nova task do discord.py | — | Sem serialização com a anterior. |

---

## 10. Mapa de quem é dono de cada estado hoje

**Em uma frase:** o mesmo dado mora em vários lugares e ninguém é dono oficial.

| Estado | Dono | Fonte da verdade? | Quem altera | Persistência | Derivado? |
|---|---|---|---|---|---|
| Lista de passos | `FormStateManager.steps_list` | YAML (cacheado) | `filter_steps` (troca a lista) | não | do YAML |
| Índice do passo | `FormStateManager.step_index` | ele mesmo | `advance`, `go_back`, `_design_select_callback` (`form.py:832`) | não | não |
| Dict do passo atual | `Form._step` | duplicata de `steps_list[step_index]` | `_update_form_step`, `_go_back`, `_design_select_callback` | não | deveria ser |
| Respostas (exibição) | `Form.responses`, lista de `{key,title,value,style,hidden,_raw_value}` | **ambíguo** com a linha abaixo | `_upsert_response`, `pop()`, `update_resume`, `pre_finish_step`, `_ensure_composition_response`, `FormComposition._apply_prefilled_fields` | não | mistura valor+rótulo+estilo |
| Respostas (navegação) | `FormStateManager.responses_by_step{,_raw}` | **ambíguo** com a linha acima | `save_response` | não | não |
| Resposta anterior para refill | `FormStateManager._previous_response{,_raw}` | transitório | `go_back`, `clear_previous_response` | não | derivado |
| Seleção no componente | `view.response` / `view.responses` / `SummaryCardView.state` | por view filha | callbacks de botão/select, `fill_*`, `parse_cogs_*`, `set_defaults`, `update_state` | não | vira resposta no confirm |
| Snapshot da config salva | `Form.cogs`, `Manager.cogs`, `FormComposition.cogs`, `RemoveItem.cogs` | o Mongo é | `remove.py:117`, `manager.py:337`, `composition.py:41,49`, `add_birthdays_manager_item` | Mongo (`$set` inteiro) | fica velho depois de qualquer outra escrita |
| Modo de desenho | `Form._using_layout_view` + `interaction.message.flags.components_v2` | nenhum dos dois | `_transition_from_layout_view`, `_send_layout_view` | não | deveria vir do tipo do passo |
| Task de preview | `Form._preview_task` | ele mesmo | `_start_preview_pregeneration` | não | específico de feature |
| Ligações entre views | `parent_view.edited_form_view`, `parent_view.form_view`, `parent_view._original_embed`, `Form.after_callback`, `Form.persistence_callback`, `composition_index`, `prefilled_step_keys`, `prefilled_composition_fields`, `all_cogs`, `composition_responses` | setados por outros objetos via injeção de atributo | `buttons.py:124-127,318-322`, `edit.py:155-158,169`, `composition.py:96-98,108,142` | não | estado implícito |
| Cache Redis do cog | `cache.py` | o Mongo é | escritas via `services/cogs.py` | TTL 30 dias | derivado |
| Flags de moderations | coleção `moderations` | ela mesma (feature ligada/pausada) | `update_moderations_by_guild`, pause/unpause | Mongo | pode divergir do documento do cog (`_finish` escreve os dois, sem atomicidade) |

Combinações inválidas alcançáveis hoje: `moderations[chave]=True` sem documento de cog
(insert do cog falha depois do update de moderations, `form.py:860-865`); um `Form` cujas
`responses` ainda têm respostas de passos atrás do cursor (voltar por cima de um passo
com várias respostas, `form.py:1068-1082`); um `Manager.cogs` que não bate mais com o
Mongo (outro painel escreveu); `enabled=False` no documento do cog enquanto o
`pause_handler` do manager foi montado com `cogs` velho (`manager.py:158-168`).

---

## 11. Análise de gaps

**Em uma frase:** dezesseis lacunas; três são estruturais e críticas, uma é crítica e
barata.

| # | Propriedade desejada | Comportamento atual | Evidência | Consequência | Gravidade | Escopo |
|---|---|---|---|---|---|---|
| G1 | Um estado de sessão autoritativo com identidade e revisão | Duas stores de resposta + dicts por view, sem id/revisão | `form.py:68`, `form_state.py:13-16`, `summary_card.py:65` | estado velho; impossível deduplicar ou rastrear | **Crítico** | Estrutural |
| G2 | Estado muda só por transição | 14 pontos de mutação direta, inclusive em código de feature | §5 I2, I7 | atualizações erradas; features quebram o motor | **Crítico** | Estrutural |
| G3 | Núcleo determinístico testável sem Discord | O motor *é* uma View; todo teste precisa do harness | `form.py:47`, `tests/behavioral/harness/` | feedback lento, pouca cobertura de casos de borda (voltar, clique duplo) | **Alto** | Estrutural |
| G4 | Tratamento de evento idempotente e seguro contra tela velha | nenhum | §13 | avanço duplo, commit duplo, clique velho roteado para view nova | **Alto** | Transversal |
| G5 | Ciclo de vida explícito | booleanos e ausência (`hasattr(self, "view")`, `_using_layout_view`, `hasattr(self, "after_callback")`) | `form.py:200,155,1125` | sessão fechada continua aceitando entrada; expiração invisível | **Alto** | Transversal |
| G6 | Lógica de feature fora do motor | 9 branches / lookups por chave de comando no motor + constants | §5 I10 | cirurgia espalhada por feature; regressões em outros forms | **Alto** | Transversal |
| G7 | Efeitos com fronteira de falha | sequências inline, exceções engolidas | `form.py:851-903`, `manager.py:151-154,290-293` | estado misto da guild; "pensando…" para sempre em erro | **Alto** | Transversal |
| G8 | Event loop nunca bloqueado | `requests`/pymongo/redis síncronos em handlers; `time.sleep(15)` | `notifications_twitch.py:189`, `modals.py:737`, `form.py:940`, `welcome_messages.py:188-192` | interaction-failed para *todos* os usuários durante o bloqueio | **Crítico** (barato) | Transversal |
| G9 | Definições validadas e versionadas | dicts crus, cacheados, action desconhecida vira no-op | `utils.py:75-82`, `form.py:1065` | erro de digitação vira passo morto; sem caminho de upgrade para docs salvos | **Médio** | Local |
| G10 | Tela derivada do estado | embed alterado no lugar, flag de modo de desenho | `form.py:682-684,1036-1040,1124-1129` | mensagens mostrando estado velho | **Médio** | Transversal |
| G11 | Identidade estável de componente | ids aleatórios (padrão) ou determinísticos reusados; texto localizado como id | `summary_card.py:103-143`, `buttons.py:139,175,187` | clique velho roteado errado; palavra de confirmação depende do idioma do botão | **Médio** | Local |
| G12 | Persistência segura sob concorrência para documentos compartilhados | `$set` do documento inteiro a partir de snapshot em memória | `manager.py:337-338,454`, `data/cogs.py:43-47` | itens perdidos quando dois painéis escrevem | **Médio** | Local |
| G13 | Um caminho de validação com um canal de erro | quatro pontos de validação, três canais | §9 | UX inconsistente; `channel.send` (não efêmero) no erro de opção obrigatória (`options.py:96-100`) | **Médio** | Transversal |
| G14 | Formato salvo independente da apresentação | `{style, values}` nos documentos; `hidden` nos itens | `form.py:411-422`, `birthdays.py:151-183` | quatro helpers de desembrulhar; migrações acopladas à UI | **Médio** | Estrutural |
| G15 | Composição como tipo de passo de primeira classe | `Form("")` aninhado com injeção de atributo e aliasing | `composition.py:95-98,41,49` | caminho mais frágil; editar/adicionar/remover são todos diferentes | **Alto** | Estrutural |
| G16 | Observabilidade por sessão | logs têm interação/guild/usuário; sem id de sessão, revisão, passo, evento | `app/logger.py:219-247`, `form.py:75-88` | impossível reconstruir um ciclo de vida de form | **Médio** | Transversal |

---

## 12. Decisões que valem ser revistas

**Em uma frase:** para cada decisão de hoje, "escolheríamos de novo?" e o que pôr no lugar.

| Decisão atual | Por que provavelmente existe | Escolheria hoje? | Alternativa | Dificuldade de migrar |
|---|---|---|---|---|
| `Form` herda de `discord.ui.View` | Jeito mais rápido de ter callbacks no discord.py | **Não** | Objeto de sessão + adapter; views são renderers descartáveis | M (Etapas 2–4) |
| YAML por comando só com `steps:`, idiomas inline | Simples; texto junto da estrutura | **Sim** (adicionar `version`, nomes de `kind`, schema) | — | S |
| Nomes de "action" dobram como nome do widget (`channels`, `modal`, `options`) | Cresceu da UI | **Quase sim**; renomear para *kinds* e fazer `select: true` virar um kind, não flag | `kind: channel_pick` etc. | S (alias no compilador) |
| Respostas como lista de `{key,title,value,style,hidden,_raw_value}` | Resumo precisava de títulos | **Não** | `Dict[step_key, Answer(raw, parts)]`; títulos/estilos resolvidos da definição na hora de desenhar | M |
| `FormStateManager` como segunda store para voltar | Adicionado depois para corrigir refill | **Não** | uma sessão; `render` lê `answers[prev_key]` | S depois do G1 |
| Documento salvo `{chave: {style, values}}` | Manager precisa formatar valores | **Não** (mas continuar lendo) | guardar valores crus; formatação da definição; migrar aos poucos via `from_document` | M (migração de dados ou leitura dupla) |
| `persistence_callback` + `settings_provider` + `lifecycle_callbacks` + `pre_finish_step` | Adicionados um por vez conforme birthdays/twitch precisaram | **Não** | um protocolo `FeatureModule` | S–M |
| Limites e chaves de composição em `app/constants.py` | Form e Manager precisavam | **Não** | `items: {max, unique_by}` no YAML | XS |
| Editar = rodar `Form` filtrado com `cogs` | Reusa um caminho | **Sim como modo**, não como `Form` com atributos injetados | `mode=edit(subconjunto)` na sessão; `from_document` semeia respostas | M |
| Composição como `Form` aninhado | Reuso | **Não** | tipo de passo de composição que roda uma *sessão filha* com ligação explícita ao pai | M–L |
| Cards LayoutView com dict `state` interno e `_render()` | Components V2 exige reconstruir a árvore | **Manter o rebuild, mover o estado** | `state` do card = resposta parcial daquele passo na sessão | S |
| Delete + resend ao trocar embed ↔ LayoutView | Discord não converte mensagem no lugar | **Sim** (restrição do Discord) | responsabilidade do adapter, com tratamento de falha | S |
| Rótulo localizado como `custom_id` em Pausar/Despausar/Desativar, reusado como palavra de confirmação | Conveniência | **Não** | ids fixos; palavra de confirmação de chave i18n | XS |
| `functools.cache` no load do YAML, sem validação | Simplicidade | **Não** | compilar no startup, falhar cedo | S |
| pymongo / requests síncronos no loop | Histórico | **Não** | `asyncio.to_thread` agora; clientes async depois | S (mecânico) |
| Views expiram em silêncio depois de 1800 s | Padrão do discord.py | **Não** | `on_timeout` → finalização como Expired | XS |
| Mensagem efêmera como guarda de identidade | Correto e barato | **Sim** | manter; adicionar checagem explícita de usuário no adapter mesmo assim (defensivo) | XS |

---

## 13. Análise de confiabilidade

**Em uma frase:** dois ghost clicks confirmados fora do motor, cinco prováveis dentro,
e nenhum mecanismo de proteção hoje.

### 13.1 Ghost clicks e interações velhas — investigado no código

**Causas confirmadas (Fato).**

- **C1 — Event loop bloqueado.** `handle_send_streamer_notification` é agendada no
  `bot.loop` (`app/webhooks/twitch.py:25`) e chama `wait_for_stream_info`, que faz até
  três `time.sleep(15)` (`app/services/notifications_twitch.py:174-189`). Com o loop
  travado, nenhuma interação em lugar nenhum é respondida na janela de 3 segundos do
  Discord, e o cliente mostra "This interaction failed", indistinguível de um ghost
  click. Mesma classe, mais curto: toda chamada `requests.*` em `app/integrations/*` roda
  no loop, inclusive dentro do submit do modal (`validate_streamer_name`,
  `modals.py:730-738`), dentro do `_finish` (`StreamElementsClient.get_channel_info`,
  `form.py:939-940`), dentro da geração de preview (`welcome_messages.py:187-192`), e
  toda chamada pymongo/redis em `app/data` e `app/services/cache.py`.
- **C2 — Expiração silenciosa e restarts.** Views de form vivem 1800 s (`form.py:71`),
  modais 300 s; nenhuma view implementa `on_timeout` (fixado por
  `tests/behavioral/contracts/test_view_timeouts.py:47`), e nenhuma view de form é
  persistente (`bot.add_view` só é usado para `GreetingsView`, `app/cogs/events.py:46`).
  Depois de expirar ou de um deploy, os botões continuam na mensagem efêmera mas o
  discord.py não tem view para despachar → interaction failed. Os *tokens* de interação
  também expiram em 15 minutos, então entre 15 e 30 minutos alguns caminhos baseados em
  followup (`followup.delete_message` do `_send_layout_view`, `form.py:1161`) só
  funcionam porque usam o token da interação *nova*; qualquer caminho que reuse um token
  antigo falha.

**Causas possíveis com evidência (Inferência forte).**

- **P1 — Dispatch concorrente, sem trava.** O discord.py agenda cada interação de
  componente como task independente; nada no motor serializa ou marca a sessão como
  "processando". Clique duplo no Done do card roda `on_done` duas vezes →
  `form._callback` duas vezes → dois `advance()` (`summary_card.py:163-165,881-890`,
  `form.py:104-117`). Clique duplo no Confirm da revisão roda `_finish` duas vezes → dois
  documentos `insert_cog_event`, duas chamadas de `persistence_callback` e duas tentativas
  de assinatura externa (`form.py:846-891`). A segunda interação então falha em
  `edit_original_response` ou na mensagem apagada → o usuário vê erro num form que "já
  funcionou".
- **P2 — `custom_id`s determinísticos reusados.** `SummaryCardView._render` reconstrói os
  botões com os mesmos ids a cada mudança de estado (`summary_card.py:101-143`), e
  `DesignSelectView`, pickers e paginação usam ids fixos. Um clique capturado contra um
  desenho anterior é roteado pelo discord.py para o handler da view *atual* com o mesmo
  id. Em cards isso significa "personalizar seção N" do estado novo; em `OptionsButton`
  (ids aleatórios) significa falha silenciosa.
- **P3 — Substituição de mensagem que engole falhas.** Transições LayoutView ↔ embed
  apagam e reenviam (`form.py:1128-1133,1161-1162`); `Manager.update_command` e
  `disable_callback` engolem o erro de delete/edit (`manager.py:151-154,290-293`). Quando
  o delete falha (token expirado, efêmero já dispensado), a mensagem velha fica com
  botões que parecem vivos ligados a uma view velha ou parada.
- **P4 — Voltar corrompe respostas.** `_go_back` remove exatamente uma entrada de
  `responses` por passo (`form.py:1072-1073,1080-1081`), mas `multi_select` produz uma
  entrada por select (`form.py:239-262`) e cards produzem várias (`:375-394`). As entradas
  que sobram alimentam `_should_skip_step`, `update_resume`, `_parse_responses_to_cog` e a
  hidratação do card (`summary_card.py:838`). Fonte direta de "atualizações erradas de
  estado" e "mensagens mostrando estado velho".
- **P5 — Documentos velhos em memória.** `Manager.cogs` é um snapshot do Redis/Mongo na
  abertura do painel; `add_item_callback` faz append nele e escreve o documento inteiro
  com `$set` (`manager.py:337-338`, `data/cogs.py:43-47`); `RemoveItem` altera no lugar
  antes de escrever (`remove.py:116-118`). Dois painéis abertos (mesmo admin duas vezes, ou
  dois admins) → o último a escrever ganha, itens somem.

**Improvável.** Várias instâncias do bot (um único `commands.Bot`, um container); Discord
reenviando interações de componente (Discord entrega cada interação uma vez; retries são
para webhooks); mutação do cache de YAML (não achei mutação no lugar dos dicts de passo;
`summary_card.py:690` faz deepcopy).

**Desconhecido.** A distribuição dessas causas em produção: o repo não tem métricas de
erro (`app/cogs/prometheus.py` existe nesta branch; a review não auditou os contadores) e
os logs não estão no repo. Ver §26.

### 13.2 Cenários de concorrência e consistência (comportamento atual)

| Cenário | O que acontece hoje | Evidência |
|---|---|---|
| Clique A depois B (rápido, em sequência) | Funciona se o edit de A chegar antes do dispatch de B; senão o `interaction.message` de B é a mensagem pré-A e o callback de B edita de novo (desenho duplo) ou falha (`40060`/apagada). | `form.py:1094-1114`, task por interação no discord.py |
| Clique A duas vezes | Duas tasks. Botão de opção: liga e desliga (desmarca). Confirm/Done: avanço duplo ou `_finish` duplo (P1). | `buttons.py:57-73`, P1 |
| Duas interações simultâneas no mesmo form | Sem lock; mutação intercalada de `Form.responses`, `step_index`. | G1/G2 |
| Interação numa mensagem velha | Ids aleatórios → interaction failed; ids determinísticos → roteado para o handler atual (P2). | P2 |
| Restart do processo no meio do form | Todas as sessões somem em silêncio; botões ficam (C2). | C2 |
| Várias instâncias do bot | Não é modo de deploy hoje. | `docker-compose.yml` |
| Banco ok, Discord falha (edit final) | `_finish` cai para `followup.send`; `Manager.*` engole; estado salvo, usuário pode ver "pensando…" se os dois falharem. | `form.py:900-903`, `manager.py:151-154` |
| Discord ok, banco falha | `_finish`: moderations pode ficar `True` sem documento de cog (insert vem depois de moderations); usuário só vê log de `on_error`, mensagem fica em defer. `disable`: unsubscribe feito, depois delete falha → assinaturas somem, config fica. | `form.py:860-865`, `manager.py:250-262` |
| API externa falha em `pre_finish_step` | Exceção sobe → `on_error` → nada salvo, mensagem em defer nunca resolvida. | `form.py:851,905-941` |

Mecanismos justificados por esses cenários: lock por sessão, dedup por `event_id`,
`expected_revision`, status explícito, finalização em `on_timeout`, destravar o loop,
`revision` no documento ou operadores de array para listas de itens. **Não**
justificados: CAS em mensagens do Discord, log de transações, locks distribuídos, retries
com backoff além de um fallback de followup, jobs de reconciliação.

### 13.3 Fronteiras de efeito colateral

`Form._finish` (`form.py:846-903`), na ordem, e o que uma falha em cada passo deixa:

| N | Operação | Se N falha | Estado com N-1 ok e N falho |
|---|---|---|---|
| 1 | `interaction.response.defer(ephemeral=True)` | raro | nada |
| 2 | `pre_finish_step` → assinar Twitch/YouTube ou HTTP do StreamElements (síncrono) | exceção | assinatura externa pode existir; nada salvo; mensagem em defer para sempre |
| 3 | `_parse_responses_to_cog` | puro | — |
| 4a | `persistence_callback` (birthday: upsert de config + API de lembrete + itens) | exceção no meio | estado parcial de aniversário (config salva, lembretes pela metade) |
| 4b | `update_moderations_by_guild(True)` | exceção | assinatura existe, nada mais |
| 5 | `insert_cog_by_guild` | exceção | **moderations ligado, sem config** → manager mostra o form de setup de novo; `/setup` mostra ligado |
| 6 | `insert_cog_event` | exceção | config salva, sem entrada de histórico; usuário nunca vê o embed final |
| 7 | `edit_original_response` | exceção | cai para followup (ok) |

`Manager.disable_callback` (`manager.py:239-293`): unsubscribe externo → disable custom →
`unpause` → `delete_cog` → evento → send → edit. Falha depois do unsubscribe deixa uma
feature configurada sem assinatura.

`Manager.add_item_callback` (`manager.py:316-375`): checagem de duplicata em snapshot
velho → `pre_finish_step` (externo) → append + `$set` inteiro → evento → edit. Janela de
perda de atualização entre o snapshot e a escrita.

---

## 14. Review do YAML

**Em uma frase:** o YAML é boa definição, mas acumulou pedaços de comportamento e
palavras com mais de um significado.

O que o YAML é hoje: **definição** (passos, textos, opções, campos, selects, designs,
seções), **comportamento leve** (`condition`, `required`, `unique`, `auto_confirm`,
`reset-on-change`, `template-vars`, `response_transform`, `validation`), **configuração de
UI** (`style`, `select: true`, `emoji`, `header.thumbnail` por nome de constante,
`multiline`, `enumerate`, `max_length`) e **algumas fugas reflexivas**
(`from: interaction, attr: guild.name`; `thumbnail: BIRTHDAY_GIF` resolvido com
`getattr(KeikoIcons, ...)`, `summary_card.py:798-799`).

Ambiguidades semânticas encontradas:

- `style` significa (a) como formatar valor em resumos, (b) qual componente do Discord
  usar em `selects[]`, (c) uma tag persistida no documento, (d) `composition`.
- `key` em campo de `configuration_card` às vezes é chave de estado, às vezes chave
  persistida, às vezes parte escondida (`month`/`day` vs `date`).
- `hidden: true` num passo quer dizer "fora do picker de edição e fora da lista da intro"
  (`utils.py:121-126`, `form.py:448`); num campo quer dizer "não aparece nos resumos".
- `required` num passo vs num card (`required: [chaves]`) vs num campo de modal.
- `condition.not_in` com três grafias de false porque o passo de opções às vezes guarda
  `True`/`False` como string (`reminders_birthday.yml:288-292`).
- `select: true` transforma `channels`/`roles` em outro componente; `available_roles` é
  uma action separada que também honra `select`.
- Actions `month_select` e `summary_card` estão registradas mas mortas
  (`docs/form-configuration.md` §3).

Validação hoje: nenhuma no load; testes de contrato cobrem despacho, validators,
transforms, styles, idiomas, ordem de condições, tipos de seção
(`tests/behavioral/test_form_yaml_contracts.py`). Sem rejeição de campo desconhecido, sem
tipagem, sem declaração de defaults, sem versão, sem distinção CI vs startup (os testes
são o check de CI; o startup confia no arquivo).

Contrato alvo (§4.8) em uma frase: **o YAML declara estrutura, textos, restrições e
*nomes* de comportamentos registrados; nunca contém expressão, caminho para objeto de
runtime, ou identificador Python fora de um registro.**

---

## 15. Review do modelo de extensão

**Em uma frase:** existem cinco jeitos de uma feature entrar no motor; o mais usado é o
invisível.

Do mais ao menos disciplinado:

1. **Registros por nome** — `ModalValidations` (getattr, `modals.py:220-229`),
   `RESPONSE_TRANSFORMS`, `format_values_by_style`, `SECTION_TYPES`, tipos do
   `MultiSelectView`. Formato bom; granularidade errada em alguns pontos (validators são
   métodos de uma classe que também segura `cogs`; `validate_date` lê
   `cogs["responses"]`, `modals.py:765-772`).
2. **Parâmetros de hook** — `persistence_callback`, `settings_provider`,
   `lifecycle_callbacks` (`moderations.py:104-133`). Boa ideia, mas recebem as *views*
   (`manager_view`, `edited_form_view`) e por isso podem ler e alterar internos do motor
   (`reminders_birthdays.py:184-185,520-525`).
3. **Constantes consultadas pelo motor** — `COMPOSITION_COMMANDS_LIST`,
   `COMPOSITION_MAX_LENGTH`, `COMMAND_KEY_TO_COMPOSITION_KEY`,
   `ExecuteCommandButton.COMMAND_SERVICES`. Configuração que pertence ao YAML ou a um
   registro de feature.
4. **Branches dentro do motor** — `pre_finish_step`, `_start_preview_pregeneration`,
   split de data em `parse_cogs_to_modal`, mês em `_parse_cogs_to_select`, unsubscribe
   twitch/youtube em `manager.py`. Documentados como exceções em
   `docs/form-configuration.md` §7; continuam vivos.
5. **Injeção de atributo entre views** — o protocolo não documentado
   (`edited_form_view`, `form_view`, `composition_index`, `prefilled_*`, `all_cogs`,
   `composition_responses`, `_original_embed`, `after_callback`). É o mecanismo de
   extensão real de hoje e é invisível.

O alvo substitui 2–5 por um único protocolo `FeatureModule` e mantém 1 como registros de
funções puras.

---

## 16. Alternativas de arquitetura

**Em uma frase:** endurecer (A), extrair o motor (B) ou virar plataforma de workflow
persistida (C).

### Opção A — Endurecer a arquitetura existente

Manter `Form(discord.ui.View)`, `FormStateManager`, `Manager`, as classes de view e os
hooks. Adicionar: trava de processamento e dedup por `event_id` em `Form._callback`;
finalização em `on_timeout` em toda view; `to_thread` para I/O síncrono e remoção do
`time.sleep`; schema tipado do YAML validado no startup; `custom_id`s fixos; correção do
`_go_back`; operadores de array ou revisão de documento para listas de itens; id de
sessão nos logs; mover as exceções do §7 para `lifecycle_callbacks`.

- Fica: tudo que é estrutural. Muda: ~15 edições locais.
- Migração: trivial, sem compatibilidade a cuidar.
- Riscos: baixo por mudança; a classe "mudar helper compartilhado quebra outro form"
  continua, porque comportamento ainda não tem outro lugar para ir.
- Bugs reduzidos: C1, C2, P1 (parte), P3 (parte), P4, P5. Não G1–G3, G6, G15.
- Velocidade futura: igual; cada feature nova ainda toca o motor.

### Opção B — Introduzir um motor de form dedicado (recomendada)

Extrair `FormSession`, `Event`, `Effect`, `Screen`, `Engine.decide` e `SessionStore` como
Python puro; transformar as views existentes em renderers de `Screen` e os
`show_*`/`_finish`/callbacks do `Manager` num adapter que converte interações em eventos e
executa efeitos; substituir hooks e constants por `FeatureModule`; manter YAML com schema
e versão; migrar um form por vez atrás dos mesmos pontos de entrada
`send_command_form_message` / `send_command_manager_message`.

- Fica: os YAMLs (com schema aditivo), as views de componente (como renderers),
  `app/data` e services, o harness behavioral (como testes de adapter), o formato do
  documento salvo (lido via `from_document`, escrito igual no início).
- Muda: `Form`, `FormStateManager`, `Manager`, `EditCommand`, `RemoveItem`,
  `FormComposition` são substituídos por motor + adapter ao longo das etapas.
- Migração: incremental; os dois motores coexistem atrás dos pontos de entrada,
  selecionados por chave de comando.
- Riscos: médio; a transição LayoutView ↔ embed e o modo de composição são os dois
  lugares onde o adapter precisa reproduzir exatamente a coreografia atual do Discord.
- Bugs reduzidos: tudo de §13 por construção, mais G1–G7, G10–G16.
- Velocidade futura: form novo = YAML + `FeatureModule` opcional.

### Opção C — Redesenhar como plataforma de workflow / máquina de estados persistida

Opção B mais: sessões persistidas em Mongo/Redis e retomáveis depois de restart, views
persistentes (`timeout=None` + `bot.add_view`) com sessões reconstruídas do storage, log
append-only de transições por sessão, grafo genérico de workflow (branches, passos
paralelos) no YAML.

- Fica / muda: como B, mais um modelo de storage para sessões e um registro de
  mensagens.
- Migração: L–XL; toda mensagem vira um ponteiro durável.
- Riscos: alto — mensagens efêmeras não podem ser buscadas depois de restart, então
  "retomar" vira "mandar mensagem nova"; views persistentes exigem `custom_id` estável
  em todo componente inclusive modais; a generalidade de grafo não tem consumidor entre
  os sete forms.
- Bugs reduzidos: os de B mais C2-depois-de-restart (parcialmente).
- Velocidade futura: igual a B para os forms que a Keiko tem; mais lenta para o resto
  por causa da maquinaria extra.

---

## 17. Comparação lado a lado

| Dimensão | Atual | Opção A | Opção B | Opção C |
|---|---|---|---|---|
| Coerência arquitetural | Baixa (View = motor) | Baixa+ | Alta | Alta |
| Segurança de estado | Nenhuma | Trava + dedup | Sessão + revisão + status | Idem + durável |
| Segurança do YAML | Só testes | Schema no startup | Schema + versão + compilador | Idem + semântica de grafo |
| Modelo de extensão | 5 mecanismos | 3 (hooks + registros + constants) | 1 protocolo + registros | Idem |
| Testes | Só harness | Harness + alguns unitários | Testes puros do motor + harness para o adapter | Idem + testes de storage |
| Segurança sob concorrência | Nenhuma | Trava por form | Lock + dedup + revisão | Idem + dedup durável |
| Risco de migração | — | Baixo | Médio (incremental) | Alto |
| Esforço | — | S–M total | M–L total, em etapas | XL |
| Velocidade futura | Baixa | Baixa | Alta | Média |
| Depuração | Id de interação + guild | + id de sessão | Trace completo da sessão | Completo + histórico |
| Sobrevive a restart | Não | Não (expiração visível) | Não (expiração visível) | Parcialmente |

---

## 18. Arquitetura alvo recomendada (Opção B em detalhe)

**Em uma frase:** tipos puros no centro, um adapter para o Discord, um protocolo para
as features.

### 18.1 Tipos centrais (pseudo-código, sem import de discord)

```python
# app/settings/form.py
@dataclass(frozen=True)
class StepDef:
    key: str
    kind: str                      # "text" | "single_choice" | "channel_pick" | "role_pick" | ...
    spec: Mapping[str, Any]        # específico do kind, validado pelo schema do kind
    copy: Copy                     # título/descrição/rodapé por locale
    required: bool = False
    condition: Condition | None = None   # {key, one_of | not_one_of}

@dataclass(frozen=True)
class FormDefinition:
    key: str
    version: int
    steps: tuple[StepDef, ...]
    items: ItemsSpec | None        # composição: max, unique_by, steps


# app/settings/session.py
class Status(Enum): ACTIVE, AWAITING, COMMITTING, COMPLETED, CANCELLED, FAILED, EXPIRED

@dataclass(frozen=True)
class Answer:
    raw: Any                       # valor(es) de máquina
    parts: Mapping[str, Any] = {}  # passos com várias partes (cards, modais com chave)

@dataclass(frozen=True)
class FormSession:
    id: str
    definition: tuple[str, int]
    mode: Mode                     # Setup | Edit(keys) | AddItem | EditItem(index)
    origin: Origin                 # guild_id, user_id, locale
    status: Status
    cursor: str | None             # chave do passo atual
    answers: Mapping[str, Answer]
    revision: int
    seen_events: tuple[str, ...]   # últimos N ids de interação
    expires_at: datetime


# app/settings/form.py
def decide(defn: FormDefinition, session: FormSession, event: Event, ctx: Context) -> Decision:
    if event.event_id in session.seen_events:
        return Decision(session, [Rerender()])
    if event.expected_revision is not None and event.expected_revision != session.revision:
        return Decision(session, [Rerender(notice="stale")])
    if session.status not in (ACTIVE, AWAITING):
        return Decision(session, [Rerender(notice="closed")])
    ...
    match event:
        case Answered(step_key, payload):
            step = defn.step(step_key)
            parsed = KINDS[step.kind].parse(step, payload, ctx)
            if isinstance(parsed, ValidationError):
                return Decision(session, [ShowError(parsed.key)])
            s = session.with_answer(step_key, parsed).advance(defn)
            return Decision(s, [render(defn, s)])
        case Back():
            s = session.back(defn)
            return Decision(s, [render(defn, s)])
        case ReviewConfirmed():
            s = session.with_status(COMMITTING)
            return Decision(s, [Commit(s)])
        ...
```

### 18.2 Adapter (dono de tudo que é Discord)

```python
# app/settings/discord/executor.py
async def handle(interaction, session_id):
    async with locks[session_id]:
        session = store.get(session_id)
        event = to_event(interaction, session)          # lê custom_id "k:<sid>:<rev>:<ação>"
        ctx = await prefetch(defn, session, event)       # cargos da guild, lookups de feature, em to_thread
        decision = decide(defn, session, event, ctx)
        store.put(decision.session)
        for effect in decision.effects:
            await execute(effect, interaction, decision.session)   # render/modal/erro/commit/finalize
```

`execute(Commit)` chama `FeatureModule.commit(session, ctx)` em `to_thread` quando o
módulo é síncrono, depois produz `Finalize(result)`; uma exceção move a sessão para
`FAILED` e desenha a tela de erro com o id da sessão.

### 18.3 Protocolo de feature

```python
class FeatureModule(Protocol):
    key: str
    def to_document(self, answers: Mapping[str, Answer], origin: Origin) -> dict: ...
    def from_document(self, doc: dict) -> Mapping[str, Answer]: ...
    async def commit(self, session: FormSession, ctx: Context) -> CommitResult: ...
    async def on_disable(self, doc: dict, ctx: Context) -> None: ...
    async def on_item_added(self, doc: dict, item: Mapping[str, Answer], ctx) -> None: ...
    async def on_item_removed(self, doc: dict, item: Mapping[str, Answer], ctx) -> None: ...
    def summary(self, doc: dict, locale: str) -> list[SummaryLine]: ...

DEFAULT = GenericCogFeature()   # o caminho insert_cog_by_guild de hoje; usado quando a chave não tem módulo
```

### 18.4 Fronteiras mantidas de hoje

`app/data/*`, `app/services/cache.py`, `app/services/{dates,transforms,compositions}.py`,
as classes de componente em `app/components/` e `app/views/summary_card.py` (como
renderers), a pilha de i18n, o harness behavioral.

---

## 19. Plano de migração

**Em uma frase:** sete etapas, cada uma entregável sozinha, com os sete forms funcionando
no fim de cada uma.

### Etapa 0 — Segurança e visibilidade

- **Objetivo:** parar as duas causas confirmadas de ghost click e tornar dispatch duplo
  inofensivo; pôr id de sessão em toda linha de log.
- **Problema resolvido:** C1, C2, P1, parte de P3.
- **Mudança:** `asyncio.sleep` / `to_thread` para `wait_for_stream_info` e os clientes de
  `integrations` chamados de handlers; trava `_busy` + conjunto de `interaction.id` em
  `Form._callback`, `Manager.*` e `SummaryCardView.interaction_check` (rejeitar com aviso
  efêmero curto); `on_timeout` em `Form`, `Manager`, `OptionsView`, `SummaryCardView`,
  views de select, removendo componentes e editando a última mensagem conhecida; um
  `session_id` (uuid) em `Form`/`Manager` incluído em `ErrorContext.extra` e nos logs de
  info de `_finish` / ações de ciclo de vida.
- **Arquivos:** `notifications_twitch.py`, pontos de chamada de `app/integrations/*`,
  `form.py`, `manager.py`, `summary_card.py`, `select_views.py`, `options.py`,
  `logger.py`.
- **Compatibilidade:** nada afetado.
- **Testes:** cenários no harness para clique duplo em Done/Confirm (não pode avançar
  nem persistir duas vezes), finalização na expiração (o teste de contrato hoje fixa
  "sem comportamento custom de timeout"; atualizar de propósito), uma asserção de lag do
  event loop no caminho do webhook da Twitch.
- **Risco:** Baixo. **Esforço:** S. **Ganho:** remove as falhas mais visíveis.
- **Dependências:** nenhuma. **Rollback:** reverter por arquivo.

### Etapa 1 — Tornar contratos explícitos

- **Objetivo:** definições tipadas, validadas no startup e no CI, versionadas.
- **Problema resolvido:** G9, parte de G14; habilita a Etapa 3.
- **Mudança:** schema tipado (dataclasses ou pydantic) para passos e spec de cada kind;
  compilador `load_definition(key) -> FormDefinition` com rejeição de campo desconhecido
  e as checagens semânticas hoje em `test_form_yaml_contracts.py`; `version: 1` em cada
  YAML; limites de composição movidos para o YAML (`items: {max, unique_by}`) e lidos da
  definição (constants viram derivadas); alias dos nomes antigos de action para kinds.
- **Arquivos:** novo `app/settings/form.py`, `app/languages/form/*.yml`,
  `utils.py:parse_form_yaml_to_dict` (devolve a visão crua da definição compilada para
  quem ainda usa), `constants.py`.
- **Compatibilidade:** quem chama continua recebendo dicts de passo via um shim
  `to_legacy_steps()`.
- **Testes:** testes de schema (arquivos válidos/inválidos), teste de CI carregando toda
  definição, testes de contrato existentes apontados para o compilador.
- **Risco:** Baixo–Médio. **Esforço:** S–M. **Ganho:** erro de digitação e drift falham
  cedo.
- **Dependências:** nenhuma. **Rollback:** manter o loader cru; o shim isola quem chama.

### Etapa 2 — Estabelecer dono do estado

- **Objetivo:** uma `FormSession` com `answers`, `cursor`, `status`, `revision`; toda
  mutação por métodos da sessão.
- **Problema resolvido:** G1, G2, G5, P4, P5 (para listas de itens).
- **Mudança:** introduzir `FormSession` e `SessionStore`; `Form` guarda um `session_id` e
  delega `responses`/`state` para a sessão (`responses` vira uma visão legada calculada:
  `[{key, title, value, style, hidden, _raw_value}]` derivada de respostas + definição);
  `_go_back` remove respostas por chave de passo; `Manager` recarrega o documento antes
  de adicionar/remover e escreve listas de itens com `$push`/`$pull` (ou checagem de
  `revision` no documento); `FormComposition` vira sessão filha com id do pai explícito.
- **Arquivos:** novo `app/settings/session.py`, `store.py`; `form.py`, `form_state.py`
  (apagado no fim), `manager.py`, `composition.py`, `remove.py`, `data/cogs.py`.
- **Compatibilidade:** formato do documento salvo inalterado; hooks continuam recebendo a
  mesma lista `responses` (calculada).
- **Testes:** testes puros da sessão (avançar/voltar/pular/substituir resposta/status);
  cenários de regressão para voltar por cima de multi_select e cards; contratos de
  consumidores.
- **Risco:** Médio. **Esforço:** M. **Ganho:** fecha a classe de bugs de estado velho.
- **Dependências:** Etapa 1 (definição para derivar a visão legada).
- **Rollback:** a `responses` calculada mantém o formato externo; reverter por arquivo.

### Etapa 3 — Extrair o motor de form

- **Objetivo:** `decide(definição, sessão, evento) -> (sessão, efeitos)` puro; registro de
  tipos de passo com `render`/`parse`; modelo `Screen`.
- **Problema resolvido:** G3, G4, G6 (lado do motor), G10, G13.
- **Mudança:** mover `_update_form_step`, `_should_skip_step`, `_save_step_response`,
  `_transform_step_response`, `_save_summary_card_response`, `_go_back`, os pontos de
  validação e `_parse_responses_to_cog` para funções do motor; cada `show_*` vira
  `KIND.render(step, session) -> Screen`; o `get_response()` de cada view filha vira
  `KIND.parse(step, payload)`; `Form` vira uma fachada fina de compatibilidade que chama o
  adapter.
- **Arquivos:** novo `app/settings/form.py`, `events.py`, `effects.py`, `screen.py`,
  `kinds/*.py`; `form.py` encolhe para a fachada.
- **Compatibilidade:** pontos de entrada inalterados; forms entram por chave de comando
  (`ENGINE_V2_KEYS`), começando por `block_links`.
- **Testes:** testes de tabela do motor por kind; testes de propriedade (idempotência em
  `event_id`, revisão monotônica, sessão fechada rejeita); cenários do harness para o form
  migrado sem mudança.
- **Risco:** Médio. **Esforço:** M–L. **Ganho:** a maior parte da arquitetura.
- **Dependências:** Etapas 1–2. **Rollback:** flag de opt-in por chave.

### Etapa 4 — Separar o Discord

- **Objetivo:** um adapter que converte interações em eventos, codifica
  `sessão:revisão:ação` nos `custom_id`s, executa efeitos, cuida da transição embed ↔
  LayoutView, timeouts e identidade de mensagem.
- **Problema resolvido:** P2, P3, G11, resto de G10.
- **Mudança:** `adapters/discord/{interactions,renderer,executor,entrypoints}.py`; views
  em `app/components` e `summary_card.py` viram renderers que recebem `Screen`;
  `send_command_form_message` / `send_command_manager_message` chamam o adapter.
- **Arquivos:** os acima; pontos de entrada em `moderations.py`; `buttons.py` (ids
  fixos).
- **Compatibilidade:** cogs inalterados.
- **Testes:** o harness behavioral vira a suíte do adapter (API inalterada); testes de
  contrato para o codec de `custom_id` e limites CV2.
- **Risco:** Médio (coreografia). **Esforço:** M. **Ganho:** segurança contra clique
  velho; desenho testável.
- **Dependências:** Etapa 3. **Rollback:** flag por chave.

### Etapa 5 — Padronizar extensões

- **Objetivo:** um protocolo `FeatureModule`; remover hooks, listas de constants,
  branches no motor, injeção de atributo.
- **Problema resolvido:** G6, G7 (lado da feature), I7, I10.
- **Mudança:** `features/<chave>.py` implementando o protocolo; `GenericCogFeature` como
  padrão; `pre_finish_step` → `commit`; `lifecycle_callbacks` → métodos;
  `settings_provider` → `summary`; `COMMAND_SERVICES` → registro de features; validators
  viram funções puras com `needs`.
- **Arquivos:** `app/services/{reminders_birthdays,notifications_twitch,notifications_youtube_video,welcome_messages,stream_elements}.py`, `constants.py`, `modals.py` (validators), sobras em `form.py`/`manager.py`.
- **Compatibilidade:** feature por feature.
- **Testes:** testes unitários de módulo de feature (ida e volta de documento, resultado
  de commit); contratos de consumidores em todas as chaves.
- **Risco:** Baixo–Médio. **Esforço:** S–M. **Ganho:** zero edição no motor por feature.
- **Dependências:** Etapa 3. **Rollback:** manter caminho de hook antigo por chave.

### Etapa 6 — Migrar os forms restantes e apagar o motor antigo

- **Objetivo:** os sete forms no motor novo; apagar `form_state.py`, internos antigos de
  `Form`, `EditCommand`/`RemoveItem` (viram modos), actions mortas.
- **Ordem:** block_links → default_roles → stream_elements → welcome_messages →
  notifications_twitch → notifications_youtube → reminders_birthday.
- **Testes:** todo cenário existente roda no motor novo; snapshot dos documentos
  persistidos por form antes/depois (o `expect_persisted` do harness).
- **Risco:** Médio para birthday (maior superfície). **Esforço:** M–L acumulado.
- **Dependências:** Etapas 3–5. **Rollback:** flag por chave até apagar.

---

## 20. Esforço x ganho

| Mudança | Dor resolvida | Esforço | Risco | Ganho | Alavancagem |
|---|---|---|---|---|---|
| Destravar o event loop (`to_thread`, sem `time.sleep`) | tempestades de interaction-failed | S | Baixo | Alto | Média |
| Trava + dedup por `interaction.id` em cada view | avanço duplo / commit duplo | XS | Baixo | Alto | Baixa |
| Finalização em `on_timeout` | ghosts por expiração silenciosa | XS | Baixo | Médio | Baixa |
| Corrigir `_go_back` (remover por chaves de passo) | respostas velhas | XS | Baixo | Médio | Baixa |
| `custom_id`s fixos; palavra de confirmação por i18n | roteamento errado / dependente de idioma | XS | Baixo | Baixo | Baixa |
| Escritas de lista de itens via `$push`/`$pull` ou revisão no doc | itens perdidos | S | Baixo | Médio | Média |
| Id de sessão nos logs | incidentes indiagnosticáveis | XS | Baixo | Médio | Média |
| Schema tipado do YAML + compilador + versão | drift, typos, caminhos mortos | S–M | Baixo | Médio | **Alta** |
| `FormSession` + store (estado único) | toda a classe de estado velho | M | Médio | Alto | **Alta** |
| Motor `decide` + tipos de passo + `Screen` | testabilidade, isolamento | M–L | Médio | Alto | **Alta** |
| Adapter de Discord + codec de `custom_id` | segurança contra clique velho | M | Médio | Alto | Alta |
| Protocolo `FeatureModule` | cirurgia espalhada | S–M | Baixo | Alto | **Alta** |
| Composição como sessão filha | caminho mais frágil | M | Médio | Alto | Média |
| Sessões persistidas / views persistentes | sobreviver a restart | XL | Alto | Baixo | Baixa |
| Grafo genérico de workflow no YAML | nenhuma hoje | L | Alto | Nenhum | Nenhuma |

**Vitórias rápidas:** linhas 1–7. **Fundações:** schema/compilador, `FormSession`, id de
sessão. **Estruturais:** motor, adapter, `FeatureModule`, composição. **Sofisticação
opcional:** sessões persistidas, grafo de workflow.

---

## 21. Antes e depois

| Dimensão | Hoje | Recomendado |
|---|---|---|
| Criar um form novo | YAML + constant + módulo de service + `COMMAND_SERVICES` + cog + arquivos de idioma; conhecer 4 tipos de hook | YAML + cog + arquivos de idioma; `FeatureModule` só se a persistência não for genérica |
| Comportamento custom | hooks que recebem views, branches no motor, constants | métodos do `FeatureModule` + validators puros registrados |
| Dono do estado | `Form.responses` + `FormStateManager` + dicts de view + `Manager.cogs` | `FormSession` no `SessionStore` |
| Ciclo de vida | implícito (`hasattr`, booleanos) | enum `Status` explícito com tabela de transição |
| Validação do YAML | só testes, dicts crus cacheados | compilado, tipado, versionado, startup + CI |
| Interações do Discord | tratadas dentro de métodos do motor | adapter; o motor nunca importa discord |
| Eventos velhos | roteados ou falhando em silêncio | rejeitados por `expected_revision`; redesenho com aviso |
| Eventos duplicados | avanço duplo / commit duplo | dedup por `event_id` + lock por sessão |
| Persistência | sequências inline, `$set` inteiro | efeito `Commit` → `FeatureModule.commit` → `CommitResult`; operadores de array para itens |
| Testes | só harness | unitários + propriedade no motor; harness para o adapter |
| Depuração | id de interação + guild | id de sessão, revisão, passo, evento, resultado do efeito |
| Adicionar features | tocar motor + helpers compartilhados | YAML + módulo de feature |
| Arquivos tocados (form simples) | 5–6 | 3 |
| Risco de regressão | alto (helpers compartilhados) | baixo (features isoladas; motor coberto por testes puros) |

---

## 22. Estratégia de testes para o alvo

| Nível | O quê | Sem Discord? | Sementes já no repo |
|---|---|---|---|
| Schema | todo YAML compila; fixtures inválidas rejeitadas (campo desconhecido, idioma faltando, condição ruim) | sim | `test_form_yaml_contracts.py` |
| Config semântica | condições apontam para chaves anteriores; registros resolvem; limites de composição | sim | idem |
| Tabelas do motor | `(defn, sessão, evento) → (sessão', efeitos)` por kind, inclusive voltar, pular, condicional, obrigatório de card, ida e volta de transform | sim | `tests/test_form.py`, `tests/test_form_state.py` (a reescrever) |
| Invariantes / propriedade | revisão estritamente crescente; replay de `event_id` é no-op; sessão fechada rejeita; chaves de `answers` ⊆ chaves da definição; render é determinístico | sim | nenhuma |
| Contratos de extensão | cada `FeatureModule`: `from_document(to_document(x)) == x`; formato do resultado de commit; cada validator é puro | sim (mocks de integrações) | `tests/mocks/*` |
| Idempotência / concorrência | dois eventos iguais concorrentes → uma transição; revisão velha → rejeitada; lock serializa | sim | nenhuma |
| Adapter | codec de `custom_id`; Screen → componentes dentro dos limites CV2; coreografia embed ↔ LayoutView; finalização em timeout | harness | `tests/behavioral/harness/`, `test_components_v2_limits.py`, `test_view_timeouts.py` |
| Integração Discord | smoke ao vivo numa guild de teste | não | documentado em `docs/testing-strategy.md` |
| Regressão | um cenário por incidente, nomeado pelo contrato | quase tudo no nível do motor agora | `tests/behavioral/regressions/` |

---

## 23. Estratégia de observabilidade

Identificadores que tornam um ciclo de vida de form reconstruível (cada um já existe ou
custa um campo): `command_key`, `definition_version`, `session_id`, `session_revision`,
`mode`, `status`, `step_key`, `event_type`, `interaction_id`, `message_id`, `guild_id`,
`user_id`, `effect` (nome + resultado + duração). Não útil: respostas completas (PII,
ruído), custom ids além da ação decodificada, conteúdo de embed.

Emitir: uma linha info por `decide` (`session_id rev passo evento → status efeitos=[...]`),
uma linha por execução de efeito (ok / falhou + classe da exceção), um warn por evento
rejeitado (velho / duplicado / fechado) com o motivo, um error por commit falho com o
`CommitResult` até ali. Adicionar em `ErrorContext.extra` (`app/exceptions.py`) os campos
de sessão para o canal de log do Discord (`app/logger.py:219-247`) mostrar. Métricas (a
branch já tem `app/cogs/prometheus.py`): lag do event loop (mede C1 diretamente), sessões
abertas/concluídas/canceladas/expiradas/falhas por chave de comando, eventos rejeitados
por motivo, duração de commit por feature.

---

## 24. Melhorias imediatas (seguras para começar agora)

1. Trocar `time.sleep(15)` por `await asyncio.sleep(15)` e tornar `wait_for_stream_info`
   async (`notifications_twitch.py:174-189`); envolver `bot.twitch.*`,
   `StreamElementsClient.*`, `requests.get` em `asyncio.to_thread` nos pontos de chamada
   dos handlers (`modals.py:737,747`, `form.py:940`, `welcome_messages.py:188`).
2. Adicionar trava de processamento e memória de `interaction.id` em `Form._callback`,
   `Form._finish`, `SummaryCardView.interaction_check`, callbacks do `Manager`.
3. Implementar `on_timeout` nas views de form/manager/card/select: remover componentes e
   editar a última mensagem com aviso de "expirado".
4. Corrigir `_go_back` para remover respostas pelas chaves que o passo produziu, não
   dando `pop` em uma.
5. Adicionar checagem de schema do YAML no startup que falha em `action` desconhecida,
   `validation`/`response_transform`/`style`/`type` de seção desconhecidos, idiomas
   faltando.
6. Parar de usar rótulos localizados como `custom_id`; tirar a palavra de confirmação de
   uma chave i18n.
7. Logar um `session_id` por form (uuid4 em `Form.__init__` / `Manager.__init__`) em
   toda linha de log do motor e no `ErrorContext`.
8. Escrever os cenários de regressão para: clique duplo no Done do card, clique duplo no
   Confirm da revisão, voltar por cima de `multi_select`, dois painéis de manager
   adicionando itens.

Nenhuma dessas muda o formato salvo nem os YAMLs, exceto a (5), que só os lê.

---

## 25. Oportunidades de longo prazo (depois da fundação)

- Rascunho persistido por guild para forms longos (só se usuários pedirem "continuar de
  onde parei"); vira uma implementação de `SessionStore`, não mudança no motor.
- Um painel `/setup` que abre qualquer form em modo *edição* de um campo só (o modo
  `Edit([chave])` já é conceito de primeira classe).
- Front-ends fora do Discord (uma página web de admin) reusando `FormDefinition`,
  `Engine` e `FeatureModule` com outro adapter.
- Upgrades de definição: `from_document` por versão permite migrar configs salvas sem
  tocar no motor.
- Tela de "revisão" genérica com botão de editar por campo desenhada da definição
  (substitui o select de `EditCommand`).

---

## 26. Perguntas em aberto (o repositório não responde)

1. Que fração dos ghost clicks reportados acontece (a) >30 min depois de abrir, (b) logo
   depois de deploy, (c) durante eventos online da Twitch? Isso decide quanto da dor a
   Etapa 0 remove.
2. Dois admins gerenciam a mesma feature ao mesmo tempo na prática? Decide se operadores
   de array são vitória rápida ou opcional.
3. Um form precisa sobreviver a restart do bot? (Decisão de produto; define a Opção C.)
4. A diferença entre token de interação (15 min) e timeout de view (30 min) é
   intencional?
5. Existem documentos salvos em produção cujo formato antecede a convenção
   `{style, values}` atual (notas de migração existem em `~/task-state`; o repo não tem)?
   Decide o escopo de versionamento do `from_document`.
6. Quais métricas `app/cogs/prometheus.py` já exporta nesta branch? (Não auditado; lag do
   event loop e contadores de sessão são as que a review precisa.)

---

## 27. Ordem de execução recomendada

```
Fazer primeiro (Etapa 0, dias)
  destravar o event loop · trava + dedup · on_timeout · corrigir _go_back · session_id nos logs
  · cenários de regressão para clique duplo / voltar / painéis concorrentes
        ↓
Fazer em seguida (Etapas 1–2, semanas)
  schema tipado do YAML + compilador + versão · FormSession + SessionStore atrás de Form
  · escritas de lista de itens com operadores de array · composição como sessão filha
        ↓
Fazer depois de estabilizar (Etapas 3–5)
  engine.decide + tipos de passo + Screen · adapter de Discord + codec de custom_id
  · protocolo FeatureModule; migrar block_links → … → reminders_birthday
        ↓
Opcional depois (Etapa 6+ / pedaços da Opção C)
  apagar motor legado · sessões persistidas só se o produto pedir retomada
  · adapter fora do Discord
```

---

## Apêndice — As 25 perguntas, respondidas em uma linha cada

1. **Construir do zero?** Um motor puro sobre definições compiladas e sessões
   identificadas e versionadas, com Discord como adapter e features como módulos (§4,
   §6).
2. **Premissas que abandonaríamos:** Form = View; handlers alteram estado; branches de
   feature no motor; tags de apresentação no armazenamento; composição como `Form`
   aninhado (§3, §12).
3. **O que é um Form:** uma sessão de configuração guiada, curta, de um ator só, que
   commita um documento e algumas ações de domínio (§2).
4. **O YAML representa as coisas certas?** Quase; vaza comportamento e reflexão em alguns
   pontos (§14).
5. **Responsabilidades do YAML:** estrutura, textos, restrições, nomes de comportamentos
   registrados, versão (§4.8).
6. **Nunca no YAML:** expressões, caminhos de atributo, callables, lógica de
   persistência, nomes Python fora de registros (§4.8).
7. **Fonte única da verdade:** a `FormSession` no store; documentos depois do commit
   (§4.4).
8. **Estados do ciclo de vida:** ACTIVE, AWAITING, COMMITTING, COMPLETED, CANCELLED,
   FAILED, EXPIRED (§4.5).
9. **Transições:** a tabela em §4.5.
10. **Invariantes:** I1–I12 (§5).
11. **Quem altera estado:** só `Engine.decide` (§5 I2).
12. **Por que um form quebra outro hoje:** comportamento só pode viver em helpers
    compartilhados e branches no motor (§8.1, §11 G6).
13. **Por que ghost clicks:** event loop bloqueado, expiração/restart silenciosos, sem
    trava, ids reusados, falhas de substituição engolidas (§13.1).
14. **Duplicatas podem ser seguras?** Sim, com dedup por `event_id` + lock por sessão +
    status (§4.6).
15. **Falhas parciais:** hoje estados mistos (§13.3); alvo: efeito `Commit` com
    `CommitResult` e status FAILED (§6.3, §6.4).
16. **Extensão específica de produto:** `FeatureModule` + registros puros (§4.7, §18.3).
17. **API pública:** schema do YAML, `FeatureModule`, registros, helpers de teste (§4.9).
18. **Acoplamento com Discord:** só no adapter (§4.10).
19. **Arquitetura acidental:** ligações entre views em §10, itens 3–5 de §15, §12.
20. **Preservar:** YAML-first, um ponto de entrada por modo, views de componente como
    renderers, o harness, `app/data`, registros de transforms/formatters.
21. **Trocar:** internos de `Form`/`FormStateManager`/`Manager`, parâmetros de hook,
    listas de constants, composição como `Form` aninhado, tags `style` no
    armazenamento.
22. **Melhor fundação para 20–50 features:** Opção B (§17, §18).
23. **Incremental:** tudo em §19 é por etapa e por chave de comando.
24. **Maior alavancagem por esforço:** schema/compilador, `FormSession`, `FeatureModule`
    (§20).
25. **O que torna isso chato (no bom sentido):** uma tabela de transição que dá para ler,
    testes que não precisam de Discord, e um protocolo de feature que não alcança o motor.


---
---

# Parte II — Análise complementar (baseline corrigida para `origin/main` @ `65f7ec1`)

A Parte I foi escrita contra a branch da worktree `rukasudev/adding-metrics` em
`6cf2016`. O `origin/main` está 175 arquivos à frente desse commit (PRs #28 a #32:
redesign do block links, analytics + traces + journeys, painel do manager em Components
V2, correção do event loop). Várias afirmações da Parte I falam de código que o `main`
já mudou.

**Postura.** O código que chegou com esses PRs recebe exatamente o tratamento que a
Parte I deu ao código antigo: é **evidência e restrição de migração, não a
arquitetura**. Onde ele corrige um sintoma, a correção é reconhecida e depois julgada
contra o norte de §4–§6; onde ele adiciona um conceito que o produto precisa (um journey,
um catálogo de eventos), o conceito fica e a implementação é rederivada do zero como
tudo o mais. Nada abaixo é "construir em cima do que o `main` tem". Referências com
prefixo `main:` foram lidas do `origin/main`; as sem prefixo continuam apontando para a
baseline da Parte I.

## II.0 Correção de baseline — afirmações da Parte I reavaliadas, e o código pós-pull revisado

**Em uma frase:** o diagnóstico se mantém; dois sintomas foram remendados, não
removidos; o código novo traz superfícies de produto a preservar e arquitetura acidental
da mesma família que a Parte I apontou.

### II.0.a Afirmações da Parte I contra o `main`

| Afirmação da Parte I | Situação no `main` | Evidência | Julgamento contra o norte |
|---|---|---|---|
| C1 — event loop bloqueado por I/O síncrono e `time.sleep` | **Remendado nos pontos de chamada conhecidos** (PR #32, merged 2026-09-13) | `off_loop` (`main:app/services/utils.py:30-36`) envolve chamadas de Twitch/StreamElements/Mongo em `form.py`, `notifications_twitch.py`, `stream_elements.py`, `block_links.py`, `birthday_handler.py`; regressão `test_event_loop_is_never_blocked.py` | Um wrapper que quem chama precisa lembrar não é I12. Todas as outras chamadas `pymongo`/`redis`/`requests` em `app/data`, `cache.py`, validators e no manager continuam no loop. Do zero: camada de dados async; `off_loop` não existe. |
| C2 — expiração silenciosa, botões mortos | **Reportado, não corrigido** | `on_timeout` → `report_abandoned()` emite `setup.abandoned`; docstring: "Nothing is said to the user — the buttons already stop responding" (`main:app/views/form_state.py:175-193`) | O usuário continua vendo botões que parecem vivos e falham. Do zero: expiração é uma transição com `Finalize(expired)` visível. |
| P1 — sem trava contra dispatch duplo | **Parcial, e de propósito não para navegação** | `ActionCooldown` só em botões informativos; "Navigation buttons … have NO cooldown: double-clicking them is protected behavior" (`test_view_action_cooldown.py`) | **Done** do card e **Confirm** da revisão ainda rodam duas vezes. Cooldown por instância de botão é a unidade errada; a unidade é o evento da sessão. |
| P2 — `custom_id`s determinísticos reusados | **Sem mudança** | `summary_card.py` `_render` | — |
| P3 — apagar-antes-de-enviar engole falhas | **Metade** | `_send_layout_view` envia e depois apaga (`main:form.py:1368-1390`); `transition_to_embed` ainda apaga e depois envia (`main:panel_transitions.py:23-28`) | Duas funções donas da mesma coreografia com ordens opostas: sintoma de coreografia morando em views em vez de num executor de efeitos só. |
| P4 — `_go_back` remove uma resposta por passo | **Sem mudança** | `main:form.py` `_go_back` | — |
| P5 — `$set` do documento inteiro a partir de snapshot velho | **Sem mudança** | `manager.py`, `data/cogs.py` | — |
| G16 — sem id de sessão / sem trace | **Superfícies existem** (ver II.0.b, II.2) | `FormSession` + `SessionAwareView`, `trace.py`, `journey.py`, `catalog.yml` | As *superfícies* (mensagem de journey, catálogo, arquivo de logs) respondem a necessidades reais e ficam como requisitos de produto. A *implementação* é um mixin na View mais 12 pontos de emissão; revisada abaixo. |
| G6 — branches de feature no motor | **Sem mudança** (alguns mudaram de lugar) | `pre_finish_step` ainda faz branch (`main:form.py:1143-1160`); as constantes `COMPOSITION_*` agora também listam `block_links` | — |
| G13 — três canais de erro | **Melhorou** | rejeição de item duplicado usa `response_error_embed` | `OptionsView._confirm_callback` ainda usa `channel.send` |
| G15 — composição como `Form` aninhado | **Sem mudança, com mais um atributo injetado** | `FormComposition(parent_context=..., parent_form=...)` | — |
| §14 gramática do YAML | **Estendida organicamente** | `condition.matches`, `description-when`, `visible-when`, `{response:key|fallback}`, seção `multi-select`, `picker-description`, `options[].style` | Revisada em II.0.b e II.4. |
| Manager é embed + botões | **Substituído por painel Components V2** | `ManagerPanelView`, `_announce_event` (`main:manager_panel.py`, `manager.py`) | Revisado em II.0.b. |
| Regras de estilo de código | **Existem** | `.claude/rules/code-style.md` (12 regras) | Mantidas; II.6 estende. |

### II.0.b O código pós-pull, revisado contra o norte

Mesmo método da Parte I: o que é, por que provavelmente existe, escolheríamos do zero, o
que entra no lugar.

| Novo no `main` | O que é | Escolheríamos do zero? | Equivalente do zero |
|---|---|---|---|
| `off_loop(func, *args)` (`utils.py:30-36`) | `asyncio.to_thread` com um nome; usado em 12 pontos | **Não.** Converte uma propriedade estrutural em disciplina. Dois dos quatro pontos originais ficaram de fora no mesmo PR (PROD-ERRORS F5). | Camada de dados async (`motor`, redis async) e clientes de integração async; lint que proíbe cliente síncrono fora de `app/data` e `app/integrations`. O helper é apagado. |
| `FormSession` + mixin `SessionAwareView` (`form_state.py:12-193`) | Um objeto contador (passos vistos, voltas, falhas, duração) misturado em `Form` e `Manager`; o setter de `view` escreve `owner_form` em toda view filha | **Não.** Chama-se "sessão" mas não tem respostas, cursor, status nem revisão: é contabilidade de analytics parafusada na View, e um *terceiro* dono de estado ao lado de `Form.responses` e `FormStateManager`. O setter que faz back-reference `owner_form` é injeção de atributo com decorator. | A `FormSession` de §4.1 (respostas, cursor, status, revisão). Os números de fricção são *derivados* do log de eventos da sessão, não contados à mão. O nome é recuperado. |
| `trace.py` — uma unidade de trabalho, uma mensagem; `ContextVar`; sinks | Ideia de produto correta; implementação vive na árvore de handlers do logging (`TraceFoldingHandler`, `DiscordLogsHandler.send_trace`) | **Conceito sim, lugar em parte.** Um trace por interação/webhook/job está certo. Decidir o que um trace *contém* dobrando toda chamada `logger.*` é como "limpo não é o mesmo que silencioso" (a mensagem "Left Guild" perdida) aconteceu. | Manter a fronteira do trace no adapter (um trace por evento tratado). As linhas de trace do trabalho de form vêm da `Decision` e do resultado dos efeitos, não de dobrar logs de texto livre. |
| `journey.py` — uma sessão, uma mensagem, editada até o fim | Superfície de produto correta; montada observando eventos de analytics (`register_observer`) e redesenhando um `Trace` | **Conceito sim, fonte não.** O journey é desenhado a partir de eventos que 12 pontos colocados à mão emitem; ponto faltando é linha faltando. | O journey é uma projeção do histórico de `Decision` da sessão: toda transição é uma linha por construção. Mesma mensagem, mesma experiência de leitura, zero pontos de emissão. |
| `analytics/catalog.yml` + `analytics.emit` + 12 pontos no motor (`docs/analytics.md` §2) | Vocabulário fechado de 30 eventos, privacidade por construção, camadas de armazenamento | **Catálogo sim, pontos não.** O catálogo, a regra de privacidade e o desenho de armazenamento estão certos e ficam como requisitos. Doze pontos de emissão dentro de callbacks de view são o risco de drift que o teste de contrato do catálogo não enxerga. | Um ponto de emissão: `Decision → eventos`. O contrato do catálogo confere a tabela de eventos do motor contra o catálogo. |
| `ManagerPanelView` (`manager_panel.py`) | Painel Components V2 com ✏️ por seção; `__getattr__` repassa todo atributo desconhecido ao `Manager`; properties encaminham `edited_form_view`, `form_view`, `_original_embed` | **Painel sim, encanamento não.** Edição por seção é o produto certo. O proxy existe para os callbacks antigos do `Manager` continuarem funcionando através do container: a mesma família de `parent_view.edited_form_view = …` da Parte I (§10), agora com `__getattr__`. | `ManagerPanel` é um `Screen`; o renderer desenha; botões de seção codificam `k:<sessão>:<rev>:edit:<step_key>`. Sem back-references. |
| `panel_transitions.py` | A regra "flags de Components V2 são fixas no envio, então substitua em vez de editar" | **Regra sim, função não.** A regra é restrição do Discord e pertence ao conhecimento do renderer. Tê-la como helper que dois chamadores usam com ordens opostas de apagar/enviar é o defeito. | Um executor do efeito `Replace`: envia, depois apaga, loga delete que falhou. |
| `ActionCooldown` + `acknowledge_hot_click` (`buttons.py`) | Rate limit por instância de botão com aviso que se apaga | **Não.** A unidade de "o mesmo clique duas vezes" é o evento da sessão, não a instância do botão; a exceção para botões de navegação prova isso (precisavam de dedup, não de cooldown). | Dedup por `event_id` + `expected_revision` no adapter; evento velho/duplicado responde com `Notice` + redesenho. |
| `condition_allows` com `matches` (regex no YAML), `description-when`, `{response:key:formatter|fallback}` com `RESPONSE_TOKEN_FORMATTERS = {"host": …}`, ordem de busca de `_condition_value` respostas → cogs → `parent_context` | A gramática de condição ganhou regex, lista de variantes de copy e uma mini-linguagem de template com registro de formatter de uma entrada | **Não.** É a "linguagem de programação acidental no YAML" de §3, um PR mais adiante; três fontes de busca para uma chave é escopo implícito. | A gramática `when` da II.4 com escopos explícitos; tokens de copy resolvidos de um catálogo fechado de formatters declarado no schema da definição. |
| `FormComposition(parent_context=…, parent_form=…)` | Mais dois atributos injetados para um sub-passo ler respostas do pai | **Não.** | Sessão filha com cadeia de escopos (II.4). |
| `keep_cancel_button_last` | Reancora o botão Cancelar porque views são montadas na ordem de `add` | **Não.** Regra de layout garantida por cirurgia de lista depois do fato. | `Screen` declara a ordem dos botões; o renderer obedece. |
| `hidden` copiado em toda entrada de resposta; split rótulo/cru de `styled_values` em `_save_step_response` | Mais campos no dict de resposta sem tipo | **Não.** | `Answer` tipado; visibilidade e rótulos são fatos da definição, resolvidos no desenho. |
| `RecordsBrowser`, `base_embed`, `ViewConstants`, `DiscordLimits` | View genérica para listas de registros; esqueleto de embed compartilhado; tunáveis e limites globais | **Sim.** Genéricos, sem acoplamento com o motor. | Mantidos como views/constantes genéricas fora da plataforma; o browser vira consumidor do renderer. |
| `.claude/rules/code-style.md` | 12 regras revisadas pelo mantenedor | **Sim.** | Mantido; estendido pela II.6 e garantido por ferramenta. |
| Logs de debug em `guild.logs` + arquivo diário + `tools/keiko logs` | Armazenamento e consulta de tudo que é logado | **Sim.** Fora da plataforma de forms; foi o que tornou a II.1 possível. | Mantido sem mudança. |
| Redesign do `block_links` (card com modos, composição de regras, condições, registros) | O primeiro form que exercita card + composição + condições juntos | **Como form, sim; como evidência, inestimável.** É o form que empurrou a gramática além do desenho e produziu H10–H12. | É o primeiro form a migrar *depois* dos simples, e o consumidor principal dos testes da gramática `when`. |

Efeito líquido nas conclusões da Parte I: o **diagnóstico se mantém**; dois sintomas
foram **remendados, não removidos** (C1, P3); o código pós-pull **adiciona superfícies de
produto que o alvo precisa preservar** (mensagem de journey, catálogo de eventos, painel
com edição por seção, arquivo de logs) e **adiciona arquitetura acidental da mesma
família que a Parte I identificou** (um terceiro dono de estado, proxies `__getattr__`,
back-references por atributo, uma mini-linguagem crescendo no YAML, coreografia dividida
entre helpers); a **direção recomendada não muda**.

## II.1 A arquitetura alvo elimina os erros históricos? (lido do arquivo real)

**Em uma frase:** 13 grupos de falha no escopo, 12 eliminados por construção, 1
mitigado; e as falhas silenciosas nunca vão aparecer em log nenhum.

Fontes, lidas na íntegra por uma passada de análise dedicada (relatório:
`docs/form-platform-error-history.md`, 508 linhas, só leitura):

- **A** — o índice local de dois anos de arquivos diários de log, `~/.keiko/logs.db`
  (2024-07-27 → 2026-08-20; 18.054 registros, 2.355 ERROR + 676 WARNING, traceback em
  1.656 dos erros).
- **B** — `guild.logs` de produção (2026-08-21 → 2026-09-13; 988 ERROR + 2.263 WARNING).

Os registros foram agrupados por assinatura (tipo da exceção + primeira linha
normalizada + último frame em `app/`) e classificados. Totais:

| Classe | A (2 anos) | B (24 dias) | O que é |
|---|---:|---:|---|
| LOGGER_INFRA | 2.124 | 2.920 | 429 nos dois canais de log (2.319), linhas do werkzeug por port scanner (1.547), um pico de DNS/gateway em 2024-12-23 (714), embed > 4096 (141), avisos de heartbeat bloqueado (129), restarts |
| INTEGRATION_WEBHOOK | 489 | 325 | serviços de notificação Twitch/YouTube (`get_channel` em `None` 211, payload ruim do YouTube 131, 429 em busca de mensagem 344), API de lembretes |
| PERSISTENCE | 266 | 0 | `KeyError 'allowed_chats'` (105 incidentes, logados duas vezes), `welcome_messages_channel` (3), `default_roles` `None` (4), quedas do Mongo Atlas atingidas por um `find_one` síncrono dentro de `on_message` (47) |
| FORM_ENGINE | 21 | 6 | 14 grupos, listados abaixo |
| DISCORD_LIBRARY | 68 | 0 | picos de 5xx do Discord |
| OTHER | 63 | 0 | bugs de cog (`/help` em DM, error handler) |
| **Total** | **3.031** | **3.251** | |

Lendo com honestidade: os fluxos interativos de configuração são **0,4 % do volume de
erros** em dois anos. A review não se justifica por volume de erro. Ela se justifica
pelo que cada um desses 27 registros é (a tentativa de configuração de um admin quebrada
no meio) e pelo fato de que as classes de falha silenciosa (estado errado depois de
Voltar, itens perdidos entre dois painéis, clique velho roteado para o handler errado)
**não lançam exceção e, portanto, não podem aparecer neste arquivo**. O arquivo confirma
causas; ele não descarta as silenciosas.

### Cada grupo de falha no escopo, e o que o alvo faz com ele

| # | Falha (registros, período) | Mecanismo encontrado no traceback | Mecanismo no alvo | Veredito |
|---|---|---|---|---|
| H1 | `KeyError: 'allowed_chats'` em `block_links.check_message` em toda mensagem de uma guild — 105 incidentes (×2 no log), 2026-05-18 → 07-12 | documento escrito antes de a chave existir, lido cru (`cogs[KEY]["values"]`) | `FeatureModule.from_document(schema_version)` é o único leitor; documento que o módulo não consegue atualizar vira load `FAILED` logado, nunca um subscript | **Estrutural** |
| H2 | `KeyError: 'welcome_messages_channel'` em `send_welcome_message` — 3, 2026-03-30 | mesmo | mesmo | **Estrutural** |
| H3 | `TypeError: 'NoneType' not iterable` em `default_roles.filter_roles` — 4, 2026-04-02/03 | mesmo (chave de cargos faltando → `None`) | mesmo, mais `Answer` tipado (lista ausente é `[]`, nunca `None`) | **Estrutural** |
| H4 | `ServerSelectionTimeoutError` / `_OperationCancelled` do Mongo Atlas atingidos por `cache.get_cog_data_or_populate → data/cogs.find_one` dentro de `on_message` — 47 registros, 2026-03-25 → 06-25; mais 129 avisos de `heartbeat blocked` cujo traceback do loop termina em `find_one` (55), `redis.get` (9), busca de imagem com `requests` (6), `wait_for_stream_info` (4); um bloqueio contínuo de 350 segundos em 2026-08-31 | driver síncrono no event loop | camada de dados async (`motor`, redis async) de ponta a ponta; validators declaram `needs` e o adapter busca antes do `decide`; lint proíbe cliente síncrono fora de `app/data`. A queda ainda falha a checagem; ela não trava mais o gateway | **Estrutural** para o travamento; a queda é infra |
| H5 | `select.py:update` com `self.view is None` a partir do `/help` — 8, 2026-02-24 → 03-19 | um item `Select` desligado da View parada/limpa antes de um update rodar | views são renderers descartáveis; nada atualiza componente no lugar; estado vive na sessão | **Estrutural** |
| H6 | `View interaction referencing unknown view` em `Editar`/`Confirmar`/`Add` do manager — 2 em A, 2 em B (2026-09-10) | clique numa View que tinha parado, expirado ou pertencia a um processo anterior | o codec de `custom_id` resolve o clique pela `SessionStore`; sessão expirada ou desconhecida responde com finalização visível de "expirado" em vez de botão morto | **Estrutural** |
| H7 | `KeyError: 'en-gb'` / `'en-US'` em `_set_titles_and_descriptions` e `parse_command_event_description` — 5, 2026-04-05 | locale do usuário usado como chave de dict sem fallback | `Origin.locale` normalizado uma vez quando a sessão abre; resolução de copy passa por uma função só | **Estrutural** (já corrigido no `main`, `f964951`) |
| H8 | `DesignSelectView` editando com `content=` uma mensagem Components V2, escondido por um bug de aridade do `on_error` — 1, 2026-03-09 | o renderer não sabia o tipo da mensagem; assinatura do error handler divergiu | o renderer decide embed ↔ Components V2 por `Screen`; um só caminho de `on_error` no adapter | **Estrutural** |
| H9 | `IndexError` em `form._handle_subscription` durante o fluxo de edição (`manager.update_command → pre_finish_step`) — 1, 2026-04-25 | código de feature indexando a lista compartilhada `self.responses` com índice calculado para outra lista | `FeatureModule.commit(session)` recebe respostas tipadas do item em edição; sem lista compartilhada, sem branch no motor | **Estrutural** (I2, I7, I10) |
| H10 | `10062 Unknown interaction` em `manager.add_item_callback` — 1, **novo em B** (2026-09-10) | `form.update_counter → _after_callback → composition.finish → parent_callback → form.update_counter → _after_callback → manager.add_item_callback → defer()`: dois after-callbacks aninhados rodaram numa interação antes de ela ser reconhecida | o adapter reconhece cada interação exatamente uma vez e depois chama `decide`; sessões filhas devolvem uma `Decision` ao pai, nunca reentram na cadeia de callbacks dele | **Estrutural** |
| H11 | `10008 Unknown Message` em `panel_transitions.transition_to_embed` a partir de `show_buttons` — 1, **novo em B** (2026-09-09) | edição de uma mensagem layout que o fluxo já tinha apagado (ordem apagar-depois-editar no sentido embed) | efeito `Replace` executado por uma função do adapter: envia primeiro, apaga depois, falha deixa a mensagem anterior | **Estrutural** |
| H12 | `50035 In components.0: type must be one of (1, 9, 10, 12, 13, 14, 17)` em `_send_layout_view` a partir de `show_design_select` — 1, **novo em B** (2026-09-09) | payload Components V2 carregando um componente legado | `Screen → renderer` valida a árvore de componentes contra o tipo da mensagem na construção; fixado pelos testes de contrato CV2 existentes | **Estrutural** |
| H13 | `50035 In type: Value must be one of {4, 5, 6, 7, 10, 12}` no `FileUploadModal` — 4 em A (2026-07-14), 1 em B (2026-09-09), **ainda aberto em produção** | modal montado com um tipo de componente que o Discord não aceita nesse modal (`ui.FileUpload` dentro de `ui.Label` do discord.py master) | um `Screen` de modal é validado contra a lista de componentes permitidos em modal antes de `OpenModal`; a primeira montagem inválida falha um teste de contrato, não um usuário | **Mitigado** — a regra vem do Discord e pode mudar; o alvo pega no CI, não impede a API de mudar |

Fora do escopo da plataforma de forms (já corrigido no `main` ou infra): as
tempestades de 429 nos canais de log amplificadas pelo port scanner, as linhas do
werkzeug, o pico de DNS de 2024-12-23, o limite de tamanho de embed, o `get_channel` em
`None` da Twitch (211 registros, 2025-06 → 2026-03), os erros de payload do YouTube, o
novo 429 constante em busca de mensagem (324 em B, chamador desconhecido), as falhas da
API de lembretes.

Contagem: **13 grupos de falha no escopo; 12 eliminados por construção, 1 mitigado.**
Nenhum grupo precisou de mecanismo além do que §4 já propunha. Três dos treze são
**novos nos últimos 30 dias** (H10, H11, H12) e os três são falhas de fronteira de
transição, exatamente a classe que a Parte I apontou como defeito central (§4.3, I11).

### O que o arquivo diz sobre as previsões da Parte I

| Previsão | Evidência no arquivo | Leitura |
|---|---|---|
| P1 — dispatch duplo em Done / Confirm | nenhum `40060` em dois anos; H10 é um primo (uma interação consumida duas vezes por callbacks aninhados) | o mecanismo é real; o sintoma exato do clique duplo não está registrado |
| P2 — clique velho roteado para uma view mais nova por `custom_id` reusado | nenhuma | não pode ser registrado: não lança exceção |
| P3 — apagar-depois-enviar deixa o usuário sem tela | H11 (sentido embed), e a sessão de 2026-09-09 que o `test_layout_view_transition` da Parte I descreve | **confirmado** |
| P4 — Voltar por cima de um passo com várias respostas deixa respostas velhas | nenhuma | não pode ser registrado: não lança exceção; precisa de cenário de regressão |
| P5 — dois painéis perdem itens | nenhuma | não pode ser registrado: o `$set` dá certo |
| C1 — loop bloqueado | 129 avisos de heartbeat + H4 + o `10062` ao salvar um youtuber (PROD-ERRORS 2026-09-10) | **confirmado**, corrigido nos pontos conhecidos no `main` |
| C2 — expiração / restart deixa botões mortos | H6 (4 registros, nas duas fontes) | **confirmado**, ainda aberto |

Consequência para o plano: P2, P4 e P5 precisam ser fixados por cenários na Fase B,
porque nenhum log vai mostrá-los; H13 precisa ser investigado contra as regras atuais de
modal do Discord antes da Fase D, porque é a única falha no escopo que o alvo não
consegue tornar impossível.

## II.2 Observabilidade — o que o `main` já tem, o que o alvo acrescenta

**Em uma frase:** o `main` já vê que aconteceu; o alvo faz dar para provar por quê.

O `main` tem mais do que a Parte I descreveu. Estas são as superfícies que existem hoje (`docs/analytics.md`), listadas como **requisitos que o alvo precisa continuar atendendo**, não como implementação a estender (a II.0.b diz por quê):

- **Trace** por unidade de trabalho num `ContextVar` (`app/services/trace.py`): uma
  mensagem no Discord por slash command / requisição de webhook / job adiado;
  listeners ficam em silêncio a menos que falhem ou reportem um evento.
- **Journey** por sessão de configuração (`app/services/journey.py`): uma mensagem só,
  redesenhada até a sessão terminar, com as linhas dos passos resolvidas para os títulos
  que o usuário viu, e um rodapé com o histórico de 24h por feature.
- **Analytics de produto** com um catálogo de 30 eventos (`app/analytics/catalog.yml`),
  emitidos de 12 pontos do motor, privacidade garantida por um teste que digita um
  segredo num fluxo real; armazenamento dividido em eventos (90d), contadores mensais
  (13 meses) e um perfil por guild.
- **Logs de debug** em `guild.logs` (30d, indexado por `session_id`) mais um arquivo
  diário e uma CLI com busca full-text e agrupamento de erros (`tools/keiko logs`).
- **Fricção da sessão** (`FormSession`): passos vistos, falhas de validação, voltas,
  faixa de duração, tempo por passo.
- Digest semanal e `/admin insights`.

O que o alvo muda, do zero, mantendo todas as superfícies acima:

| Pergunta de quem está de plantão | Hoje (`main`) | Alvo |
|---|---|---|
| "Qual clique fez isso?" | `interaction_id` nos embeds de erro; linhas do journey por horário | `event_id` em toda linha de decisão; eventos rejeitados logados com motivo (`stale`, `duplicate`, `closed`) |
| "Em que estado o form estava?" | chave do último passo (`session.last_step_key`) | `session_id`, `revision`, `status`, `cursor` e as **chaves** das respostas presentes (nunca os valores) |
| "Qual versão da definição do form rodou?" | nenhuma; o YAML é cacheado no processo e sem versão | `definition_version` na sessão e em todo evento |
| "Qual efeito falhou, depois de qual deu certo?" | linhas de trace vindas dos `logger.*` que existirem no caminho | uma linha por efeito: `Render`, `Replace`, `OpenModal`, `Commit`, `Finalize` com resultado e duração; `CommitResult` na falha |
| "Consigo reproduzir?" | ler o journey, clicar de novo à mão numa guild de teste | **replay**: os eventos ordenados do journey + a versão da definição são exatamente a entrada do `decide` puro; uma sessão que falhou em produção vira um teste unitário (`replay(eventos) == esperado`) |
| "É o motor ou a feature?" | julgamento a partir do traceback | o efeito que falhou diz a camada: motor (`decide` lançou, é bug), adapter (`Render`/`Replace` lançou, é Discord), feature (`Commit` lançou, é domínio) |
| "De onde vêm os eventos?" | 12 pontos listados à mão em `docs/analytics.md` §2 | **um ponto só**: o motor emite analytics a partir da `Decision` (`setup.step_viewed` = um `Render` com cursor novo; `setup.validation_failed` = um `ShowError`; `setup.completed` = um `Commit` ok). O teste de contrato do catálogo passa a checar o motor, não 12 pontos de chamada |
| "O loop está saudável?" | cog Prometheus existe; nenhuma métrica de lag do loop encontrada no `main` | gauge de lag do event loop, contador de eventos rejeitados por motivo, contador de falha de efeito por tipo, sessões por status por feature |
| "O que o usuário viu?" | linhas do journey, transcript nos testes | o `Screen` que cada `Render` produziu é logável como estrutura compacta (título + tipos de componente), então "mensagem mostrando estado velho" vira algo diffável |

Ganho estimado, com honestidade: os artefatos visíveis (mensagem de journey, catálogo,
arquivo de logs) já existem e ficam como superfícies; as implementações são rederivadas. A contribuição do alvo é **determinismo e
atribuição**: toda observação ganha uma revisão e uma camada, e uma sessão vira
reproduzível. É a diferença entre "dá para ver que aconteceu" e "dá para provar por
quê", e não custa nada extra quando `decide` é puro, porque o ponto de emissão é o
valor de retorno de uma função.

## II.3 Premissa nova — o que o usuário vê não muda por acidente

**Em uma frase:** migração de motor é invisível para o admin do servidor; toda
diferença visível fica registrada num changelog.

Premissa (adicionada ao norte, §4 e §6):

> Uma migração do motor é invisível para os admins dos servidores. Toda tela, toda
> mensagem, todo rótulo, ordem e cor de botão, toda cópia de erro e toda sequência de
> enviar/editar/apagar que um admin consegue perceber continua igual, a menos que a
> mudança esteja listada em `docs/ux-changes.md` com antes/depois e motivo.

Mecanismo, usando o que o `main` já tem:

1. **Transcritos golden.** O harness behavioral já produz um fluxo de eventos
   normalizado e determinístico (`scenario.outputs`, estabilidade garantida por
   `test_harness.py`). Gravar um transcrito golden por form por caminho canônico (setup
   feliz, erro de validação + recuperação, voltar, cancelar + manter, cancelar +
   descartar, editar um passo, adicionar item, remover item, pausar/despausar/desativar),
   uns 60 transcritos. Guardar em `tests/behavioral/golden/`.
2. **Contrato de igualdade.** Durante a migração, o motor novo precisa reproduzir cada
   transcrito byte a byte, exceto entradas listadas no changelog de UX. O teste falha com
   um diff, do mesmo jeito que as mensagens de falha de hoje já embutem o transcrito.
3. **Classificação dos deltas.** `invisível` (codificação de custom_id, ids internos),
   `cosmético` (uma linha de rodapé, um ícone), `comportamental` (um aviso novo, outra
   quantidade de mensagens). Só `cosmético` e `comportamental` entram no changelog;
   `invisível` é garantido invisível pelo normalizador, que descarta esses campos (ele já
   descarta `custom_id`s gerados automaticamente e timestamps).
4. **Deltas conhecidos que o alvo vai introduzir**, para documentar, não esconder:
   - um aviso efêmero curto num clique velho ou duplicado ("essa tela está desatualizada,
     aqui está a atual") — **comportamento novo**;
   - um aviso de "expirado" no lugar dos botões mortos depois do timeout —
     **comportamento novo**;
   - o erro de opção obrigatória saindo de um `channel.send` público para um embed
     efêmero (`options.py:96-100`) — **comportamental, correção de bug**;
   - a transição no sentido embed enviando antes de apagar — **invisível** quando dá
     certo, **comportamental** quando falha (o usuário mantém a tela antiga em vez de
     perdê-la).
5. **Copy continua no YAML e nos arquivos de idioma.** O motor nunca introduz uma
   string; `Screen` carrega chaves, o adapter resolve via `ml()` exatamente como hoje.

## II.4 Forms multicondicionais em vários níveis

**Em uma frase:** a gramática de hoje é uma chave só com AND; o alvo é uma gramática
`when` única, tipada, com escopos e checagem em compilação.

O que existe no `main` (`docs/form-configuration.md`, `main:app/services/utils.py:178-192`):

- `condition: {key, not_in, matches}` num passo: uma chave, AND de dois operadores,
  avaliada por `condition_allows`; valor buscado em **respostas → documento salvo →
  contexto do pai** (`Form._condition_value`).
- `description-when: [{condition, en-us, pt-br}]`: mesma gramática para copy.
- `visible-when: {key, not_in}` em seção de card: só `not_in`, contra o estado do card.
- Tokens `{response:key|fallback}` em descrições.
- Aninhamento de composição: **um nível**, com `parent_context` injetado para um
  sub-passo ler as respostas do pai.
- Testes de contrato: condições referenciam chaves anteriores; `visible-when` referencia
  chaves de estado do card; `description-when` referencia chaves produzidas.

Limites que vão morder assim que "mais disso" chegar:

| Necessidade | Hoje | Lacuna |
|---|---|---|
| Duas chaves numa condição (`mode == custom E channel preenchido`) | impossível; só uma `key` | gramática |
| OU / NÃO | impossível | gramática |
| Igualdade / pertencimento (`in`) | só `not_in` (daí `[false, "false", "False"]`) e regex | gramática + respostas tipadas |
| Condição do pai sobre um campo de item da composição, ou sobre a contagem de itens | impossível (`parent_context` é mão única, pai → filho) | escopo |
| Composições aninhadas em dois ou mais níveis | `Form("")` dentro de `FormComposition` dentro de `Form`; injeção de atributo não compõe | estrutura |
| `required`, `options` e defaults condicionais | nenhum (`required` é estático; opções são estáticas) | cobertura da gramática |
| Seções de card dependendo de outras seções com `in`/`matches` | `visible-when` é só `not_in` | consistência |
| Garantia estática de que as condições formam um DAG e todo ramo é alcançável | parcial (referências resolvem); sem alcance, sem checagem de ciclo | compilador |
| Explicar ao usuário por que um passo foi pulado (suporte) | nada | observabilidade |

Design alvo (adicionado a §4.8 e §18):

```yaml
when:                      # uma gramática só, usada em todo lugar que precisa de regra
  all:
    - {key: mode, in: [block_all, custom]}
    - any:
        - {key: allowed_chats, present: true}
        - {key: parent.register_now, is: true}
    - not: {key: link, matches: "^https?://"}
```

- **Folhas**: `is`, `in`, `not_in`, `matches`, `present`, `absent`, `count` (para
  listas de itens: `{key: custom_links, count: {min: 1}}`). Valores são tipados
  (`Answer.raw` é booleano de verdade em passos booleanos), então `is: true` tem uma
  grafia só.
- **Combinadores**: `all`, `any`, `not`, aninháveis.
- **Escopos**: `key` sozinha resolve na sessão atual; `parent.key` sobe um nível de
  sessão filha (repetível: `parent.parent.key`); `item.key` dentro de um passo de
  composição aponta para o item em edição; `items.count` para a lista.
- **Uso uniforme**: `when` (passo roda), `visible-when` (seção aparece e conta para
  `required`), `required-when`, `description-when`, `options-when` (filtra opções),
  `default-when`. Mesmo avaliador (`evaluate(regra, escopo) -> bool`, puro).
- **Checagens do compilador**: toda chave referenciada existe no mesmo escopo ou num
  escopo externo e é produzida por um passo anterior; sem ciclos; todo valor de `in`/`is`
  é uma das opções declaradas do passo que produz, quando ele tem opções; aviso para ramo
  que nenhuma combinação de opções declaradas alcança.
- **Runtime**: avaliado dentro do `decide`; a `Decision` registra quais regras foram
  avaliadas e o resultado, então o journey consegue dizer "pulou `custom_links` porque
  `add_custom` é falso", que é a resposta da pergunta de suporte.
- **Profundidade**: um passo de composição abre uma **sessão filha** (`mode=EditItem(i)`
  / `AddItem`) com `parent_id` explícito; o `decide` da filha recebe uma cadeia de
  escopos. Profundidade é ilimitada por construção; os limites de Components V2 (40
  componentes, 4000 caracteres) continuam sendo o único limite prático e são checados por
  `Screen`.
- **Testes**: testes de tabela do avaliador; um teste de propriedade que gera
  combinações de respostas a partir das opções declaradas de todo YAML e garante que
  todo passo é alcançado por pelo menos uma combinação (cobertura de ramos da definição).

## II.5 Prontidão para um builder de YAML e features geradas por IA com preview no Discord

**Em uma frase:** o alvo é pré-requisito e cobre uns 80%; faltam três peças pequenas.

Duas capacidades estão implícitas: **(a)** uma pessoa monta um form numa UI e vê um
preview; **(b)** uma IA escreve o YAML a partir de um prompt e o resultado é visto no
Discord antes de ativar. O que cada uma precisa, e onde o alvo está:

| Requisito | Hoje (`main`) | Alvo (Opção B) | Ainda falta para o builder |
|---|---|---|---|
| Schema formal e documentado da definição | nenhum; a gramática vive no motor e em prosa | modelos tipados compilados do YAML | **exportar JSON Schema** dos modelos tipados (uma função), com descrição por campo; o builder valida no cliente, a IA valida a própria saída |
| Catálogo de primitivos com metadados | registros existem mas são dicts Python | registros continuam | cada entrada de registro carrega `description`, `params`, `example`, nos dois idiomas; o builder lista, a IA lê |
| Desenhar um form sem Discord | impossível (desenhar *é* a chamada ao Discord) | modelo `Screen` de `render(step, session)` | um segundo renderer: `Screen → HTML` (preview do builder), pequeno, porque `Screen` já é abstrato |
| Preview dentro do Discord antes de ativar | impossível | `Mode.DryRun`: sessão cujo `decide` nunca emite `Commit` e cujo `Finalize` diz "preview" | um comando de admin `/admin preview <definição>` que roda uma sessão dry-run numa mensagem efêmera |
| Definições vindas de outro lugar que não o disco | `parse_form_yaml_to_dict` lê `app/languages/form/<chave>.yml`, cacheado para sempre | `DefinitionRegistry` por `(chave, versão)` | uma interface `DefinitionSource` (`disco`, `rascunho no banco`, `inline`); o builder salva rascunhos no Mongo por guild; ativar promove o rascunho a definição versionada |
| Persistência sem Python para um form novo | storage genérico de cog funciona só para o formato plano | `GenericCogFeature` (o `FeatureModule` padrão) com respostas tipadas → documento | sem mudança; um form gerado que precisa de ações de domínio ainda precisa de um `FeatureModule`, que é a fronteira certa para código gerado por IA parar |
| Segurança antes de ativar | testes de contrato no CI | compilação: schema + semântica + DAG de condições + limites CV2 por tela + dois idiomas presentes | o mesmo passo de compilação roda num rascunho; rascunho que não compila não pode ser previsualizado |
| Qualidade de copy | skill de estilo de escrita para humanos | sem mudança | um lint de copy (dois idiomas, convenções de emoji dos testes de contrato do YAML) vira parte dos avisos do compilador |

Veredito: **o alvo é o pré-requisito e chega a uns 80%**. As três adições específicas
do builder (export de JSON Schema, `DefinitionSource`, `Mode.DryRun`) são pequenas
depois que as definições são tipadas e o desenho é um `Screen` puro. Sem o alvo (desenho
dentro de `discord.ui.View`, YAML cacheado do disco, sem schema), um builder teria que
reimplementar o motor para mostrar um preview, e uma IA não teria contrato para validar.

Um cuidado para (b): definições geradas por IA nunca podem chegar numa guild real sem o
passo de compilação e um dry-run; a compilação é o portão, e precisa rejeitar tudo fora
da gramática fechada (sem fugas reflexivas `attr:`, e a II.4 remove a última).

## II.6 Premissa nova — estilo de código

**Em uma frase:** o código se lê de cima para baixo sem comentários; nome, tipo e
estrutura carregam o significado; ferramenta no CI garante.

`.claude/rules/code-style.md` (12 regras no `main`) já resolve: sem comentários
narrativos, onde constantes moram, data layer plana, só views genéricas em
`app/views/`, helpers genéricos em `utils.py`, tunáveis de UI em `ViewConstants`. A
premissa abaixo é adicionada ao norte e estende essas regras; é garantida por ferramenta,
não só por review.

> O código se lê de cima para baixo sem comentários. Os nomes carregam o significado; os
> tipos carregam o contrato; a estrutura carrega o fluxo. Um comentário é um relatório de
> defeito contra um nome.

- **Docstrings**: uma frase, presente do indicativo, só em módulos, classes e funções
  públicas; nunca no meio de função; nunca repetindo a assinatura; nunca história
  ("isso costumava…").
- **Comentários**: nenhum, exceto uma linha para uma restrição externa que o código não
  consegue expressar (um limite do Discord, uma peculiaridade de API), e essa linha nomeia
  a restrição, não a história. Os blocos `# Send before deleting: …` e `# Both branches
  reach a third-party API…` em `main:form.py` são exemplos do que o alvo remove ao tornar
  a restrição estrutural (um executor de efeitos que sempre envia primeiro).
- **Ritmo visual**: duas linhas em branco entre definições de topo, uma entre blocos
  lógicos dentro de função, nenhuma dentro de um bloco; imports em três grupos; 88
  colunas; vírgula final em literais multilinha; retorno cedo em vez de `if` aninhado;
  sem escadas de `hasattr`/`getattr` (são o sintoma de estado implícito, e o alvo remove a
  causa).
- **Tamanho**: função cabe numa tela; módulo tem um motivo para mudar; classe não tem
  método que não precise de classe.
- **Tipos**: dataclasses congeladas para definições, sessões, eventos, efeitos e telas;
  `Protocol` para pontos de extensão; nenhum `Dict[str, Any]` cruzando fronteira pública.
- **Ferramentas** (`make lint`, CI): `ruff` (format + lint, incluindo regras `D` de
  docstring limitadas a objetos públicos e resumo de uma linha, `C901` complexidade,
  `ARG`, `RET`), `mypy --strict` em `app/settings/`, e uma checagem própria que falha em
  comentário `#` com mais de uma linha fora de `app/integrations/`.

Por que a arquitetura serve ao estilo em vez de brigar com ele: os quatro helpers de
"desembrulhar `value|values`", as escadas de `isinstance(cogs, list)`, os
`hasattr(self, "after_callback")` e as quinze reatribuições de `self.view` existem porque
o estado não tem tipo. Dê um tipo ao estado e o código que precisava de comentário some
junto com o comentário.

## II.7 O caminho de obra-prima — sem restrição de esforço

**Em uma frase:** construir a plataforma limpa em isolamento, provar contra os
transcritos golden dos sete forms, migrar todos num único trem, apagar o motor antigo.

A Parte I otimizou segurança de migração com shims de compatibilidade (uma visão legada
de `responses` calculada, `to_legacy_steps()`, flags de motor por chave, sete etapas). Sem
a restrição, os shims são a troca errada: cada shim é código que existe para ser apagado
e que mantém o modelo antigo vivo na cabeça de quem revisa. O caminho abaixo constrói a
plataforma limpa, prova contra todos os forms existentes com o harness e os transcritos
golden, faz o cut-over e apaga o motor antigo. Mantém a Opção B como arquitetura (as
sessões persistidas da Opção C continuam não comprando nada que o produto pediu) e
incorpora tudo que a Parte I tinha deixado como "opcional" e uma obra-prima não deixaria
de fora.

### Alvo, forma final (diferenças em relação a §18)

- `app/settings/` é um pacote **sem import de `discord`** fora de
  `app/settings/discord/`, garantido por um teste de fronteira como
  `tests/tools/test_tools_boundary.py`.
- **Async de ponta a ponta**: a camada de dados passa para `motor`; `off_loop` é apagado
  junto com o último ponto de chamada síncrono. I12 vira verdade por construção, não por
  wrapper.
- **Definição**: modelos tipados, export de JSON Schema, `version`, a gramática `when` da
  II.4, `DefinitionSource` (disco agora; rascunhos no banco quando o builder chegar).
- **Sessão**: `FormSession` congelada com `id`, `revision`, `status`, `mode`, `cursor`,
  `answers`, `parent_id`, `seen_events`, `definition_ref`; interface `SessionStore` com
  implementação em memória.
- **Motor**: `decide` puro; `Decision` carrega as regras avaliadas e os eventos de
  analytics emitidos; replay a partir de um journey é um helper de teste de primeira
  classe.
- **Modelo `Screen`** + dois renderers: Discord (embed/View ou container Components V2,
  escolhido pelo tipo de passo, limites CV2 verificados) e HTML (preview do builder; pode
  esperar, mas a costura existe desde o primeiro dia).
- **Efeitos**: `Render`, `Replace`, `OpenModal`, `ShowError`, `Notice`, `Commit`,
  `Finalize`; executados por uma função do adapter sob um trace que o adapter abre por
  evento; uma linha de log por efeito.
- **Adapter**: codec de `custom_id` `k:<sessão>:<rev>:<ação>`, `asyncio.Lock` por
  sessão, dedup por `event_id`, velho/duplicado/fechado → `Notice` + redesenho,
  `on_timeout` → `Expired` → `Finalize(expired)` que remove componentes e avisa.
- **Features**: protocolo `FeatureModule`; `GenericCogFeature` padrão; módulos por feature
  para birthday, twitch, youtube, welcome, stream_elements; **sem** listas de constants,
  **sem** parâmetros de hook, **sem** `pre_finish_step`.
- **Persistência**: documentos guardam valores tipados crus mais `schema_version`;
  listas de itens escritas com operadores de array; `from_document` atualiza formatos
  antigos; script de migração one-off fica fora do repo (regra 9 de estilo).
- **Observabilidade**: analytics emitidos da `Decision`; `revision`, `event_id`,
  `definition_version`, resultado por efeito; a mensagem de journey é uma projeção das
  decisões da sessão (mesma experiência de leitura, sem pontos de emissão); métricas de
  lag do loop, eventos rejeitados e falhas de efeito no cog Prometheus.
- **Contrato de UX**: transcritos golden (II.3) e `docs/ux-changes.md`.
- **Estilo**: ferramentas da II.6 no CI desde o primeiro commit de `app/settings/`.

### Fases (cada uma termina com a suíte inteira verde)

**Fase A — Remendos de ponte no motor antigo (uma tarde).** Só o que protege produção
enquanto a plataforma nova é construída: trava de sessão em `Form._callback`, `_finish`
e `SummaryCardView.interaction_check`; `_go_back` removendo respostas pelas chaves que o
passo produziu; `transition_to_embed` enviando antes de apagar. Cada um com seu cenário
de regressão (P1, P4, P3-embed). Nada mais é tocado no motor antigo.

**Fase B — Transcritos golden (dois a três dias).** Gravar os ~60 transcritos da II.3
contra o motor antigo no `main`. A partir daqui eles são a definição de "o usuário vê a
mesma coisa".

**Fase C — Construir `app/settings/` completo, em isolamento (o grosso do trabalho).**
Definições + compilador + JSON Schema; avaliador `when`; sessão + store; motor + tipos
de passo (um por action existente, mais composição como sessão filha); screen +
renderer Discord; efeitos + adapter; `FeatureModule` + genérico + cinco módulos de
feature; ponto de observabilidade. Camadas de teste: tabelas puras do motor, testes de
propriedade do avaliador, testes de replay a partir de journeys gravados, testes de
adapter através do harness existente com o motor trocado por fixture, teste de fronteira,
ferramentas de estilo. O motor antigo continua servindo produção intocado nesta fase.

**Fase D — Cut-over, os sete forms, em ordem de risco num único trem de release.**
`block_links`, `default_roles`, `stream_elements`, `welcome_messages`,
`notifications_twitch`, `notifications_youtube`, `reminders_birthday`. O portão de cada
um: seus transcritos golden batem (com os deltas documentados), os contratos de
consumidores passam, os formatos de `expect_persisted` batem ou o upgrade de
`schema_version` documentado se aplica. Os pontos de entrada (`send_command_form_message`,
`send_command_manager_message`, `run_feature_command`) passam a chamar o adapter; os cogs
não mudam.

**Fase E — Apagar.** `app/views/form.py`, `form_state.py` (inclusive
`SessionAwareView`), `composition.py`, `edit.py`, `remove.py`, `manager.py`,
`manager_panel.py` (reconstruído como renderer de `Screen` sem proxies),
`panel_transitions.py` (absorvido pelo executor de `Replace`), `ActionCooldown`,
`keep_cancel_button_last`, os quatro parâmetros de hook, as constantes `COMPOSITION_*` e
`COMMAND_SERVICES`, as actions mortas, os helpers de desembrulhar, `off_loop`, os 12
pontos de emissão de analytics (substituídos pela tabela da `Decision`). `docs/form-configuration.md` é reescrito em torno
do contrato novo; `docs/analytics.md` §2 encolhe para um ponto só.

**Fase F — Prontidão para o builder.** `DefinitionSource` com implementação de rascunho
no banco, `Mode.DryRun`, `/admin preview`, renderer HTML de `Screen`. Só depois de E, e
só quando o produto decidir construir a UI ou o caminho de IA.

### O que este caminho de propósito não faz

- Não persiste sessões entre restarts (um restart ainda expira forms abertos, agora de
  forma visível). A interface `SessionStore` deixa isso como encaixe futuro se o produto
  pedir.
- Não mantém dois motores vivos por mais tempo que a Fase D. Uma flag por chave existe só
  dentro do trem de release e é apagada na Fase E.
- Não muda nenhuma copy voltada ao usuário além dos quatro deltas documentados da II.3.

### Esforço x ganho, revisto para "sem restrição"

A pergunta deixa de ser "qual etapa é barata" e vira "qual ordem mantém produção segura
enquanto a coisa inteira é construída". As fases A e B são a rede de segurança; C é onde
vai o capricho; D é a prova; E é a recompensa (o código perde umas 3.000 linhas de motor e
ganha um pacote que um contribuidor novo lê de cima para baixo); F é opcional e destrava
as ideias de produto da II.5.

## II.8 Respostas atualizadas às perguntas finais da Parte I

- **Q13 (por que ghost clicks)** — no `main`: o loop não é mais bloqueado nos pontos
  conhecidos; expiração é reportada mas continua invisível ao usuário; clique duplo em
  Done / Confirm, ids reusados e a transição no sentido embed continuam.
- **Q14 (duplicatas podem ser seguras)** — sem mudança; a trava é ponte da Fase A, dedup
  + revisão é o alvo.
- **Q20 (preservar)** — acrescentar à lista, como *superfícies e regras*, não como
  código: a mensagem de journey, o catálogo de analytics e suas camadas de
  armazenamento, o painel do manager com edição por seção, a regra de Components V2
  (flags fixas no envio), `.claude/rules/code-style.md`, o arquivo e a ferramenta de
  logs de debug, `RecordsBrowser` como view genérica, o modelo de ViewStore do harness.
  As implementações atuais são revisadas em II.0.b e rederivadas em II.7.
- **Q22 (melhor fundação para 20–50 features)** — continua Opção B; sem restrição de
  esforço o caminho é II.7, não §19.
- **Q25 (o que torna isso chato)** — mais: um transcrito golden por caminho, para "mudei
  o que o usuário vê?" ser um teste, não uma pergunta de review.
