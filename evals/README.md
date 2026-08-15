# myMinions Evals

This directory contains the evaluation system for the myMinions agent runtime.

## Structure

```text
evals/
├── specs/       # evaluation objectives, metrics, thresholds
├── datasets/    # golden datasets and negative cases
├── fixtures/    # fixed external inputs for future COROS/RAG/kitchen evals
├── judges/      # deterministic and model-based scoring code
├── traces/      # future saved agent trajectories
└── run_evals.py
```

## Current Suite

`natural_language_routing` evaluates whether `MainAgentOrchestrator` safely
converts natural language messages into internal commands.

It uses fixed LLM route outputs, so it does not call:

- DeepSeek
- Discord
- COROS MCP
- Bilibili
- local kitchen or memory data stores

## Metrics

The suite is defined in:

```text
evals/specs/natural_language_routing.json
```

Current metrics:

- `route_accuracy`: positive examples routed to the expected command
- `rejection_accuracy`: negative examples rejected
- `cross_channel_rejection`: unavailable commands rejected by channel
- `low_confidence_rejection`: low-confidence LLM routes rejected
- `invalid_argument_rejection`: malformed kitchen actions rejected

## Run

```bash
uv run python evals/run_evals.py
```

Expected output:

```text
Suite: natural_language_routing
Cases: 13/13 passed
- route_accuracy: 1.00 >= 0.90 PASS
- rejection_accuracy: 1.00 >= 1.00 PASS
- cross_channel_rejection: 1.00 >= 1.00 PASS
- low_confidence_rejection: 1.00 >= 1.00 PASS
- invalid_argument_rejection: 1.00 >= 1.00 PASS
```

## Next Suites

- COROS LangGraph trajectory eval
- COROS report quality eval
- RAG retrieval and answer faithfulness eval
- kitchen recipe extraction eval
- real LLM routing golden-set eval
