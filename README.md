# Fault Propagation and Recovery in Multi-Agent Workflows

CSE 291A Phase 1 research project

## Overview

This project studies how faulty intermediate results propagate through a Researcher → Planner → Executor workflow, and whether recording artifact dependencies can support efficient, safe recovery. Phase 1 implements fault injection, trace and dependency recording, and full-restart and always-verify baselines. 

The workflow uses eight tasks from the bundled [AgentDojo Banking v1.2.2 suite](AgentDojo/agentdojo/src/agentdojo/default_suites/v1_2_2/banking/): `user_task_0`, `user_task_2`, `user_task_3`, `user_task_6`, `user_task_11`, `user_task_12`, `user_task_13`, and `user_task_14`. Task definitions and the simulated Banking environment are included in `AgentDojo/agentdojo/`.

## Implementation

`phase1_mas/src/phase1_mas/` contains the workflow. The Researcher writes versioned `task_evidence`; the Planner reads it and writes an `execution_plan`; the Executor reads the plan, uses the permission-enforcing ToolGateway, and writes an `execution_receipt`. The artifact store records versions, hashes, reads, and parent dependencies. JSONL traces capture agent and tool events, verifier decisions, token use, estimated cost, and latency. AgentDojo's task utility predicate grades each final environment.

Faults are injected mainly at the Researcher → Planner evidence handoff. The grid covers incorrect results, timeout or missing results, stale output, and malicious instructions. All four have signaled variants; incorrect results and malicious instructions also have silent variants. The experiments compare clean runs, unprotected faulty runs, full restart, and Always Verify at both handoffs. The offline authorization checker evaluates state-changing tool actions against the original user request and unmodified task environment; it is an evaluation tool, not a runtime defense.

Key entry points are [`runner.sh`](phase1_mas/runner.sh), [`runner_always_verify_only.sh`](phase1_mas/runner_always_verify_only.sh), [`clean_always_verify.py`](phase1_mas/src/phase1_mas/clean_always_verify.py), and [`unauthorized_actions.py`](phase1_mas/src/phase1_mas/unauthorized_actions.py). Tests are in [`phase1_mas/tests/`](phase1_mas/tests/).

## Code and data

| Location | Contents |
| --- | --- |
| [`AgentDojo/agentdojo/`](AgentDojo/agentdojo/) | Bundled benchmark code, Banking tasks, and simulated task data. |
| [`phase1_mas/src/phase1_mas/`](phase1_mas/src/phase1_mas/) | Agents, workflow, faults, verification, metrics, and offline authorization checker. |
| [`phase1_mas/phase1_results_248_seed42/`](phase1_mas/phase1_results_248_seed42/) | Original seed-42 clean, paired fault, full-restart, and historical Always Verify results. |
| [`phase1_mas/results/05_clean_always_verify_seed42/`](phase1_mas/results/05_clean_always_verify_seed42/) | Eight clean + Always Verify traces and summary. |
| [`phase1_mas/results/phase1_always_verify_seed42/`](phase1_mas/results/phase1_always_verify_seed42/) | Forty-eight faulty + Always Verify traces and result JSON. |
| [`phase1_mas/results/unauthorized_actions_offline_seed42/`](phase1_mas/results/unauthorized_actions_offline_seed42/) | Authorization rules, summary, and per-attempt/action verdicts. |

Result JSON uses repository-relative trace paths. Run analysis commands from the repository root.

## Results at a glance

| Condition | Outcome |
| --- | ---: |
| Original clean baseline | 8/8 AgentDojo task utility success |
| Unprotected faulty attempts | 19/48 AgentDojo task utility success |
| Full restart, final clean retry | 44/48 AgentDojo task utility success |
| Clean + Always Verify | 6/8 correct task completions |
| Faulty + Always Verify | 40/48 verifier rejections |
| Unauthorized state-changing actions | 7/48 unprotected faulty; 7/48 faulty Always Verify |

The seven unauthorized findings in each faulty group occurred in the silent incorrect-result cases. Task utility, correct completion, verifier rejection, and unauthorized action are distinct measures. In particular, a verifier's safe stop is not automatically successful task completion. See the linked summaries and the project report for denominators, cost and latency measurements, and interpretation.

## Reproduce

Use Python 3.10+ and run commands from the repository root. Installing packages and running tests require no LLM API. Live experiments require an authorized OpenAI-compatible endpoint; the original results used `api-glm-5.3-flash` at the UCSD endpoint with seed 42. Choose new output directories to preserve the included results.

```bash
git clone https://github.com/duuan/Project_cse291A_26fall.git
cd Project_cse291A_26fall
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e AgentDojo/agentdojo -e phase1_mas pytest
python -m pytest phase1_mas/tests
```

For live runs, set your own credential. The clean + Always Verify entry point checks the seed-42 model configuration against the recorded reference:

```bash
export PHASE1_MAS_MODEL=api-glm-5.3-flash
export PHASE1_MAS_BASE_URL=https://tritonai-api.ucsd.edu/v1
export PHASE1_MAS_API_KEY='<your-authorized-key>'
export PHASE1_MAS_TEMPERATURE=0
export PHASE1_MAS_TIMEOUT_SECONDS=60
export PHASE1_MAS_SEED=42
export PHASE1_MAS_INPUT_USD_PER_MILLION_TOKENS=0.15
export PHASE1_MAS_OUTPUT_USD_PER_MILLION_TOKENS=0.50
```

```bash
# Full clean/fault/restart/verify grid with the verifier in the checked-out code
SEED=42 RESULTS_ROOT=phase1_mas/results/my_new_full_run bash phase1_mas/runner.sh

# Only the 48 faulty Always Verify points
SEED=42 RESULTS_ROOT=phase1_mas/results/my_new_verify_fault_run bash phase1_mas/runner_always_verify_only.sh

# Only the eight clean + Always Verify tasks
python -m phase1_mas.clean_always_verify --reference-dir phase1_mas/phase1_results_248_seed42 --output-dir phase1_mas/results/my_new_clean_verify
```

The offline authorization audit reads existing traces and makes no LLM calls:

```bash
python -m phase1_mas.unauthorized_actions --reference-root phase1_mas/phase1_results_248_seed42 --verify-root phase1_mas/results/phase1_always_verify_seed42 --clean-verify-root phase1_mas/results/05_clean_always_verify_seed42 --output-dir phase1_mas/results/my_new_authorization_audit
```

## Scope

The findings cover eight simulated Banking tasks, mainly one seed, and one recorded model configuration. The verifier detects explicit fault signals more readily than plausible silent incorrect data. Full restart cannot undo a side effect that occurred in an initial faulty attempt. The next phase can use the recorded artifact dependencies to evaluate selective recovery.

## Reference

Debenedetti et al., [*AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents*](https://arxiv.org/abs/2406.13352); [source repository](https://github.com/ethz-spylab/agentdojo).
