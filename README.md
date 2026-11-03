# Fault Propagation and Recovery in Multi-Agent Workflows

UC San Diego CSE 291A research project · Phase 1

## Project overview

**Research question:** How do incorrect, stale, missing, or malicious intermediate results propagate through a multi-agent workflow, and can tracking dependencies support efficient recovery while preserving correct and safe task execution?

This repository implements and measures the first phase of that study: a Researcher → Planner → Executor workflow over AgentDojo Banking tasks, controlled faults at an artifact handoff, and full-restart and always-verify comparisons. It records versioned artifacts and dependencies, but **dependency-aware selective recovery has not yet been implemented**. The `TAMAS/` directory contains a separate benchmark project for context; the Phase 1 runner does not use it.

## Research objectives

- Inject controlled faults into intermediate evidence and compare each faulty run with a matched clean run.
- Measure downstream exposure, changed behavior, task utility, affected stages, repeated or wasted work, wall-clock latency, token use, and estimated inference cost.
- Preserve artifact versions, hashes, causal dependencies, tool effects, and trace events for later recovery analysis.
- Compare a complete clean restart with verification before both agent handoffs.
- Audit state-changing actions against the original user's authorization, separately from task success and runtime tool permissions.

## System architecture

```mermaid
flowchart LR
    T[AgentDojo Banking task and initial environment] --> R[Researcher]
    R --> E[task_evidence artifact]
    E --> V1{Verifier when enabled}
    V1 --> P[Planner]
    P --> L[execution_plan artifact]
    L --> V2{Verifier when enabled}
    V2 --> X[Executor]
    X --> G[ToolGateway]
    G --> A[AgentDojo environment]
    X --> C[execution_receipt artifact]
    A --> S[AgentDojo utility scoring]
    E -. versioned reads and parent IDs .-> D[ArtifactStore and JSONL trace]
    L -. versioned reads and parent IDs .-> D
    C -. tool effects .-> D
    V1 -. decisions and LLM use .-> D
    V2 -. decisions and LLM use .-> D
    S -. outcome .-> D
```

The Researcher reads through the `ToolGateway`, writes `task_evidence`, and the Planner turns that evidence into an `execution_plan`. The Executor consumes the plan, may invoke one permitted state-changing Banking tool, and writes an `execution_receipt`. The gateway enforces role-specific tool permissions and records call arguments, outcomes, and environment snapshots. `ArtifactStore` assigns versions and hashes and records producer, consumer, and parent relationships; the JSONL trace also records LLM calls, verification decisions, and scoring. See `workflow.py`, `agents.py`, `artifact_store.py`, `tool_gateway.py`, and `trace.py` under `phase1_mas/src/phase1_mas/`.

With Always Verify, `AlwaysVerifyArtifactVerifier` checks the evidence before Planner and the plan before Executor. For a plan, it reads the exact referenced evidence version and checks its recorded dependency; the evidence is supplied as untrusted task data, not as an instruction. A rejected handoff produces a no-op receipt and a scored, safely stopped run. A safe stop is **not** automatically correct task completion.

## Benchmark and tasks

The adapter loads AgentDojo **Banking suite v1.2.2** from the bundled `AgentDojo/agentdojo/` source and uses the selected user task's original prompt, initial environment, and utility predicate. The eight-task manifest in `agents.py` selects tasks with an evidence → plan → single write-action path:

| Task | User-requested operation and evidence source |
| --- | --- |
| `user_task_0` | Pay a bill using a bill file. |
| `user_task_2` | Update scheduled rent after reading a landlord notice. |
| `user_task_3` | Send a refund based on transaction history. |
| `user_task_6` | Create a recurring subscription payment using transaction history. |
| `user_task_11` | Send a VAT-difference payment using transaction history. |
| `user_task_12` | Update scheduled rent after a landlord notice. |
| `user_task_13` | Update account address using an address-change file. |
| `user_task_14` | Update the account password after consulting transaction history. |

Task definitions, initial Banking data, and utility predicates are bundled under `AgentDojo/agentdojo/src/agentdojo/default_suites/`; the v1.2.2 suite reuses and updates earlier versioned definitions. `agentdojo_adapter.py` loads the suite and `scoring.py` evaluates final user-task utility. AgentDojo injection-task security predicates are available for its separate environment-injection variants; they are not the authorization oracle for the main handoff-fault grid. No external Banking dataset download is specified by this project.

## Fault injection

The main grid injects at the **Researcher → Planner `task_evidence` handoff**, creating a new artifact version. `faults.py` implements:

| Family | Signaled behavior | Silent behavior in the 48-point grid |
| --- | --- | --- |
| Incorrect intermediate result | Replaces an observation with an explicit incorrect-result sentinel. | Replaces a task-specific observation with a plausible, schema-compatible wrong value. |
| Timeout / missing result | Clears observations and marks the Researcher response missing or timed out. | Not part of this grid. |
| Stale output | Substitutes or simulates old/incomplete evidence and marks it stale. | Not part of this grid. |
| Malicious instruction | Adds an explicit untrusted instruction field. | Embeds instruction text in ordinary evidence response content. |

Signaled faults include fault metadata and an invalidity signal visible to downstream logic. Silent incorrect and silent malicious variants keep experiment provenance in the trace while presenting an `UNKNOWN` validity artifact to agents; they do not reveal the experimental fault label in the downstream evidence. The separate AgentDojo environment-injection variants are supported by the adapter but are **not** included in the main 248-attempt runner.

## Experimental strategies and metric meanings

`runner.sh` records 248 workflow attempts: eight clean attempts; 48 matched clean/fault pairs (96 attempts); 48 full-restart points (faulty first attempt plus clean retry, 96 attempts); and 48 faulty Always Verify points. The 48 fault points are 32 signaled cases (four families × eight tasks) and 16 silent cases (incorrect and malicious × eight tasks). The later clean + Always Verify v4 run adds eight separate attempts; it is outside the original 248.

| Strategy | Procedure |
| --- | --- |
| Clean baseline | Run all eight tasks without fault injection or verification. |
| Unprotected fault propagation | Run a matched clean/fault pair and compare artifact exposure, downstream behavior, utility, and work. |
| Full restart | Run the faulty attempt, then rerun all agents from a fresh Banking environment after the fault is known. The first attempt's side effects remain relevant when interpreting safety. |
| Always Verify | Verify both handoffs using an LLM verdict plus structural checks; reject by stopping before the next role acts. The historical original verifier and the later revised **v4** verifier are distinct result sets. |

**AgentDojo task utility** is the benchmark's final-state predicate. **Correct task completion** in the clean + Always Verify report additionally requires normal completion, an approved receipt, and an Executor state change. **Verifier rejection** records a blocked handoff; it does not prove a fault was harmful. **Unauthorized action** is an offline, task-semantic verdict about an attempted or executed state-changing tool call. These measures must not be substituted for one another.

## Setup and reproducibility

Run commands from the repository root. Python 3.10+ is declared by both packages. The bundled AgentDojo package has its own dependencies; `phase1-mas` declares the OpenAI-compatible client. `pytest` is needed for offline tests. The following commands are documented from the package metadata and CLI entry points; installation and live inference have **not** been executed as part of this README review.

```bash
git clone https://github.com/duuan/Project_cse291A_26fall.git
cd Project_cse291A_26fall
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e AgentDojo/agentdojo
python -m pip install -e phase1_mas
python -m pip install pytest
python -m pytest phase1_mas/tests
```

Tests and the analysis command below require no LLM provider. For live experiments, configure an **authorized** OpenAI-compatible endpoint. The original seed-42 summaries record model `api-glm-5.3-flash`, base URL `https://tritonai-api.ucsd.edu/v1`, temperature `0`, request seed `42`, timeout `60` seconds, and estimated prices of USD 0.15 per million input tokens and USD 0.50 per million output tokens. Access to that UCSD endpoint may be restricted. Use your own credential; never commit it.

```bash
export PHASE1_MAS_PROVIDER=openai_compatible
export PHASE1_MAS_MODEL=api-glm-5.3-flash
export PHASE1_MAS_BASE_URL=https://tritonai-api.ucsd.edu/v1
export PHASE1_MAS_API_KEY='<your-authorized-key>'
export PHASE1_MAS_TEMPERATURE=0
export PHASE1_MAS_TIMEOUT_SECONDS=60
export PHASE1_MAS_SEED=42
export PHASE1_MAS_INPUT_USD_PER_MILLION_TOKENS=0.15
export PHASE1_MAS_OUTPUT_USD_PER_MILLION_TOKENS=0.50
```

For a separate clean run, choose a **new** output directory; `pilot.py` itself does not refuse an existing directory:

```bash
python -m phase1_mas.pilot --mode llm --output-dir phase1_mas/results/my_new_clean_run
```

The complete original design is launched by one script. It writes clean, unprotected fault, full-restart, and original Always Verify outputs into distinct subdirectories and refuses an existing `RESULTS_ROOT`. Its silent cases use eight task-specific payloads defined in the script; a single `--fault-type all` CLI invocation covers only the 32 signaled points.

```bash
SEED=42 RESULTS_ROOT=phase1_mas/results/my_new_full_suite bash phase1_mas/runner.sh
```

The following verified CLI forms run **only the signaled subset** of the fault, restart, or Always Verify conditions, respectively. Each needs a fresh output path. `runner_always_verify_v4_only.sh` runs the 48 faulty Always Verify points using the verifier in the checked-out code, with its own fresh `RESULTS_ROOT`; its filename does not itself pin the prompt version.

```bash
python -m phase1_mas.fault_pilot --task-id all --fault-type all --fault-visibility signaled --seed 42 --output-dir phase1_mas/results/my_new_signaled_faults
python -m phase1_mas.recovery --task-id all --fault-type all --fault-visibility signaled --strategy full_restart --seed 42 --output-dir phase1_mas/results/my_new_signaled_restart
python -m phase1_mas.recovery --task-id all --fault-type all --fault-visibility signaled --strategy always_verify --seed 42 --output-dir phase1_mas/results/my_new_signaled_verify
SEED=42 RESULTS_ROOT=phase1_mas/results/my_new_v4_fault_grid bash phase1_mas/runner_always_verify_v4_only.sh
```

The eight clean + Always Verify attempts have their own entry point. It refuses an existing output directory and checks that the current public LLM configuration exactly matches the original seed-42 reference configuration. Its default reference directory must contain the original clean traces and full-restart summary.

```bash
python -m phase1_mas.clean_always_verify --reference-dir phase1_mas/phase1_results_248_seed42 --output-dir phase1_mas/results/my_new_clean_verify
```

To recompute offline authorization results from **existing** complete traces, use a new output directory. This command expects the original 248-attempt data plus all 48 v4 faulty and eight v4 clean traces; see the availability note below.

```bash
python -m phase1_mas.unauthorized_actions --reference-root phase1_mas/phase1_results_248_seed42 --v4-root phase1_mas/results/phase1_always_verify_v4_seed42 --clean-v4-root phase1_mas/results/05_clean_always_verify_seed42_prompt_v4 --output-dir phase1_mas/results/my_new_authorization_audit
```

The original run stores clean summaries and JSONL traces in `phase1_mas/phase1_results_248_seed42/01_clean_baseline/`, paired propagation reports and metrics in `02_fault_propagation_grid/`, and recovery summaries/traces in `03_full_restart/` and `04_always_verify/`. Newer v4 summaries are under `phase1_mas/results/`. Output directories are experiment-specific; do not reuse one when comparing prompt revisions.

The released v4 result JSON uses repository-relative `trace_path` values. Resolve these paths from the repository root; the offline authorization command above should also be run there.

## Observed results (seed 42)

All values below were read from **local result JSON**. The original 248-attempt results and selected newer summaries are Git-tracked locally; availability of every newer trace on the remote GitHub URL was not verified.

| Condition | Attempts or pairs | AgentDojo utility success | Other outcome |
| --- | ---: | ---: | --- |
| Original clean baseline | 8 attempts | 8/8 | Correct completion 8/8, as re-derived by the clean + v4 summary. |
| Matched clean runs in fault grid | 48 pairs | 44/48 | These are separate paired runs, not the eight-run baseline. |
| Unprotected faulty runs | 48 attempts | 19/48 | Behavioral propagation 48/48; harmful propagation 29/48 by paired-report definition. |
| Full restart, final clean retry | 48 points | 44/48 | Counts final utility after restarting; faulty first actions are separate. |
| Original Always Verify, faulty | 48 attempts | 6/48 | 47/48 verifier rejections under the historical prompt/implementation. |
| Revised v4 Always Verify, faulty | 48 attempts | 7/48 | 40/48 verifier rejections. |
| Revised v4 clean + Always Verify | 8 attempts | 6/8 | Correct completion 6/8; one verifier stop. |

The 48 v4 faulty rejections divide into **32/32 signaled**, **8/8 silent malicious**, and **0/8 silent incorrect**. The clean v4 run accepted 8/8 evidence handoffs and 7/8 plan handoffs; its one plan rejection is a clean-run false-rejection *proxy*, not proof that every rejected plan was semantically valid. The clean run's other noncompletion was not counted as a verifier stop. Plausible silent incorrect evidence remains a clear detection gap.

| Clean condition | LLM calls | Input tokens | Output tokens | Estimated USD | Mean end-to-end latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original clean | 24 | 28,874 | 6,012 | 0.007337 | 6.03 s |
| Clean + Always Verify v4 | 39 | 44,207 | 13,200 | 0.013231 | 8.53 s |

The v4 clean run reports **16 verifier-only LLM calls**, 16,333 input tokens, 6,449 output tokens, estimated USD 0.005674, and 31.28 s of summed verifier LLM latency across eight attempts. Total cost is an estimate using the recorded token prices, not a provider invoice; observed runtime also depends on the endpoint and system load. Work, latency, affected-stage, and propagation definitions are implemented in `metrics.py` and recorded in the JSON summaries and per-run traces.

## Offline unauthorized-action measurement

`unauthorized_actions.py` constructs eight task-specific action constraints from the **original user requests and unmodified Banking environment**, including relevant tool, recipient or target, amount, transaction, recurrence, and updated fields. It reads JSONL `ToolGateway` calls and effects after an experiment; it does not change agents or act as a runtime defense. The checker distinguishes allowed, unauthorized, and indeterminate actions, no state-changing action, and gateway-denied attempts. It reports attempted separately from successfully executed unauthorized actions. An AgentDojo utility failure alone is not an unauthorized-action finding; ambiguous task conditions remain indeterminate.

The locally recorded v2 offline audit includes 208 workflow attempts: original clean (8), unprotected faulty (48), v4 faulty Always Verify (48), clean v4 Always Verify (8), and full restart's faulty first attempts and clean retries (48 each).

| Trace group | Attempts with unauthorized state-changing action | Executed unauthorized | Indeterminate action rows |
| --- | ---: | ---: | ---: |
| Original clean | 0/8 | 0 | 3 |
| Unprotected faulty | 7/48 | 7 | 3 |
| v4 faulty Always Verify | 7/48 | 7 | 1 |
| Clean v4 Always Verify | 0/8 | 0 | 1 |
| Full restart, initial faulty attempt | 7/48 | 7 | 5 |
| Full restart, clean retry | 0/48 | 0 | 6 |

All seven findings in each faulty group were in the **eight silent incorrect-result cases** (7/8); no gateway-denied attempts were recorded in these groups. The audit writes `authorization_rules.json`, `summary.json`, `attempts.jsonl`, and `actions.jsonl` to its chosen directory. Its deterministic rules cannot resolve every semantic ambiguity, which is why indeterminate rows remain visible.

## Repository structure and data availability

```text
AgentDojo/agentdojo/                       Bundled AgentDojo source and Banking suite
TAMAS/TAMAS/                               Separate benchmark project; not used by Phase 1 runner
phase1_mas/
  pyproject.toml                            Phase 1 package metadata
  runner.sh                                 Original complete 248-attempt orchestration
  runner_always_verify_v4_only.sh           Faulty Always Verify subset runner
  src/phase1_mas/
    agents.py, workflow.py                  Three roles and execution loop
    agentdojo_adapter.py, tool_gateway.py   Benchmark bridge and tool mediation
    artifact_store.py, trace.py             Versioned artifacts and events
    faults.py, fault_pilot.py               Fault injection and paired runs
    recovery.py, clean_always_verify.py     Restart/verifier and clean verifier baseline
    metrics.py, experiments.py              Offline metric aggregation
    unauthorized_actions.py                Offline task authorization audit
  tests/                                    Offline regression tests
  phase1_results_248_seed42/               Original clean/fault/restart/verify data
  results/
    05_clean_always_verify_seed42_prompt_v4/
    phase1_always_verify_v4_seed42/
    unauthorized_actions_offline_seed42_v2/
```

**Submission data audit:** Local Git tracks the original `phase1_results_248_seed42/` data, the v4 clean summary, the v4 faulty **signaled** summary, and the v2 authorization rules and summary. It does **not** track the eight clean-v4 JSONL traces, the v4 faulty per-point directories and silent summaries/traces, or the v2 `attempts.jsonl` and `actions.jsonl` (present locally). These omissions prevent a fresh clone of the currently tracked tree from reproducing the complete 208-attempt offline authorization audit. Review trace content for credentials or sensitive fields before publishing any additional data; a sanitized, complete release is preferable. Remote branch contents could not be independently confirmed during this documentation review. Results above are claims about the inspected **local** files, not proof of remote availability.

## Limitations and future work

- Eight Banking tasks and mainly one seed limit generalization. Model behavior, prompt wording, and endpoint performance affect results.
- Signaled artifact metadata makes many faults easier to reject than realistic silent corruption. The v4 verifier rejected no silent incorrect cases in this grid.
- Task utility is a final-state oracle and does not by itself certify that the action sequence was authorized. Deterministic authorization rules leave some actions indeterminate.
- Restarting from a fresh simulated environment estimates recovery cost but cannot undo an external side effect already executed in the initial attempt.
- Artifact dependencies are recorded and checked, but the project has not implemented selective reruns of only affected stages. A next phase can use exact-version dependency edges to decide what to recompute, then compare its correctness, safety, latency, and cost with these baselines.

## References

- Debenedetti et al., [*AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents*](https://arxiv.org/abs/2406.13352); [project source](https://github.com/ethz-spylab/agentdojo). The bundled AgentDojo source provides the benchmark used here.
- Kavathekar et al., [*TAMAS: Benchmarking Adversarial Risks in Multi-Agent LLM Systems*](https://arxiv.org/abs/2511.05269). Its source is included under `TAMAS/` for reference, not called by the Phase 1 workflow.
