# ADR-006: Budgets, Retries, and No-progress Escalation

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Autonomous correction can consume unbounded time and money or repeat ineffective changes. An attempt limit alone cannot cover long-running calls, token or cost growth, repeated failures, or early evidence that the loop is not improving.

## Decision

Every autonomous or repeated loop has an explicit minimum budget-policy contract before execution begins:

```text
max_attempts
max_wall_clock_seconds
max_model_tokens
max_estimated_cost
max_unchanged_iterations
timeout_policy
escalation_target
```

These are canonical policy concepts. Their exact schema types and configured numeric values remain later implementation decisions. The State Machine evaluates remaining budgets before authorizing another cycle; specialists cannot reset, extend, or reinterpret them.

The initial Development Loop policy is:

```text
1 primary Codex execution
maximum 3 focused correction attempts
```

Focused corrections preserve the approved Milestone Contract and scope. A fourth focused correction is forbidden. Retries also stop before the numeric maximum when two consecutive attempts produce no measurable progress.

Measurable progress is a recorded, relevant change in one or more of:

- diff digest;
- failing test set;
- reviewer findings;
- satisfied acceptance criteria;
- verification status.

No measurable progress across two consecutive attempts stops automatic retry and requires a human decision through the existing human-decision or blocked path, as determined by current evidence and policy. Budget exhaustion, invalid authority, repeated equivalent failure, or a stop condition likewise prevents automatic continuation.

## Rationale

Multidimensional budgets bound resource exposure, while progress-sensitive stopping avoids spending the full retry allowance on a stalled approach. Human escalation preserves control when automation cannot justify another attempt.

## Consequences

- Attempt records must capture budget use, results, progress indicators, and stop reasons.
- Correction prompts or contracts must be focused on current findings and may not expand scope.
- A budget is an upper bound, not an entitlement to use every attempt.
- Human approval is required to revise the contract or authorize a new bounded run after escalation.

## Rejected alternatives

- **Retry until success**: rejected as unbounded and unsafe.
- **Three corrections regardless of progress**: rejected because it wastes resources on repeated non-progress.
- **Attempt count as the only budget**: rejected because a single attempt may exceed acceptable time, token, or cost limits.

## V1 boundary

V1 uses one primary execution and at most three focused corrections, with early no-progress escalation. Exact default time, token, and cost values belong to later approved policy configuration.

## Future extension point

Later workflow types may use different approved budgets and progress measures, but every autonomous cycle remains bounded and State-Machine governed.

## Related architecture documents

- [End-to-end workflow](../02-end-to-end-workflow.md)
- [State Machine](../03-state-machine.md)
- [Failure and recovery](../06-failure-and-recovery.md)
- [ADR-009: Supervised autonomy calibration](adr-009-supervised-autonomy-calibration.md)
