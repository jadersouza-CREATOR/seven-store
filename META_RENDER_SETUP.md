# Configuração da Meta no Render — Seven Store

## 1. Variáveis do Render

O `render.yaml` já declara:

- `META_APP_ID` — ID do aplicativo criado no Meta for Developers.
- `META_APP_SECRET` — segredo do aplicativo. Não publique no GitHub.
- `META_VERIFY_TOKEN` — um texto secreto escolhido por você para validar os webhooks.
- `META_REDIRECT_URI` — `https://seven-store.onrender.com/meta/callback`.
- `META_GRAPH_VERSION` — versão da Graph API usada pelo sistema.

As três primeiras são marcadas como `sync: false`, então os valores devem ser informados como secrets/env vars no Render.

## 2. URL de retorno OAuth

Cadastre exatamente esta URL no aplicativo da Meta:

`https://seven-store.onrender.com/meta/callback`

Se você mudar o domínio do Seven Store, atualize `META_REDIRECT_URI` e a URL cadastrada no aplicativo da Meta.

## 3. Webhooks

WhatsApp:

`https://seven-store.onrender.com/webhook/whatsapp`

Instagram:

`https://seven-store.onrender.com/webhook/instagram`

Use o mesmo valor de `META_VERIFY_TOKEN` na configuração de verificação do webhook.

## 4. Fluxo do Seven Store

1. O usuário entra em Configurações.
2. Clica em **Conectar Instagram com a Meta** ou **Conectar WhatsApp com a Meta**.
3. O Seven Store gera um `state` aleatório e redireciona para a autorização oficial da Meta.
4. A Meta retorna para `/meta/callback`.
5. O Seven Store valida o `state` e troca o código por um access token.
6. Para Instagram, o sistema procura uma Página com Instagram profissional vinculado.
7. Para WhatsApp, o sistema procura uma conta WhatsApp Business e o primeiro número disponível.
8. Os dados da integração são salvos no PostgreSQL para a Central de Atendimento.

## 5. Segurança

Nunca coloque `META_APP_SECRET`, access tokens ou outros segredos no código, no HTML ou em commits públicos.

O login do Instagram/WhatsApp é feito pela Meta; o Seven Store não solicita a senha da conta.

## 6. Observação

A aprovação das permissões e a disponibilidade das APIs dependem da configuração e do estado do aplicativo no Meta for Developers. O código do Seven Store não substitui essa configuração externa.
