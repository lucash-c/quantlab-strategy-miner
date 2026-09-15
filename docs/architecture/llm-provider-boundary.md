# Future LLMProvider boundary

This is an architectural decision only. No provider, API client, credential, prompt runtime, or
LLM dependency is implemented by the third increment.

A future optional application layer may define an `LLMProvider` port selected by the user. Its
inputs must be immutable quantitative artifacts and its outputs must be treated as proposals,
never as authoritative market calculations. Provider packages will live outside `quantlab-core`,
`quantlab-data`, and `quantlab-backtest`; those packages must remain import-independent from any
LLM SDK.

Credentials must later come from a secure operating-system or deployment secret store. They
must never enter strategies, datasets, cache keys, manifests, logs, fixtures, or the repository.

