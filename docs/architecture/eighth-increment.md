# Oitavo incremento: Robustness v1 pós-score

O pacote aditivo `quantlab-robustness` consome evidência imutável do Research/Score, um catálogo e
uma lista explícita de sessões autorizadas. Ele reutiliza `CandidateSessionEvaluation` e agrega
quatro famílias independentes: Walk-Forward diagnóstico, Monte Carlo por blocos completos,
Sensitivity OAT e Execution Stress por re-backtest.

As dependências seguem uma direção única: Score não conhece Robustness; policies de família não
dependem do protocol global; assessments ligam fatos ao protocol; qualified ranking apenas filtra
o ranking original. Não há score composto, otimização, promoção de variante ou seleção oculta.

O path set Monte Carlo é compartilhado entre candidatos. Sensitivity deduplica somente variantes
quantitativamente idênticas, preservando cenários. Stress sempre chama o Backtest Engine. Caches
SQLite validam JSON canônico/fingerprint e o checkpoint registra apenas unidades completas. O
export atômico reconstrói JSON/JSONL/Parquet canônicos, mantendo bytes independentes de ordem
física, cache quente ou retomada.

O alvo continua Windows com cerca de 8 GB: mercado/features ficam nos caches por sessão; folds e
cenários reusam CSEs; Monte Carlo carrega ledgers de sessões já avaliadas, não ticks. O workload
preflight impede explosão antes de trabalho caro sem alterar a ciência nem truncar.

Riscos residuais: histórico real multissessão ainda não foi fornecido; sobreposição de folds reduz
independência estatística; permutation full-length não distribui P&L total; thresholds ruins podem
produzir conclusões ruins, embora auditáveis. Esses limites são persistidos e documentados.
