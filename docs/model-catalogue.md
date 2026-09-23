# Model catalogue

## Claude Code and Codex

Cloud chat uses subscription-authenticated CLIs, not provider APIs. **CLI default**
lets the installed CLI choose its model. Claude also offers `opus`, `sonnet`,
`fable` and `haiku` aliases, resolved by Claude Code. Codex models are read from
its local `models_cache.json`; hidden entries are omitted. Refresh after updating
or running Codex. Account/plan restrictions can still make a listed model unavailable.

On the development account, the cache currently lists GPT-6 Astra, GPT-5.6
Sol/Terra/Luna and GPT-5.5. This is an example, not a promise of access or a
fixed list. API-only model names are not copied into the Codex picker.

## Local catalogue update: 23 September 2026

- **Qwen3.5-0.8B:** approximately 0.53 GB, a small CPU chat starter.
- **Qwen3.8-27B:** Q5 GGUF, approximately 19.8 GB.
- **Qwen3.8-27B Ultra Uncensored Heretic:** Q5 GGUF, approximately 20.1 GB.
- **Gemma-4-12B Uncensored Heretic:** Q5 GGUF, approximately 9.4 GB.

The local downloads were checked against the publishers' file listings. These
sizes exclude context memory and runtime overhead. Use a recent llama.cpp build.
Hugmunn currently uses these models for text and tools, without image inputs or
MTP acceleration. Community “uncensored” labels describe the publisher's tuning;
they are not a guarantee of output quality or unrestricted responses.

Both CLIs have passed live subscription smoke checks. The CPU starter also
passed a real CPU-only inference check. The large local weights have not been
downloaded or benchmarked as part of this update.

## Sources

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [Qwen3.8 GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
- [Qwen3.8 Heretic GGUF](https://huggingface.co/llmfan46/Qwen3.8-27B-Ultra-Uncensored-Heretic-Native-MTP-Preserved-GGUF)
- [Gemma 12B Heretic GGUF](https://huggingface.co/llmfan46/gemma-4-12B-it-uncensored-heretic-GGUF)
