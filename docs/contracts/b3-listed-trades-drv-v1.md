# B3 Negocio a Negocio - Listados, perfil DRV v1

## Escopo

Este contrato descreve o adaptador versionado `b3-listed-trades-drv/v1` para o arquivo
`DD-MM-AAAA_NEGOCIOSAVISTA_DRV.zip`. Ele converte um unico instrumento selecionado para o
CSV Canonico v1. Nao define contrato vigente, serie continua ou rollover.

O perfil DRV foi derivado do arquivo oficial recebido para 10/09/2026. A extensao
`TipoDoCanal` e aceita e preservada literalmente, mas nao possui semantica no sistema: ela nao
participa de filtros, ordenacao ou regras quantitativas.

## Envelope e layout

- A entrada e um ZIP aberto somente para leitura.
- O nome deve seguir `DD-MM-AAAA_NEGOCIOSAVISTA_DRV.zip`.
- O ZIP deve conter exatamente um TXT de mesmo nome-base.
- O TXT usa UTF-8, com BOM opcional, separador `;` e cabecalho estrito.

O cabecalho esperado e, exatamente:

```text
DataReferencia;CodigoInstrumento;AcaoAtualizacao;PrecoNegocio;QuantidadeNegociada;HoraFechamento;CodigoIdentificadorNegocio;TipoSessaoPregao;DataNegocio;CodigoParticipanteComprador;CodigoParticipanteVendedor;TipoDoCanal
```

Colunas adicionais ou ausentes sao schema drift e interrompem a importacao. Uma evolucao de
layout requer outro perfil versionado.

## Mapeamento canonico

| B3 | CSV Canonico v1 | Regra |
| --- | --- | --- |
| `CodigoInstrumento` | `symbol` | Igualdade exata com o contrato solicitado. |
| `DataNegocio` + `HoraFechamento` | `timestamp` | ISO-8601 em `-03:00`; internamente UTC. |
| ordem da linha de dados | `source_sequence` | Indice zero-based no TXT, sem considerar o cabecalho. |
| `PrecoNegocio` | `price` | Decimal positivo no instrumento selecionado; virgula e convertida para ponto sem `float`. |
| `QuantidadeNegociada` | `quantity` | Inteiro positivo de 64 bits. |

`DataReferencia`, `CodigoIdentificadorNegocio`, `TipoSessaoPregao`, `AcaoAtualizacao`, os
participantes e `TipoDoCanal` sao preservados no artefato de auditoria.

## Tempo

`HoraFechamento` tem o formato `HHMMSSNNN`, em que `NNN` representa a fracao de segundo.
O offset da fonte e fixo em `-03:00`, conforme o glossario da B3. O adaptador nao consulta o
timezone do sistema nem aplica regras historicas de horario de verao.

Exemplo:

```text
DataNegocio=2026-09-10, HoraFechamento=090000132
=> 2026-09-10T09:00:00.132-03:00
=> 2026-09-10T12:00:00.132000000Z
```

`DataReferencia` nunca substitui silenciosamente `DataNegocio`.

## Eventos e cancelamentos

Os dominios aceitos de `AcaoAtualizacao` sao:

- `0`: negocio novo;
- `2`: delete/cancelamento.

A identidade usada para relacionar o cancelamento ao negocio e:

```text
(DataNegocio, CodigoInstrumento, CodigoIdentificadorNegocio)
```

Uma tombstone `2` exclui da saida canonica qualquer evento `0` com essa identidade,
independentemente do horario em que o cancelamento foi publicado. O negocio novo e o delete
continuam presentes na auditoria. Delete sem negocio correspondente e registrado como
`orphan_cancellation` e nunca produz negocio canonico.

Acao desconhecida e rejeitada. A execucao estrita nao envia um dataset com rejeicoes para o
pipeline quantitativo.

O universo B3 pode conter precos negativos em instrumentos que nao foram selecionados. O
adapter reconhece e contabiliza esses decimais assinados sem `float`, mas nao os projeta no CSV
Canonico v1, cujo contrato exige preco positivo. Um preco nao positivo no instrumento
selecionado e uma rejeicao e bloqueia o pipeline.

## Sessao

Os dominios conhecidos sao `1` (sessao regular) e `6` (after hours). Nenhum deles e filtrado
implicitamente pelo adaptador. O valor original permanece disponivel na auditoria. Um dominio
desconhecido e uma linha rejeitada.

## Ordenacao

`CodigoIdentificadorNegocio` identifica o ciclo new/delete. A ordenacao canonica usa
`(timestamp_ns_utc, source_sequence)`, e `source_sequence` e a ordem fisica da linha no TXT.
`TipoDoCanal` nao participa da ordenacao.

## Imutabilidade e identidade

O ZIP nunca e alterado ou extraido. O adaptador registra SHA-256 e tamanho do ZIP e do TXT
interno, verifica novamente o ZIP depois da leitura e grava a versao do adaptador. Caminhos
absolutos, tempos de execucao e outros valores volateis nao integram os artefatos canonicos.

## Artefatos

- `canonical-trades.csv`: somente negocios novos, ativos e do contrato selecionado.
- `b3-selected-events.parquet`: valores originais e decisao aplicada a cada evento selecionado.
- `b3-rejections.jsonl`: linhas rejeitadas em JSON canonico; vazio quando nao ha rejeicoes.
- `b3-import-report.json`: proveniencia, contagens, intervalos, hashes e politica aplicada.

JSON, JSONL e CSV possuem representacao de bytes canonica. Parquet registra hashes de bytes e
semantico. A equivalencia entre importacoes usa ambos conforme o tipo do artefato.
