# Proveniencia da fixture B3 DRV

`sample_drv.txt` contem um subconjunto de linhas do arquivo
`10-09-2026_NEGOCIOSAVISTA_DRV.zip`, cujo SHA-256 e
`fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f`.

As linhas foram mantidas literalmente e na ordem relativa da origem:

- 2: primeiro evento `WINZ26`, usado para provar o filtro de instrumento;
- 14302 e 14303: `WINV26` com timestamp identico;
- 97004, 146924, 201075, 229372, 274281, 302491 e 339273: primeiro evento
  `WINV26` dos minutos 09:04 a 09:10;
- 895814 e 4789066: par real new/delete de `ETHF27`, identificado por negocio 50.

Os testes que exercitam dominios invalidos ou a sessao after hours derivam uma linha em memoria
e alteram somente o campo sob teste. O arquivo versionado permanece uma amostra literal.
