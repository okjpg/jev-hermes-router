---
name: relatorio
description: Relatório de economia do jev-hermes-router, por dia, semana ou mês.
---

# Relatório do Jev

Use quando o usuário perguntar quanto o router economizou, pedir o relatório do Jev, ou quiser saber se vale a pena manter o plugin ligado.

## Como puxar

Não calcule nada na mão. O número sai de um script puro, sem rede e sem LLM, que lê o log local do plugin:

```bash
python3 "${HERMES_HOME:-$HOME/.hermes}/plugins/jev-hermes-router/report.py" --janela 24h
```

Janelas: `24h` (padrão), `7d`, `30d`, `tudo`. Com vários perfis do Hermes na mesma máquina, acrescente `--todos-perfis`.

Na conversa, o atalho é o comando `/jev relatorio` (aceita `semana`, `mes`, `tudo`).

Devolva o texto do script como está. Se o usuário pedir interpretação, siga as regras abaixo.

## Como ler

- **Economia líquida vs modelo da sessão**: quanto o consumo caiu (ou subiu) contra rodar tudo no modelo que a sessão já usava. É a métrica principal.
- **↓ chamadas que desceram**: o ganho bruto do router. Mensagens simples indo pro modelo barato.
- **↑ chamadas que subiram**: o router mandou pro modelo mais forte. Isso custa mais cota de propósito, em troca de qualidade. Não é defeito.
- **Contra deixar tudo no máximo**: comparação útil pra quem hoje usa sempre o modelo mais forte.
- O dólar é preço de lista da API, usado como proxy de cota. Quem usa assinatura (ChatGPT/Claude) não paga por token; paga em limite semanal.

## Regras

- Número negativo não é bug. Significa que o dia teve mais pedido pesado do que simples. Diga isso em uma frase.
- Não prometa economia futura com base num dia só. Com menos de 50 mensagens, avise que a amostra é pequena.
- Não invente valores. Se o script disser "Nenhuma mensagem roteada", é isso que aconteceu.

## Relatório automático no fim do dia

Se o usuário quiser receber todo dia:

```bash
mkdir -p ~/.hermes/scripts
cp ~/.hermes/plugins/jev-hermes-router/cron/jev_relatorio.py ~/.hermes/scripts/
hermes cron create "0 21 * * *" --name jev-relatorio --no-agent \
  --script jev_relatorio.py --deliver origin --failure-deliver local
```

Confira o horário com `hermes cron list` (campo `Next run`) antes de dizer que está agendado. `--deliver origin` manda pro canal de onde o comando foi rodado; troque por `telegram` se o usuário preferir.
