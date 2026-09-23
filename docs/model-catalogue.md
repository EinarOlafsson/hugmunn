# Model catalogue

The account model list refreshes when you sign in or choose **Accounts → Refresh
model lists**. Access and billing depend on your API account; a ChatGPT or Claude
chat subscription does not supply an API key.

## Catalogue update: 23 September 2026

- **GPT-6 Astra, Sol, Luna:** Responses API streaming, function calls and reasoning
  effort. Hugmunn requests `store: false` and retains encrypted reasoning only in
  memory during the current tool loop. Quick uses low effort on Astra and no
  reasoning on Sol/Luna. Existing GPT-5.1 and GPT-4.1 remain available.
- **Claude Opus 5.5, Fable 5.1, Sonnet 5, Haiku 4.5:** current IDs and token limits.
  New Claude generations use adaptive thinking and effort; Haiku 4.5 retains
  token-budget thinking. Quick reduces effort on always-thinking models.
- **Qwen3.8-27B:** Q5 GGUF, approximately 19.8 GB.
- **Qwen3.8-27B Ultra Uncensored Heretic:** Q5 GGUF, approximately 20.1 GB.
- **Gemma-4-12B Uncensored Heretic:** Q5 GGUF, approximately 9.4 GB.

The local downloads were checked against the publishers' file listings. These
sizes exclude context memory and runtime overhead. Use a recent llama.cpp build.
Hugmunn currently uses these models for text and tools, without image inputs or
MTP acceleration. Community “uncensored” labels describe the publisher's tuning;
they are not a guarantee of output quality or unrestricted responses.

API adapters are tested against local streaming fixtures. The new large weights
have not been downloaded or benchmarked as part of this catalogue update.

## Sources

- [OpenAI GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model)
- [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra),
  [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol),
  [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Claude models](https://platform.claude.com/docs/en/models/overview)
- [Claude thinking controls](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)
- [Qwen3.8 GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
- [Qwen3.8 Heretic GGUF](https://huggingface.co/llmfan46/Qwen3.8-27B-Ultra-Uncensored-Heretic-Native-MTP-Preserved-GGUF)
- [Gemma 12B Heretic GGUF](https://huggingface.co/llmfan46/gemma-4-12B-it-uncensored-heretic-GGUF)
