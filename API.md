# API da Helena

Referência da API REST (+ WebSocket) pra quem quiser construir seu próprio
cliente (o app Android oficial e a [página web embutida](README.md#página-web-chat--configuração)
usam exatamente esta API — não há nada de especial reservado a eles).

## Convenções gerais

- **Base URL**: `http://<host>:<porta>` (default `http://localhost:5000`,
  ou o que estiver em `HELENA_PORT`/`HELENA_HOST`).
- **Tudo é JSON**: `Content-Type: application/json` nas requisições com
  corpo (exceto upload de mídia, que é `multipart/form-data`).
- **Erros**: corpo `{"error": "mensagem em português"}`, status HTTP
  apropriado (400 validação, 401 não autenticado, 403 proibido, 404 não
  encontrado, 409 conflito, 502 falha ao falar com um serviço externo).
  Algumas rotas mais antigas (`/reminders`) usam `{"ok": false, "error": ...}`
  em vez de só `{"error": ...}` — note o `ok` explícito nesses casos.
- **IDs e timestamps**: tudo `int`, timestamps sempre `datetime` ISO 8601
  em UTC (ex.: `"2026-07-17T13:41:38.374239+00:00"`).
- Rotas marcadas **🔒** exigem header `Authorization: Bearer <token>` (JWT).
  Sem token ou token de usuário apagado → `401`.

## Autenticação

### `POST /auth/register`

Body: `{"name": str, "email": str, "password": str}` (todos obrigatórios,
`password` mín. 6 caracteres, `email` precisa ter formato válido).

- `400` — campo faltando, email inválido, ou senha curta.
- `409` `{"error": "email já existe"}`
- `201` → `{"access_token": "<jwt>", "user": {...}}`

### `POST /auth/login`

Body: `{"email": str, "password": str}`.

- `400` — campo faltando.
- `401` `{"error": "credenciais inválidas"}`
- `200` → `{"access_token": "<jwt>", "user": {...}}`

O `user` devolvido em ambas: `{"id", "name", "email", "push_registered",
"notif_prefs", "default_browser", "created_at"}`.

O token não expira em pouco tempo (30 dias) — guarde e reuse; não há refresh
token, faça login de novo quando expirar (`401` em qualquer rota 🔒).

## Chat

### `GET /messages` 🔒

Histórico paginado, mais recentes primeiro na consulta mas devolvido em
**ordem cronológica**. Query params: `?limit=N` (default 50, máx. 200),
`?before=<id>` (pagina pra trás a partir desse id).

`200` → `{"messages": [Message, ...]}`

`Message`: `{"id", "role" ("user"|"assistant"|"tool"), "content", "media_url",
"media_type", "media_meta", "tool_name", "created_at"}`. `media_url` é só o
nome do arquivo — monte a URL de download como `GET /media/<seu_user_id>/<media_url>`.

### `GET /messages/info` 🔒

Perfil da conversa (estilo "info do contato"). `200` →
`{"started_at": iso|null, "total_messages": int, "media": [Message, ...]}`
(até 120 mensagens com mídia, mais recentes primeiro).

### `POST /messages` 🔒

Envia uma mensagem e **espera o turno inteiro do agente terminar** antes de
responder (pode levar dezenas de segundos se disparar tool-calling/pesquisa —
use um timeout de cliente generoso, 60-180s). Body: `{"content": str,
"media_url": str (opcional), "media_type": str (opcional), "media_meta": dict (opcional)}`
— pelo menos `content` ou `media_url`. Pra anexar mídia, faça
`POST /media/upload` primeiro e use o `media_url`/`media_type` devolvidos.

- `400` mensagem vazia (sem content nem mídia).
- `403` mídia de outro usuário; `404` mídia não encontrada.
- `503` `{"error": "..."}` se o provider ativo (Gemini ou Ollama) não estiver
  configurado.
- `201` → `{"message": Message, "replies": [Message, ...]}` — `replies` pode
  ter mais de uma mensagem (cada tool de geração — imagem/áudio/documento —
  insere a sua, e o texto final vira mais uma).

Um turno pode gerar efeitos que chegam **depois** da resposta HTTP (ex.: um
job em segundo plano) — veja [Tempo real](#tempo-real-websocket) pra pegar
essas mensagens sem dar polling.

## Mídia

### `POST /media/upload` 🔒

`multipart/form-data`, campo `file`. Salva e classifica, mas **não** anexa a
nenhuma mensagem ainda — use o retorno no próximo `POST /messages`.

- `400` sem arquivo, sem extensão, ou vazio.
- `201` → `{"media_url": str, "media_type": "image"|"audio"|"pdf"|..., "media_meta": {"mime", "original_name", "size"}}`

### `GET /media/<owner_id>/<filename>` 🔒

Baixa um arquivo. `owner_id` **precisa** ser o seu próprio user id (`403`
cross-user). `404` se não existir.

## Conta

- `GET /account/me` 🔒 → `{"user": {...}}` (mesmo shape do login).
- `PUT /account/basic-info` 🔒 — body parcial `{"name", "email"}`. `409` se
  email já usado por outra conta. `200` → `{"ok": true, "user": {...}}`.
- `PUT /account/name` 🔒 — body `{"name": str}` (obrigatório) — how a Helena
  te chama (`nome_preferido`, separado do nome da conta). `200` →
  `{"ok": true, "name": str}`.
- `PUT /account/notif-prefs` 🔒 — body é o objeto de preferências inteiro
  (substitui, não mescla). `200` → `{"notif_prefs": {...}}`.
- `GET /account/browsers` 🔒 → `{"installed": [{"id","name",...}], "default": str|null}`.
- `PUT /account/browsers/default` 🔒 — body `{"browser_id": str|null}`. `400`
  se não estiver na lista de instalados. `200` → `{"ok": true, "default": ...}`.
- `GET /account/audit` 🔒 — `?limit=N` (default 100, máx. 500). O que a
  Helena executou na máquina (shell/desktop). `200` → `{"entries": [...]}`.
- `POST /account/panic` 🔒 — kill switch: revoga `principal`/`fullcontrol` e
  pausa federação da SUA conta. `200` → `{"ok": true, "message": "..."}`.
- `POST /account/reset-chat` 🔒 — apaga só as mensagens. `200` → `{"ok": true, "action": "reset-chat"}`.
- `POST /account/reset-context` 🔒 — apaga mensagens + resumo + notas + perfil
  (mantém lembretes). `200` → `{"ok": true, "action": "reset-context"}`.
- `POST /account/wipe` 🔒 — body `{"deleteAccount": bool}`. Apaga TUDO da
  conta (+ arquivos de mídia no disco); `deleteAccount=true` também remove o
  usuário. `200` → `{"ok": true, "action": "wipe", "account_deleted": bool}`.
  **Irreversível** — confirme na UI antes de chamar.

## Lembretes e notificações

- `GET /reminders` 🔒 → `{"ok": true, "reminders": [Reminder, ...]}`
- `POST /reminders` 🔒 — body `{"title", "due_at" (ISO), "kind": "agenda"|"simple" (default "simple"), "recurrence": "daily"|"weekly"|"monthly"|"yearly" (opcional), "notify_at" (opcional), "description" (opcional), "origin" (opcional)}`.
  `400` `{"ok": false, "error": ...}` em validação. `201` →
  `{"ok": true, "reminder_id": int, "due_at": iso}` — **note que não devolve
  o reminder inteiro**, só o id.
- `PUT /reminders/<id>` 🔒 — body parcial. **Toda falha (inclusive validação)
  devolve `404`** `{"ok": false, "error": ...}`, não `400`. Sucesso `200` →
  `{"ok": true, "reminder": Reminder}`.
- `DELETE /reminders/<id>` 🔒 → `200 {"ok": true, "deleted": id}` ou
  `404 {"ok": false, "error": "lembrete não encontrado"}`.
- `GET /notifications/pending` 🔒 — fila pré-calculada pro cliente
  materializar localmente (offline-first): itens com `fire_at` nas próximas
  24h ainda não confirmados. `200` → `{"notifications": [{"id","title","body","fire_at","type","reference_id","delivered","created_at"}]}`.
- `POST /notifications/ack` 🔒 — body `{"ids": [int, ...]}`. Marca como
  entregues (o cliente já materializou a notificação local). `200` →
  `{"ok": true, "acked": <quantidade>}`.

`Reminder`: `{"id","title","description","due_at","origin","kind","recurrence","notified_1w","notified_1d","notified_6h","notify_at","notified","created_at"}`.

## Comandos salvos e rotinas

Atalhos nomeados que o usuário pré-aprova (`created_by="user"`) pra Helena
rodar por nome, sem passar pelo card de aprovação toda vez.

- `GET /saved-commands` 🔒 → `{"commands": [SavedCommand, ...]}`
- `POST /saved-commands` 🔒 — `{"name", "command", "description" (opcional)}`. `409` nome duplicado.
- `PUT /saved-commands/<id>` 🔒 — parcial. `DELETE /saved-commands/<id>` 🔒.
- `GET /routines` 🔒 → `{"routines": [Routine, ...]}`
- `POST /routines` 🔒 — `{"name", "steps": [{"kind": "command"|"shell", "value"}] (≥1), "description" (opcional)}`.
- `PUT /routines/<id>` 🔒 — parcial (`steps` só substitui se a lista limpa não ficar vazia). `DELETE /routines/<id>` 🔒.

`SavedCommand`: `{"id","name","description","command","created_by","created_at","updated_at"}`.
`Routine`: `{"id","name","description","steps","created_by","enabled","next_run","recurrence","created_at","updated_at"}`.

## Aprovação de shell

Quando um usuário `principal` (não `fullcontrol`) pede pra Helena rodar um
comando, ela **não executa na hora** — cria um card pendente na conversa e
espera a decisão do usuário por aqui.

- `GET /commands/approvals` 🔒 → `{"approvals": [{"id","command","created_at"}]}` — lista de "permitir sempre" já concedidos.
- `DELETE /commands/approvals/<id>` 🔒 → revoga um "sempre". `200 {"ok": true}` / `404`.
- `POST /commands/<cmd_id>/decision` 🔒 — body `{"decision": "allow"|"deny"|"always"}`.
  - `400` decision inválida. `404` comando não encontrado/não seu.
  - `409` `{"error": "comando já foi decidido"}` — claim atômico, então
    reenviar a mesma decisão duas vezes (ex.: retry de rede) não executa em
    dobro.
  - `200` → `{"messages": [Message, ...]}` (saída do comando + resposta da
    Helena). O mesmo lote também chega via WebSocket (`new_messages`) — dá
    pra confiar em qualquer um dos dois, dedupe pelo `id` da mensagem.
  - O texto do comando nunca é enviado pelo cliente — é o que já foi
    registrado no servidor pra esse `cmd_id`.

## Configuração (`/settings`)

Equivalente web do CLI (`helena setup`/`config`/`provider`/`models`) — dá
pra ler/mudar o `.env` do servidor (provider Gemini/Ollama, modelo, porta,
etc.) e reiniciar o servidor pela API. Documentado em detalhe no código-fonte
(`app/blueprints/settings.py`) porque é mais uma ferramenta de administração
que parte do contrato "de cliente de chat" — mas usa a MESMA auth 🔒 (sem
tier de permissão extra) e é REST simples:

- `GET /settings` 🔒 → `{"values": {...}, "secrets": [...], "info": {...}}` (segredos mascarados).
- `PUT /settings` 🔒 — body parcial `{"CHAVE": "valor", ...}`; `400` se
  alguma chave não estiver na allowlist editável. Valor vazio num campo
  secreto = "não mexer" (nunca apaga).
- `GET /settings/ollama/models` 🔒 → catálogo de modelos locais + hardware detectado.
- `POST /settings/ollama/pull` `{"name"}` 🔒 → dispara download em
  background (`202`); acompanhe com `GET /settings/ollama/pull/status?name=`.
- `POST /settings/ollama/test` `{"name" (opcional)}` 🔒 → `{"ok", "detail"}`, testa geração de verdade.
- `POST /settings/restart` 🔒 → reinicia o servidor (`202`, a conexão cai em seguida — é esperado).

## Federação (Helena-a-Helena)

Avançado — comunicação assinada entre instâncias diferentes da Helena
(cada uma com seu próprio dono). A maioria dos clientes não precisa disso;
documentado aqui pra completude.

- `GET /federation/settings` 🔒 → `{"public_url_configured", "paused"}`
- `POST /federation/resume` 🔒 → sai do modo pânico de federação.
- `POST /federation/peers/pairing-codes` 🔒 → gera um código pra dar a outra
  instância parear. `403` se pausado.
- `POST /federation/peers` 🔒 — `{"code", "base_url"}`, resgata um código de
  outra instância (pareamento outbound). `502` em falha de rede/protocolo.
- `GET /federation/peers` 🔒 / `PUT /federation/peers/<id>` 🔒 (`label`,
  `trust_level` ∈ `confiavel|nao_confiavel|a_averiguar`, `ai_dialogue_enabled`,
  `ai_can_initiate` — forçado a `false` se `trust_level != confiavel`) /
  `DELETE /federation/peers/<id>` 🔒.
- `GET /federation/peers/<id>/messages` 🔒 → histórico com aquele peer.
- `POST /federation/peers/<id>/messages` 🔒 — `{"body", "reply_to_message_id" (opcional)}`.
  `502` se a entrega falhar (mensagem fica `status="failed"`, ainda `201`
  com o objeto pra você ver o status).
- `POST /federation/pairing/redeem` e `POST /federation/webhook/message` —
  **rotas públicas server-to-server**, não usam JWT (assinatura HMAC própria)
  — não chame essas do seu cliente de usuário.

`Peer`: `{"id","link_id","remote_base_url","label","trust_level","ai_dialogue_enabled","ai_can_initiate","created_at"}`.
`PeerMessage`: `{"id","peer_id","direction","body","status","authored_by","kind","request_id","in_reply_to","verified_request_message_id","created_at"}`.

## Tempo real (WebSocket)

Socket.IO em `/socket.io` — o servidor **não** serve o arquivo do cliente
(`socket.io.js`), traga o seu (`npm install socket.io-client`, ou via CDN).
Servidor roda `python-socketio` 5.x / protocolo Socket.IO v4 — use um
cliente compatível com a v4 (a maioria das libs atuais é). Autentica no
`connect`, sem HTTP intermediário:

```js
const socket = io("http://localhost:5000", { auth: { token: accessToken } });
```

Sem `token` válido, o servidor recusa a conexão. Cada usuário entra numa
room própria (pelo id) — eventos só chegam pros seus próprios dados.

| Evento | Payload | Quando |
|---|---|---|
| `new_messages` | `{"messages": [Message, ...]}` | Mensagens novas de qualquer origem que não seja o próprio POST síncrono do cliente (ex.: decisão de shell, resposta vinda de notificação) — dedupe pelo `id`. |
| `job_progress` | `{"text": str}` | Feedback ao vivo de um job em segundo plano (pesquisa/tarefa de desktop) — efêmero, não fica no histórico. |
| `job_done` | `{"message": Message}` | Job em segundo plano terminou. |
| `peer_paired` | `{"peer": Peer}` | Um pareamento de federação se concretizou. |
| `peer_message` | `{"message": PeerMessage}` | Mensagem nova de um peer federado. |

Se seu cliente não quiser lidar com WebSocket, tudo continua funcionando só
com `POST /messages` (síncrono) + `GET /notifications/pending` (polling
offline-first) — é assim que o `chat_cli.py` deste repositório funciona,
sem nenhum WebSocket.

## Health check

`GET /health` — sem auth, sem `/`. `200 {"status": "ok"}`. Use pra saber se
o servidor está de pé (ex.: depois de pedir `POST /settings/restart`).

## Exemplo mínimo (curl)

```bash
BASE=http://localhost:5000

TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"voce@exemplo.com","password":"sua-senha"}' | jq -r .access_token)

curl -s -X POST "$BASE/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"oi, helena"}' | jq
```
