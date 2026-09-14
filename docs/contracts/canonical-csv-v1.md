# CSV Canonico v1

## Colunas

O cabecalho e obrigatorio e deve conter exatamente, nesta ordem:

```csv
symbol,timestamp,source_sequence,price,quantity
```

| Campo | Contrato |
| --- | --- |
| `symbol` | Identificador nao vazio. Um arquivo contem exatamente um simbolo. |
| `timestamp` | ISO-8601 com offset explicito e ate 9 casas fracionarias. |
| `source_sequence` | Inteiro nao negativo que desempata negocios no mesmo timestamp. |
| `price` | Decimal positivo, sem separador de milhar, sem notacao cientifica e com no maximo 9 casas significativas. |
| `quantity` | Inteiro positivo. |

Exemplo apenas de formato, sem constituir dado ou regra quantitativa:

```csv
symbol,timestamp,source_sequence,price,quantity
TEST,2026-01-02T09:00:00.000000001-03:00,0,100.00,1
TEST,2026-01-02T09:00:00.000000001-03:00,1,100.05,2
```

## Regras

1. `(timestamp normalizado para UTC, source_sequence)` deve ser unico.
2. A ordem fisica do CSV nao e relevante; a ordem canonica usa essa chave.
3. Offsets distintos que representam o mesmo instante sao tratados como o mesmo timestamp.
4. Linhas, campos ou valores invalidos interrompem a importacao; nao ha correcao silenciosa.
5. O normalizador nao modifica o arquivo de origem.
6. A escala de preco e derivada depois da remocao de zeros fracionarios nao significativos.
7. Nao sao criados negocios, candles ou volumes ausentes.
8. Inteiros devem caber no intervalo positivo de 64 bits usado pelo formato normalizado.

## Evolucao

Um formato real da B3 sera implementado como adaptador que produz este contrato canonico. Alterar
semantica, tipos ou ordenacao exige uma nova versao do contrato.
