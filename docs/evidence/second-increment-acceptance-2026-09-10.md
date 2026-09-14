# Evidencia de aceite do segundo incremento - amostra B3 10/09/2026

## Resultado

O teste opt-in executou duas vezes, com sucesso, o fluxo:

```text
B3 ZIP real -> adapter DRV -> WINV26 -> eventos -> CSV Canonico v1 -> Parquet
-> candles 1m -> SMA(20) -> estrategia manual -> backtest -> ledger/metricas
```

Os inventarios das duas execucoes foram iguais e todos os 12 artefatos comparados tiveram
bytes identicos.

| Identidade | Valor |
| --- | --- |
| Aceite | `sha256:36ef8f1f2d471199ba725d6cee46bcf1046d8606e5081aa9bb97b284dad4b49f` |
| Importacao B3 | `sha256:6d02f5667d6dc277499af77055b7909e54571a5d7bff7c5910cbb16cb3160dc2` |
| Dataset | `sha256:df9a4c0a51d4563966ce230a99740654531819476e10dc008c94facb7662d603` |
| Primeiro incremento | `sha256:d33d36d8d3d37756fa275491f18a5987cf2856519fdb34ca5df71e25f13f6a6f` |
| Segundo incremento | `sha256:17a3ff93e1ffbb1a01bfcd5d557f8007e2fa335f40d265bd935c9d03f49a2e84` |

## Fonte e contagens

| Item | Valor |
| --- | ---: |
| ZIP, bytes | 50.762.669 |
| TXT interno, bytes | 529.161.058 |
| Linhas lidas | 7.277.116 |
| Linhas B3 validas | 7.277.116 |
| Linhas rejeitadas | 0 |
| Instrumentos encontrados | 474 |
| Eventos `new` | 7.277.113 |
| Eventos `delete` | 3 |
| Eventos `WINV26` | 6.262.194 |
| Negocios canonicos `WINV26` | 6.262.194 |
| Cancelamentos `WINV26` | 0 |
| Sessao regular `WINV26` | 6.262.194 |
| Candles 1 minuto | 563 |
| Features SMA(20) | 563 |

Intervalo `WINV26` na fonte: 09:03:00.560 a 18:31:27.232 em `UTC-03:00`.

Intervalo normalizado: `2026-09-10T12:03:00.560000000Z` a
`2026-09-10T21:31:27.232000000Z`.

O intervalo cobre 569 minutos civis inclusivos e produziu 563 candles. Os seis minutos sem
negocios nao foram preenchidos.

## Backtest de validacao

A estrategia manual `WINV26.SMA.VALIDATION` usa SMA(20), compra quando o fechamento esta acima
da SMA, alvo de 100 pontos, stop de 100 pontos, uma posicao e fechamento no fim dos dados.
Custos, slippage e multiplicador financeiro nao fazem parte deste teste.

| Metrica | Valor |
| --- | ---: |
| Operacoes fechadas | 191 |
| Vitorias | 100 |
| Derrotas | 91 |
| Empates | 0 |
| Lucro bruto, unidades de preco | 10.090 |
| Perda bruta, unidades de preco | 9.100 |
| Resultado liquido, unidades de preco | 990 |
| Drawdown maximo, unidades de preco | 1.000 |
| Maximo de perdas consecutivas | 6 |

Essas metricas validam o encadeamento tecnico; nao constituem avaliacao economica da estrategia.

## Fingerprints de artefatos

Os valores abaixo foram iguais nas execucoes A e B.

| Artefato | SHA-256 de bytes |
| --- | --- |
| Relatorio do adapter | `a6f32d98a933805b3dc23fc881ddd1ba3d9b034ede2bbccc91aa77d353c4ea5f` |
| Rejeicoes | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Auditoria Parquet | `846925196b308b91b242dba7e86527ffb81639393b657e2b7a9a16bd029e0782` |
| CSV canonico | `521bfb541521e5e68fcb50805fdbff38fd664a1b4ffda30ffd8c271d64fb5f6f` |
| Parquet normalizado | `98e6d9339798384e243bb2ac972c87e5e0ecb2a717b187616bc962508af15037` |
| Candles | `bf6e0b4d9f9a3390c61231a2e480ae2f2d99855b4ff2f13a0fee2517884a4101` |
| Features | `624e93d5ca648fd4fbd6694f365b904b7464df7fa4d8d53bccbba724bf390e88d` |
| Estrategia canonica | `f2c3f6028cd2d0fc9dc37fff10b29827da5a4abb64bf623c6650b0d39e818099b` |
| Ledger | `9f1ac1681b3797e2daf134edb0438a5fcca267bbdc087c5d4f5b72c2879e4853` |
| Metricas | `1cfa4c61b7e0b1e16cd62c8d013aec37ed8c6164430a332fef69da4d04080eb5` |
| Manifest do primeiro incremento | `b0fada718cc682f368ece13382b37ec1cf026ad284f28b9794ecc45e597e49eea` |
| Manifest do segundo incremento | `1f2d92128289d80e9dd8128b517014573f53ef1e457e63743050d7e7c999c2443` |

Hashes semanticos relevantes:

- auditoria: `7054ab076ec2f3f34fb532d3016ee670bbd33e2841bd34ae82d253b2bbcb8854`;
- negocios normalizados: `08cd7f1b13d982188c541cca907f8b2a033ed04fd75fa0539ba5fcc54c339c8e`;
- candles: `8dbc9ce2dae65f5b448002958e2444ed016dcc594bb31fa40471a6beaf81d747`;
- features: `a43b3ccb2635e546c3a3c0b1d78a0c2d963821b5862f5d73236e44daed654332`.

## Variacoes observadas na fonte

- O nome usa o perfil `_DRV` e o cabecalho inclui `TipoDoCanal`. O campo foi preservado
  literalmente e nao participa de filtro, ordem ou semantica quantitativa.
- Ha 242 eventos com preco negativo, distribuidos por 18 instrumentos `DII/DIT`, todos fora de
  `WINV26`. Eles sao decimais B3 validos e foram contabilizados, mas nao projetados no contrato
  canonico positivo.
- Ha 295 eventos de 15 instrumentos com `DataReferencia=2026-09-10` e
  `DataNegocio=2026-09-11`, todos fora de `WINV26`. Nenhuma data foi substituida: o timestamp
  segue `DataNegocio`, e ambos os valores permanecem distintos na origem.
- Os tres deletes globais possuem um new correspondente pela chave
  `(DataNegocio, CodigoInstrumento, CodigoIdentificadorNegocio)` e horario de cancelamento
  posterior. Nenhum pertence a `WINV26`.
- Todos os 7.277.116 eventos usam `TipoSessaoPregao=1`.

## Proveniencia

- ZIP SHA-256: `fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f`.
- TXT SHA-256: `850fd8c81fb628d12fa111239e371088a4e8d3ef466806a5c7d07d3d5df012d7`.
- CRC32 informado pelo ZIP: `da05c303`.
