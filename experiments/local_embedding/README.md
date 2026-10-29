# Local embedding experiment

Self-authored development data only. This service is separate from the reviewed
AML version and the candidate Add/Search database. It is not a replacement for
the competition-specified `text-embedding-v4`, and no model IDs are aliased.

Model: [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B),
Apache-2.0, pinned to `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
Weights and dependencies are kept under the project directory and excluded from Git.
The model uses 1024-dimensional text vectors; images need prior text extraction
or a separate multimodal model. This experiment performs no vision inference.

On the authorized server, from the project root:

```sh
bash experiments/local_embedding/install.sh
bash experiments/local_embedding/start.sh
# In another terminal:
.venv-embedding/bin/python experiments/local_embedding/check.py
# Requires only the existing candidate environment (Pillow), not PyTorch:
.venv-candidate/bin/python experiments/local_embedding/memory_probe.py
```

Loopback endpoints: `GET http://127.0.0.1:18088/health` and
`POST http://127.0.0.1:18088/v1/embeddings`. The latter accepts an OpenAI-style
subset: exact `model`, `input` string or list, and optional `input_type` set to
`query` or `document`. Query inputs receive the retrieval instruction recommended
by the model card; documents do not. Results include ordered `data[].embedding`.

The initial service deliberately uses CPU with four inference threads. Maximum
batch size is eight, with 2000 characters and 512 tokens per item; longer inputs
are rejected rather than silently truncated. These limits support small probes,
not Full evaluation capacity. Requests are not logged or cached. Loading uses
only local Safetensors and no remote model code; startup cannot download files.

`check.py` tests two known, self-authored English/Chinese retrieval examples,
unit-norm vectors and three invalid requests. It writes
`docs/local-embedding-check.json`. Passing it demonstrates basic availability,
not broad retrieval quality or an official competition result.

`memory_probe.py` supplies local text vectors to a disposable candidate store,
checks two source-evidence retrievals and user isolation, and removes its database.
Its provider and query-instruction wrapper exist only inside this experiment;
the normal specified-model provider and deployed candidate configuration do not change.

The runtime and weights require server disk/memory and initial download bandwidth;
local inference makes no paid model API requests. ChatGPT/Codex is not invoked.
