# Eleições 2026 Bot — Telegram + TSE

Bot e painel web para acompanhar **resultados oficiais das Eleições 2026** pelo Tribunal Superior Eleitoral (TSE) e **pesquisas eleitorais publicadas no especial do G1**.

O projeto mantém apuração e pesquisa em áreas separadas. Não calcula probabilidades nem projeta resultados. A apuração reproduz os arquivos oficiais EA20 do TSE; as pesquisas reproduzem percentuais e metadados disponibilizados pelo G1.

## O que já vem pronto

- `/resultado` e `/brasil`: apuração presidencial nacional.
- `/estado MS`: apuração presidencial por UF.
- `/acompanhar [UF]`: cria uma mensagem que é **editada automaticamente** quando o TSE publica uma nova geração dos dados.
- `/parar`: interrompe atualização naquele chat.
- `/alertas on|off`: controla notificações nos marcos 10%, 25%, 50%, 75%, 90%, 95%, 99% e 100%.
- `/publicar [@canal]`: publica em canal/grupo e mantém a mensagem atualizada (bot precisa ser administrador). Se `CHANNEL_ID` estiver configurado, `/publicar` sem argumento usa esse canal.
- Canal obrigatório: quando `REQUIRED_CHANNEL` está definido, usuários precisam participar do canal para usar as consultas e o acompanhamento. Administradores em `ADMIN_IDS` não ficam bloqueados por essa verificação.
- `/status` e `/fonte`.
- `/pesquisas`: menu das pesquisas eleitorais.
- `/pesquisa presidente [datafolha|quaest]`.
- `/pesquisa governador UF [datafolha|quaest]`.
- `/pesquisa senador UF [datafolha|quaest]`.
- `/boletim`: envia manualmente ao canal a última pesquisa presidencial configurada.
- Monitor automático de novas pesquisas nacionais Datafolha/Quaest.
- Boletim diário no canal com a última pesquisa Datafolha disponível, por padrão às 09:00 em `America/Campo_Grande`.
- Painel web responsivo em `/`.
- API `GET /api/result?scope=br`.
- API `GET /api/g1/poll?office=presidente&scope=br&round=1&institute=datafolha`.
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
G1_DAILY_ENABLED=true
G1_DAILY_HOUR=9
G1_DAILY_MINUTE=0
G1_DAILY_INSTITUTE=Datafolha
G1_MONITOR_MINUTES=15
TIMEZONE=America/Campo_Grande
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
/pesquisas
/pesquisa presidente
/pesquisa presidente quaest
/pesquisa governador MS
/pesquisa senador MS
/boletim
/publicar @SeuCanal
```

Para restringir `/publicar`, use:

```env
ADMIN_IDS=123456789,987654321
CHANNEL_ID=@ResultadoEleicoes
REQUIRED_CHANNEL=@ResultadoEleicoes
```

## Fonte técnica

O parser foi escrito para o leiaute **EA20 — Arquivo de resultado unificado — Eleições 2026**, cuja hierarquia é `carg -> agr -> par -> cand`, com dados de seções em `s`, eleitores em `e` e votos em `v`.

A Justiça Eleitoral informa que a infraestrutura pública de resultados pode ser integrada por soluções próprias, respeitando limites e orientações técnicas. O projeto usa polling conservador (20 s por padrão) e validadores HTTP `ETag`/`Last-Modified`.


## Pesquisas G1

A integração lê a configuração pública de cada página do especial do G1 e, a partir dela, consulta diretamente a API JSON de gráficos usada pelo próprio site. Isso permite trabalhar com Presidente, Governador e Senador por UF sem manter IDs estaduais fixos no código.

O boletim diário é identificado como **última pesquisa disponível**; ele não afirma que uma nova pesquisa foi publicada naquele dia. Quando o monitor encontra uma nova rodada nacional de Datafolha ou Quaest, envia uma atualização separada ao canal.

Toda pesquisa é apresentada com data, instituto, margem de erro, tamanho da amostra e registro no TSE quando esses campos estão disponíveis na fonte. Pesquisa eleitoral e apuração oficial permanecem visualmente e tecnicamente separadas.
