# jev-hermes-router

Seu Hermes manda toda mensagem pro mesmo modelo, mesmo quando é só um "ok".
Este plugin escolhe, a cada mensagem, o modelo certo pra ela: leve pro trivial, máximo pro que importa.

(No Codex por assinatura só existem luna, sol e astra; o nível "padrão" roda em luna com esforço alto.)

Quem escolhe é o [Jev](https://typesafe.ai), um modelo de decisão da TypeSafe. Ele não escreve nada, só aponta o dedo. Custa fração de centavo por mensagem (US$ 0,00003; mil mensagens dão 3 centavos de dólar).

```
❯ oi
⚙️ leve · esforço 0 · 99% · 269ms
Oi! Tudo bem?

❯ planeja a arquitetura de um segundo cérebro pra um empreendedor
⚙️ maximo · esforço 3 · 100% · 333ms
Desenho abaixo...

❯ ok
⚙️ maximo · esforço 2 · 94% · segue · 425ms
Falou.
```

O "ok" **não desceu** pro modelo leve. O router entendeu que era continuação da conversa pesada e ficou no modelo que já tinha o contexto quente. Trocar de modelo esfria o cache e custa mais uma vez; um roteador que troca a cada "sim" gasta mais do que economiza.

## Instalar

Precisa de Hermes 0.21.4 ou mais novo, com login por assinatura (ChatGPT ou Claude).

```
curl -fsSL https://raw.githubusercontent.com/okjpg/jev-hermes-router/main/install.sh | sh
```

O script clona em `~/.hermes/plugins/jev-hermes-router` e ativa. Ele existe porque `hermes plugins install` no Hermes 0.21.x cai num bug do gerenciador de plugins (procura `pm/uv.lock` no lugar errado); o script contorna isso. Quando o bug for corrigido, `hermes plugins install okjpg/jev-hermes-router` vai funcionar direto.

Prefere ver antes de rodar? [install.sh](install.sh) tem 40 linhas.

Depois, abre o Hermes numa sessão nova e roda:

```
/jev setup
```

São 3 perguntas. Leva um minuto. A chave do Jev vai no `~/.hermes/.env` como `TYPESAFE_API_KEY=...` (o wizard te diz onde pegar).

Depois de alguns dias de uso, veja quanto economizou:

```
/jev relatorio          últimas 24h
/jev relatorio semana   últimos 7 dias
```

Ou peça em linguagem natural ("quanto o Jev economizou essa semana?"): o plugin traz a skill `jev-hermes-router:relatorio`, que sabe puxar e ler o relatório. Pra receber todo dia no fim da tarde, veja [Relatório diário](#relatório-diário).

## O que ele faz

**4 níveis.** Cada mensagem cai num nível, e o nível vira um modelo do provedor da sua sessão:

| Nível | Codex (login ChatGPT) | Anthropic (login Claude) |
|---|---|---|
| leve | gpt-6-luna | Haiku |
| padrão | gpt-6-luna (esforço alto) | Sonnet |
| pesado | gpt-6-sol | Opus |
| máximo | gpt-6-astra | Fable |

**Esforço.** Além do modelo, o Jev diz quanto ele deve pensar (0 a 3). Vira `reasoning.effort` no Codex e `output_config.effort` / budget de thinking na Anthropic.

**Continuação.** "Sim", "manda bala", "ok agora aplica": o router segura no modelo que fez o plano. Quem já fez a conversa pesada continua a conversa pesada. Aparece como `segue` na linha.

**Risco.** Enviar, apagar, publicar, deployar: nunca cai pro modelo mais fraco, e o esforço mínimo é 1.

**Falha aberta.** O Jev demorou, a chave venceu, a internet caiu: sua mensagem roda no modelo de sempre e a linha diz o motivo. O router nunca segura uma mensagem sua.

## O que ele NÃO faz

- **Não troca de provedor.** Sessão no ChatGPT fica no ChatGPT; sessão no Claude fica no Claude. Misturar quebra o histórico de raciocínio e zera o cache.
- **Não roteia cron, subagente, worker de Kanban** nem chamadas auxiliares (título, compressão). Só a mensagem que você digita.
- **Não funciona com chave de API ou OpenRouter no LLM.** O router existe pra fazer sua assinatura render mais; com chave, a economia é outra conversa. Ele detecta e fica desligado.
- **Não economiza dólar na assinatura.** O objetivo é poupar cota: modelo menor nas mensagens simples, mais mensagens antes do limite semanal. Quanto exatamente, ninguém mede por fora (veja [Como a conta é feita](#como-a-conta-é-feita)). Se você paga por token, a economia é em token.

## Privacidade

Pra decidir, o Jev lê **o texto que você digitou** e, por padrão, as 2 últimas coisas que você escreveu na sessão (cap de 2.000 caracteres). Nunca sai da sua máquina: resposta do agente, resultado de ferramenta, conteúdo de arquivo, system prompt.

Pra mandar só a mensagem atual: `/jev contexto off`.

Chave do Jev fica no `.env` do Hermes (`TYPESAFE_API_KEY` ou `OPENROUTER_API_KEY`). Não vai pra log, pra config nem pro Git.

O log local (`~/.hermes/plugin-data/jev-hermes-router/usage.jsonl`) guarda só números: modelo, tokens, a decisão e as probabilidades que o Jev devolveu. Nenhum texto seu ou do agente vai pra ele.

## Onde o Jev roda

| | TypeSafe direto | OpenRouter |
|---|---|---|
| Conta nova | sim, em console.typesafe.ai | não, usa a que já tem |
| Custo inicial | US$ 5 de crédito grátis | precisa ter saldo |
| Preço | US$ 0,042 por milhão de tokens de entrada | mesmo modelo, OpenRouter repassa |
| Velocidade | ~0,3 s | ~0,6 s |
| Rota | `v1/systemone`, estável | `/api/alpha/decisions`, alpha |

Isso é só pro Jev. Seu ChatGPT ou Claude continua no login de sempre.

## Comandos

```
/jev              status, últimos 10 turnos, trocas, cache, degraus economizados
/jev setup        refaz as 3 perguntas
/jev doctor       testa chave, provedor, versão do Hermes
/jev stats        totais desde a instalação
/jev relatorio    economia estimada: 24h (padrão), semana, mes, tudo
/jev off | on     desliga / liga
/jev quieto       esconde a linha de rota
/jev contexto off | on
```

## Relatório diário

O relatório sai de `report.py`: um script puro, sem rede e sem LLM, que lê o log local do plugin (`~/.hermes/plugin-data/jev-hermes-router/usage.jsonl`). Pra agendar no fim do dia:

```
mkdir -p ~/.hermes/scripts
cp ~/.hermes/plugins/jev-hermes-router/cron/jev_relatorio.py ~/.hermes/scripts/
hermes cron create "0 21 * * *" --name jev-relatorio --no-agent \
  --script jev_relatorio.py --deliver origin --failure-deliver local
```

Confira o horário real com `hermes cron list` (campo `Next run`). O relatório fala todo dia, mesmo sem mensagem roteada: silêncio seria igual a cron quebrado. Com vários perfis do Hermes, ele soma cada um separado.

Exemplo de saída real (perfil de teste, 27/09/2026):

```
⚙️ Jev · últimas 24h
17 mensagens, 36 chamadas ao modelo · leve 3 · padrao 8 · pesado 2 · maximo 4
Consumo: US$ 1,75 equivalentes, contra US$ 1,18 se tudo rodasse no modelo da sessão
Gasto extra líquido vs modelo da sessão: 49% (já cobra o cache perdido nas trocas)
  ↓ 19 chamadas desceram de modelo: US$ 0,09 em vez de US$ 0,42 (77% a menos)
  ↑ 2 chamadas subiram de modelo: US$ 0,57 em vez de US$ 0,02 (+US$ 0,55 pra ter o modelo mais forte)
Contra deixar tudo no modelo máximo: 24% a menos
Trocas de modelo: 9 · cache lido: 73% · Jev: 330 ms, US$ 0,0006
Obs.: amostra pequena (17 mensagens), não tire conclusão ainda; 27 chamadas antigas sem tokens de saída, contadas só pela entrada.
```

### Como a conta é feita

Assinatura não cobra por token, cobra em cota, e não existe API pública de cota. O relatório usa o **preço de lista da API** de cada modelo como proxy e compara, chamada por chamada:

- **real**: os tokens que rodaram, no preço do modelo que o router escolheu, mais o custo do Jev;
- **sem Jev**: os mesmos tokens no modelo da sessão, **com cache sempre quente**.

A segunda conta é generosa com o "sem Jev" de propósito: toda troca de modelo esfria o cache, e o relatório cobra isso do router.

Dois limites dessa conta: preço de API não é cota de assinatura (não existe tabela pública que converta um no outro), e ela assume que o outro modelo gastaria os mesmos tokens, o que nem sempre é verdade. Leia o dólar como **custo equivalente em preço de API**, não como economia na sua fatura.

## Quanto economiza (dados reais)

Medido em 17 mensagens reais, num perfil de teste com sessão no `gpt-6-sol` (Codex por assinatura), em 26 e 27/09/2026. Conteúdo, revisão de contrato, resumo de e-mail, pergunta técnica, "ok" e "valeu". Valores em custo equivalente de preço de API.

| Métrica | Dia com log completo (6 mensagens) | Total (17 mensagens)* |
|---|---|---|
| Chamadas que desceram de modelo | **95% menos** | 77% menos |
| Contra usar sempre o modelo máximo | 47% menos | 24% menos |
| Líquido contra o modelo da sessão (sol) | 162% a mais | 49% a mais |

\* 27 das 36 chamadas do total são de antes da correção do log e não têm tokens de saída, que são a parte mais cara. A coluna total é só indicativa.

O que os dados sustentam hoje: **até 95% menos custo equivalente em preço de API nas mensagens simples**. Nenhum percentual geral ainda.

Leitura honesta: o router economiza muito no que é simples e **gasta mais no que é difícil**, porque manda revisão de contrato e arquitetura pro modelo mais forte. Se sua sessão já fica no modelo máximo, ele só economiza. Se fica no intermediário, ele troca custo por qualidade nas mensagens que pedem isso. O relatório separa as duas coisas (↓ e ↑) pra você ver o que está pagando.

A amostra é pequena, e ainda não medimos se descer de modelo piora a resposta. Próximos números vão sair separados por modelo de partida da sessão, junto com um teste pareado de qualidade (a mesma mensagem no modelo barato e no da sessão, comparadas às cegas).

## O que aprendemos testando

Coisas que só apareceram rodando de verdade. Se você vai mexer no plugin, leia antes.

**1. Nem todo modelo do ladder existe na sua conta.** A primeira versão mandava o nível "padrão" pro `gpt-6-terra`. A assinatura ChatGPT não dá acesso a ele: `HTTP 400 · The 'gpt-6-terra' model is not supported when using Codex with a ChatGPT account`. O Hermes marca o modelo como indisponível na sessão e o turno morre. Correção: no Codex, "padrão" é `gpt-6-luna` com esforço alto. Antes de pôr um modelo no ladder, teste com a conta que o aluno vai usar, não com a sua.

**2. A linha precisa mostrar o que foi pro provedor, não o que o Jev sugeriu.** Com o "padrão" virando luna+high, a linha continuava dizendo `esforço 0` e o log gravava `low`, enquanto o request ia com `high`. Quem usava a linha como dado de teste lia errado. Agora a linha e o log mostram o esforço aplicado, e o log guarda a sugestão do Jev separada (`jev_effort`).

**3. Confira o nome dos campos do runtime.** O Hermes entrega uso de tokens como `output_tokens`, não `completion_tokens`. O plugin gravava `null` em toda saída e ninguém percebeu até tentar calcular custo. Sem token de saída, não há relatório de economia. Teste o formato real, não o que você acha que ele é.

**4. O contador de uso do Hermes não enxerga a troca.** O painel de uso da sessão atribui todos os tokens ao modelo da sessão, mesmo quando o router mandou pra outro. Pra medir o router, use `/jev relatorio`. O modelo que respondeu de verdade vem de `response_model` no `post_api_request`, e é esse que o log grava.

**5. Trocar de modelo custa.** Em toda troca, a primeira chamada chega com cache zerado e o raciocínio do modelo anterior é descartado (`encrypted_content is sealed to its issuer`). Por isso a política segura "ok", "sim", "manda bala" no modelo que já estava (`segue`), e por isso o relatório cobra o cache frio do router.

**6. Escalar é uma escolha, não um bug.** Numa sessão em `sol`, o router mandou revisão de contrato e desenho de sistema pro `astra`. A resposta ficou melhor e a conta ficou maior. Um roteador que só desce é um redutor de custo; este também sobe. Se você quer só economia, deixe a sessão no modelo máximo e o router vai apenas descer.

**7. Luna escreve genérico.** Um post de LinkedIn de 150 palavras caiu em "padrão" e saiu correto, mas com frase pronta ("Não é sobre X. É sobre Y."). Pra texto que precisa soar como você, peça explicitamente no seu tom ou com referência: o Jev lê a natureza da tarefa, e "no meu tom, com estes exemplos" puxa pra `pesado`.

**8. Uma sessão por vez no mesmo perfil.** Duas instalações abrindo o mesmo perfil ao mesmo tempo (Desktop e terminal) compartilham config e dados. O Hermes avisa; o plugin guarda estado de sessão em memória, então cada processo decide sozinho. Não quebra nada, mas o `/jev` de um não vê o outro. O relatório lê o arquivo e vê os dois.

## Como a política decide

Uma chamada ao Jev por mensagem, 4 perguntas: `tier`, `effort`, `continuation`, `stakes`. O Jev devolve probabilidades; a decisão é código, não modelo:

1. Se `stakes ≥ 0,8`: nunca desce, esforço ≥ 1.
2. Se `continuation ≥ 0,5`: fica no nível anterior da sessão (`segue`).
3. Senão: desce um degrau se a massa de probabilidade até ele é ≥ 0,60; sobe pro degrau mais alto cuja massa a partir dele é ≥ 0,50.

Validado em 162 turnos reais de uma semana de uso antes de sair. A política toda está em `policy.py`, sem rede.

Desde a 0.3.1, cada mensagem grava no log os sinais crus do Jev (`jev`: probabilidades, continuação, risco, esforço, degrau de partida e a versão dos limiares em `policy`). Com isso dá pra testar outros limiares sobre o seu histórico sem chamar o Jev de novo: `pol.decide(pol.answers_from_replay(linha["jev"]), ...)` reproduz a decisão original.

## Rodar os testes

```
python3 -m unittest discover -s tests -v
```

35 testes, sem rede, sem Hermes.

## Lineage

Inspirado em [jev-model-router](https://github.com/satviksinha/jev-model-router) (plugin de Claude Code). De lá veio a lição do cache por modelo, o fail-open com timeout e a linha de rota visível. A política (massa de probabilidade, continuação, risco) e o contexto das 2 últimas mensagens são daqui.

MIT. Feito pra alunos da Pixel Educação, aberto pra quem quiser.
