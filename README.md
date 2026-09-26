# jev-hermes-router

Seu Hermes manda toda mensagem pro mesmo modelo, mesmo quando é só um "ok".
Este plugin escolhe, a cada mensagem, o modelo certo pra ela: leve pro trivial, máximo pro que importa.

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
hermes plugins install okjpg/jev-hermes-router
```

Abre o Hermes e roda:

```
/jev setup
```

São 3 perguntas. Leva um minuto.

## O que ele faz

**4 níveis.** Cada mensagem cai num nível, e o nível vira um modelo do provedor da sua sessão:

| Nível | Codex (login ChatGPT) | Anthropic (login Claude) |
|---|---|---|
| leve | gpt-6-luna | Haiku |
| padrão | gpt-6-terra | Sonnet |
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
- **Não economiza dólar na assinatura.** Economiza cota: mais mensagens antes de bater o limite semanal. Se você paga por token, a economia é em token.

## Privacidade

Pra decidir, o Jev lê **o texto que você digitou** e, por padrão, as 2 últimas coisas que você escreveu na sessão (cap de 2.000 caracteres). Nunca sai da sua máquina: resposta do agente, resultado de ferramenta, conteúdo de arquivo, system prompt.

Pra mandar só a mensagem atual: `/jev contexto off`.

Chave do Jev fica no `.env` do Hermes (`TYPESAFE_API_KEY` ou `OPENROUTER_API_KEY`). Não vai pra log, pra config nem pro Git.

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
/jev off | on     desliga / liga
/jev quieto       esconde a linha de rota
/jev contexto off | on
```

## Como a política decide

Uma chamada ao Jev por mensagem, 4 perguntas: `tier`, `effort`, `continuation`, `stakes`. O Jev devolve probabilidades; a decisão é código, não modelo:

1. Se `stakes ≥ 0,8`: nunca desce, esforço ≥ 1.
2. Se `continuation ≥ 0,5`: fica no nível anterior da sessão (`segue`).
3. Senão: desce um degrau se a massa de probabilidade até ele é ≥ 0,60; sobe pro degrau mais alto cuja massa a partir dele é ≥ 0,50.

Validado em 162 turnos reais de uma semana de uso antes de sair. A política toda está em `policy.py`, 170 linhas, sem rede.

## Rodar os testes

```
python3 -m unittest discover -s tests -v
```

26 testes, sem rede, sem Hermes.

## Lineage

Inspirado em [jev-model-router](https://github.com/satviksinha/jev-model-router) (plugin de Claude Code). De lá veio a lição do cache por modelo, o fail-open com timeout e a linha de rota visível. A política (massa de probabilidade, continuação, risco) e o contexto das 2 últimas mensagens são daqui.

MIT. Feito pra alunos da Pixel Educação, aberto pra quem quiser.
