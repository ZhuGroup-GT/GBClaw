# Bundled tokenizer vocabularies

Context budgets count tokens locally, before each request, with the vocabulary of the model
family. These files make that count exact and keep it offline: nothing is downloaded at run
time. Each file is stored gzip-compressed and checked against the SHA-256 of its uncompressed
content before use. A missing or altered file is logged, and that family falls back to the
conservative one-token-per-character estimate.

| File | Used for | Source | SHA-256 (uncompressed) | License |
| --- | --- | --- | --- | --- |
| `o200k_base.tiktoken.gz` | GPT-4o, GPT-4.1/4.5, GPT-5 and later, o1/o3/o4 | [openaipublic](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken), as loaded by tiktoken | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` | MIT (tiktoken) |
| `cl100k_base.tiktoken.gz` | GPT-3.5 and GPT-4 | [openaipublic](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken), as loaded by tiktoken | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` | MIT (tiktoken) |
| `deepseek-v3.tokenizer.json.gz` | DeepSeek API models | [DeepSeek-V3-0324 `tokenizer.json`](https://huggingface.co/deepseek-ai/DeepSeek-V3-0324/resolve/main/tokenizer.json) | `621ac2e32d0dba658404412318818aaa8ce8cda492e59830109d8da6b517fb41` | MIT (DeepSeek-V3-0324) |

The GPT hashes are the ones tiktoken itself expects. The DeepSeek file is read with the
`tokenizers` package. For the DeepSeek API chat models tested, its counts equal the input
tokens the API reports, for English and Chinese text and for the system prompt plus tool
schemas.

The model family is recognized from the model name, also inside gateway deployment aliases
(`openai.py` and `deepseek.py` in the parent directory hold the patterns). Claude has no
public tokenizer and keeps the one-token-per-character estimate.

To add or replace a vocabulary, store the new file as `<name>.gz` here. For a `tokenizers`
file, update its SHA-256 constant in the provider module (as `_DEEPSEEK_TOKENIZER_SHA256` in
`deepseek.py`); a tiktoken encoding must match the hash tiktoken itself expects. Update this
table and compare a local count with the input tokens the provider reports for the same
text.
