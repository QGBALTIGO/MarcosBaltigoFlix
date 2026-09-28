# Eleições 2026 Bot — Telegram + TSE

Bot e painel web para acompanhar **resultados de Presidente nas Eleições 2026** usando os arquivos JSON oficiais de divulgação do Tribunal Superior Eleitoral (TSE).

O projeto não calcula probabilidades, não projeta vencedor e não altera dados eleitorais. Ele reproduz os números e estados informados nos arquivos oficiais EA20 do TSE.

## O que já vem pronto

- `/resultado` e `/brasil`: apuração presidencial nacional.
- `/estado MS`: apuração presidencial por UF.
- `/acompanhar [UF]`: cria uma mensagem que é **editada automaticamente** quando o TSE publica uma nova geração dos dados.
- `/parar`: interrompe atualização naquele chat.
- `/alertas on|off`: controla notificações nos marcos 10%, 25%, 50%, 75%, 90%, 95%, 99% e 100%.
- `/publicar [@canal]`: publica em canal/grupo e mantém a mensagem atualizada (bot precisa ser administrador). Se `CHANNEL_ID` estiver configurado, `/publicar` sem argumento usa esse canal.
- `/status` e `/fonte`.
- Painel web responsivo em `/`.
- API `GET /api/result?scope=br`.
- `ETag` e `Last-Modified` para evitar retransmissões desnecessárias.
- SQLite para persistir mensagens acompanhadas.
- Ambiente de **simulação 2026** ativado por padrão.
- Dockerfile + configuração para Railway.

## 1. Criar o bot no Telegram

No `@BotFather`:

1. `/newbot`
2. Escolha nome e username.
3. Copie o token.

Depois:

```bash
cp .env.example .env
```

Preencha:

```env
TELEGRAM_BOT_TOKEN=SEU_TOKEN
```

## 2. Rodar localmente

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abra `http://localhost:8000`.

## 3. Testar agora com o simulado do TSE

O padrão é:

```env
ELECTION_MODE=simulation
```

Nesse modo o projeto usa o ambiente publicado pelo TSE para o simulado 2026 e todas as telas deixam explícito que os números **não são votos reais**.

URL nacional usada no simulado:

```text
https://resultados-sim.tse.jus.br/simulado/simulado2026/ele2026/21270/dados/br/br-c0001-e021270-u.json
```

## 4. Trocar para a eleição oficial

No dia da eleição:

```env
ELECTION_MODE=official
```

O projeto passa a usar os parâmetros oficiais de 2026 (`resultados.tse.jus.br`, ambiente `oficial`, Eleição Geral Federal `6257`). Os parâmetros também podem ser sobrescritos individualmente no `.env` se o TSE publicar algum ajuste operacional.

## 5. Railway

Crie um serviço a partir deste repositório e adicione as variáveis:

```env
TELEGRAM_BOT_TOKEN=...
ELECTION_MODE=simulation
POLL_SECONDS=20
WEBAPP_URL=https://seu-dominio.up.railway.app
```

O projeto usa o `PORT` fornecido pelo Railway automaticamente.

### Persistência

Para não perder o SQLite em redeploys, monte um Volume do Railway em `/app/data`. Alternativamente, troque o armazenamento por Postgres.

## Comandos

```text
/start
/resultado
/brasil
/estado MS
/acompanhar
/acompanhar MS
/parar
/alertas on
/alertas off
/status
/fonte
/publicar @SeuCanal
```

Para restringir `/publicar`, use:

```env
ADMIN_IDS=123456789,987654321
CHANNEL_ID=@SeuCanal
```

## Fonte técnica

O parser foi escrito para o leiaute **EA20 — Arquivo de resultado unificado — Eleições 2026**, cuja hierarquia é `carg -> agr -> par -> cand`, com dados de seções em `s`, eleitores em `e` e votos em `v`.

A Justiça Eleitoral informa que a infraestrutura pública de resultados pode ser integrada por soluções próprias, respeitando limites e orientações técnicas. O projeto usa polling conservador (20 s por padrão) e validadores HTTP `ETag`/`Last-Modified`.
