# eval

Dataset generation, loading, indexing and (from Phase 3) the evaluation harness. Python 3.12, managed with `uv`.

```bash
cd eval && uv sync
uv run python -m dataset.generate                 # regenerate everything under data/ (byte-identical)
uv run python -m dataset.freeze --check           # verify the frozen test set
uv run python -m dataset.index_policies           # chunk policy PDFs into Qdrant (localhost:6335)
uv run python -m dataset.retrieval_smoke          # retrieval recall@5 baseline -> results/
uv run python -m dataset.load --email admin@claimpilot.local --password '...'   # ingest via the API
uv run pytest
```

See `docs/dataset.md` for the label schema and decision rules, and `docs/data-sources.md` for the seed dataset and its license.

## Frozen test set

The 160 test claims (labels and documents), the four policy forms and `splits.json` are hashed together:

```
test set sha256: efc1690fd54a7f079137a011b255c9d5294176a0c938e0710a535fa94da1a888
```

`dataset.freeze --check` and `tests/test_dataset.py::test_test_set_is_frozen` fail if any of those files change. Tune only on the 40 dev claims. If the dataset is regenerated on purpose, record the new hash here in the same commit and say why in the commit message.
