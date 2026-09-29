# Resultado Eleições 2026

Bot Telegram + painel web para acompanhar duas fontes distintas:

1. **Apuração oficial do TSE** — votos totalizados no dia da eleição.
2. **Pesquisas eleitorais do especial do G1** — intenção de voto publicada por Datafolha/Quaest, com histórico, metodologia, margem de erro e registro no TSE quando disponível.

O projeto mantém as duas coisas explicitamente separadas. Pesquisa eleitoral nunca é apresentada como apuração ou previsão.

## Telegram

Comandos principais:

```text
/start
/resultado
/estado MS
/acompanhar
/parar
/alertas on
/pesquisas
/pesquisa presidente
/pesquisa presidente quaest
/pesquisa governador MS
/pesquisa governador SP datafolha
/pesquisa senador MS
/governador MS
/senador MS
/boletim
/publicar
/status
/fonte
```

O bot exige participação em `REQUIRED_CHANNEL` quando configurado.


## Colinha e treinamento de urna

O painel web possui uma aba **Colinha** voltada a organização pessoal dos números para as Eleições 2026.

Recursos incluídos:
- seleção da UF e preenchimento na ordem de votação;
- busca de candidatura por nome, número ou partido;
- deputado federal, deputado estadual/distrital, duas vagas de senador, governador e presidente;
- persistência local da colinha;
- geração de imagem e compartilhamento;
- alerta para repetição da mesma candidatura nas duas vagas do Senado;
- treinamento em uma interface inspirada na urna, com `BRANCO`, `CORRIGE` e `CONFIRMA`;
- conferência do número digitado contra a própria colinha e resumo ao final do treino.

A funcionalidade usa as mesmas candidaturas já carregadas pelo painel e é apresentada como ferramenta independente de organização e treinamento. Ela não registra votos e não se apresenta como serviço oficial da Justiça Eleitoral.

## Pesquisas G1

A integração lê a configuração que o próprio especial do G1 publica em `window.g1PesquisasEleitorais` e consulta a API pública de gráficos:

```text
https://especiaisg1.globo/api/pesquisas-eleitorais/graficos/{paginaId}/
```

Os parâmetros `tipo_pergunta` e `instituto` são obtidos da própria página. Isso evita scraping dos percentuais visuais.

O painel permite consultar **Presidente, Governador e Senador**, selecionar UF e escolher Datafolha/Quaest quando houver levantamento disponível.

### Automação do canal

Por padrão:
- publica **um boletim diário às 09:00** em `America/Campo_Grande`;
- o boletim diário usa Datafolha para Presidente/Brasil;
- verifica novas rodadas de Datafolha e Quaest a cada 15 minutos;
- quando a fonte muda, publica uma mensagem de **nova pesquisa**;
- fingerprints e o controle do boletim diário ficam persistidos no SQLite para evitar duplicatas.

Variáveis:

```env
G1_DAILY_ENABLED=true
G1_DAILY_HOUR=9
G1_DAILY_MINUTE=0
G1_DAILY_INSTITUTE=Datafolha
G1_MONITOR_MINUTES=15
TIMEZONE=America/Campo_Grande
```

## Apuração TSE

Durante os testes:

```env
ELECTION_MODE=simulation
```

No dia da eleição, após validar os parâmetros oficiais do TSE:

```env
ELECTION_MODE=official
```

O cliente usa os arquivos EA20 e validadores HTTP `ETag`/`Last-Modified`.

## Railway

O serviço é compatível com Docker/Railway. Para persistir o SQLite entre deploys, monte um Volume em:

```text
/app/data
```

O token do Telegram e demais segredos devem ficar apenas nas variáveis do Railway, nunca no GitHub.
