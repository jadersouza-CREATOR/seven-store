# Seven Store

## Versão 0.6

Sistema de vendas Seven Store.

### Novidades da versão 0.6
- Vendas pendentes na tela de vendas.
- Vendas pendentes salvas no PostgreSQL e compartilhadas por caixa/usuário.
- A mesma venda pendente continua disponível ao entrar em outro celular ou computador com a mesma conta.
- Atualização automática da lista de pendentes.
- Continuar ou excluir venda pendente.
- Pix com QR Code e Copia e Cola.
- Integração de cobrança Pix com Mercado Pago.
- A venda Pix permanece pendente até o Mercado Pago confirmar o pagamento aprovado e o valor recebido ser igual ao valor da venda.
- Webhook `/webhook/pix` para atualização automática do pagamento.
- Caixas individuais por usuário.
- Relatórios de vendas.

### Variáveis do Render

Configure estas variáveis em **Render → Seven Store → Environment**. Nunca coloque o token real no GitHub.

```text
MERCADOPAGO_ACCESS_TOKEN=APP_USR-COLE_SEU_TOKEN_AQUI
MERCADOPAGO_PAYER_EMAIL=seu-email-de-cobranca@exemplo.com
PUBLIC_BASE_URL=https://seven-store.onrender.com
```

O arquivo `.env.example` contém apenas exemplos e não deve receber valores secretos reais.

### Fluxo Pix

1. O usuário escolhe Pix e gera a cobrança.
2. O Seven Store cria o pagamento no Mercado Pago e mostra o QR Code/Copia e Cola.
3. A venda fica `pendente` enquanto o pagamento não estiver aprovado.
4. O webhook consulta o pagamento diretamente no Mercado Pago.
5. A venda só muda para `concluida` quando o status for `approved`, o `payment_id` corresponder à cobrança e o valor recebido for exatamente igual ao valor da venda.
6. Pagamentos recusados, pendentes ou com valor diferente não concluem a venda.
