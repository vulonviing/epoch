# `epoch_switch` package overview

`src/epoch_switch/` contains the complete CLI implementation for the tabular
and document-family EPOCH pipelines. Topology selection and post-selection
execution are active runtime code, not evaluation scaffolding.

The supported executable entry point is:

```bash
epoch-regulation
# equivalent: python -m epoch_switch.regulation_cli
```

The package is organized into:

- `core/`: shared envelopes, evidence storage, LLM adapters/provenance, and the
  runtime data catalog.
- `agents/`: agent-local implementations, prompts, schemas, and shelf stores.
- `corpus/`: deterministic RD1/RD2 document parsing and candidate mapping.
- `selection/`: the topology library and static stage-2 bindings.
- `usecases/`: public registry seeds and pipeline-family declarations.
- `regulation_cli.py`: the only layer that orchestrates multiple agent stores.

See `epoch_switch/agents/AGENT_INDEX.md` for the exhaustive agent-to-file map.
The live tabular bindings are Direct `[D3, P1, F1]`, Debate
`[D3, [P2, P3], S1, F1]`, and Coalition `[D3, C1, C2, F1]`. The document
binding is `[[P4, P5, P6], F2]` for every selected topology.

All tabular data paths resolve through `epoch_switch.config.DATA_DIR`, which
defaults to the committed synthetic fixture. Runtime experiments and agent
shelves are local state and are gitignored in this public repository.
