# ADR-005: Approval-bound Authority

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Development work can create external effects in Git, `Vault_work`, and paid services. Natural-language intent alone cannot safely define who may act, on what state, or within which scope. DL-0.1 defines registered project policy, Milestone Contracts, and explicit approval gates.

## Decision

A prompt is not authorization. Authority for an action derives only from the conjunction of:

1. registered policy;
2. an approved, versioned contract;
3. a valid approval record;
4. repository or Vault state that still matches the approval binding.

An approval record binds the exact subject and digest, permitted actions, scope, repository identity, branch, base commit or inspected HEAD as applicable, allowed paths, approver, and timestamp. Approval 1 and Approval 2 retain their DL-0.1 scopes. Global deny rules override project policy; project policy overrides contract requests; a contract and approval may narrow but never broaden policy.

Before every authorized effect, Panam revalidates the action, approval, contract digest, branch, paths, relevant state digest, and current external reality. A protected-content or relevant state change invalidates the affected authority. Neither a specialist nor the State Machine may infer broader permission from a prior successful action.

## Rationale

Exact approval binding prevents stale consent, prompt injection, scope drift, and confused-deputy behavior. Layered authorization makes the human decision inspectable and enforceable at the moment of action.

## Consequences

- Each effect must be attributable to a specific valid approval and contract version.
- Changes to approved scope or relevant repository state require revalidation and, when the binding no longer matches, a new human decision.
- Capability Registry changes require the separate explicit flag defined in DL-0.1.
- Approval does not make forbidden actions permissible.

## Rejected alternatives

- **Prompt as approval**: rejected because prompts are unregistered, mutable input.
- **Role-based blanket authority**: rejected because role identity does not bind action, state, or scope.
- **One approval for an open-ended run**: rejected because it permits silent expansion and stale authority.

## V1 boundary

V1 retains Phase Start Approval, Approval 1, Approval 2, human recovery decisions, human-confirmed Pull Request creation, and human-controlled merge. Panam prepares the Phase Merge Package; Matej explicitly confirms Pull Request creation; Panam may then technically create the Pull Request through a future adapter. Panam never approves or merges the Pull Request, and merge remains human-controlled. The adapter is not currently implemented.

This ADR creates no approval schema or Capability Registry entry.

## Future extension point

Later policy engines or signed approval mechanisms may implement the same binding model but may not weaken exact-action, exact-scope, and matching-state requirements.

## Related architecture documents

- [Safety, Git, and approval policy](../05-safety-git-approval-policy.md)
- [Core data model](../04-core-data-model.md)
- [Human interaction model](../09-human-interaction-model.md)
- [ADR-004: Evidence-driven completion and freshness](adr-004-evidence-driven-completion-and-freshness.md)
