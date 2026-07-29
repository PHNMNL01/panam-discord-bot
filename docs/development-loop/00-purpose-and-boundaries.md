# Purpose and Boundaries

## Document purpose

Define what Panam Development Loop v1 is, what it coordinates, and what it deliberately does not automate.

## In-scope responsibilities

Panam Development Loop is an approval-driven, auditable orchestration system for software-development milestones. Its approved sequence is:

```text
Planner → Feasibility Assessor → Approval 1 → Codex → Verifier → Reviewer
→ focused correction loop → Handoff Agent → source commits and push
→ Knowledge Curator → Approval 2 → Vault Writer → phase integration package
→ human-confirmed Pull Request creation → human merge
```

Panam orchestrates this sequence; it is not a replacement coding model and is not itself the Planner, Codex, Reviewer, or Vault Writer.

## Approved decisions

- One Development Run executes one approved Milestone Contract.
- One development phase uses one long-lived phase branch; milestones execute sequentially on it.
- The first prototype runs locally on the Development Host. A Windows VM is the intended future isolated deployment; VDS is not a prerequisite.
- SQLite, Git, `Vault_work`, and `AI_Agents` have the source-of-truth roles listed in the [README](README.md).
- Large outputs remain filesystem artifacts, with durable references held in SQLite.

## Explicit boundaries and out of scope

- Panam never automatically approves or merges a Pull Request.
- Panam prepares the Phase Merge Package automatically. After Matej explicitly confirms Pull Request creation, Panam may technically create the Pull Request.
- Automatic branch deletion, automatic dependency installation, arbitrary host-path access, and arbitrary shell commands are outside v1.
- Codex does not commit, push, merge, rebase, alter phase branches, or modify `Vault_work` or `AI_Agents`.
- Discord is not a canonical approval surface.

## Cross-references

- [Actors and responsibilities](01-actors-and-responsibilities.md)
- [End-to-end workflow](02-end-to-end-workflow.md)
- [Safety, Git, and approval policy](05-safety-git-approval-policy.md)

## Future considerations

Deployment isolation is intentionally host-neutral. A later implementation may move the Development Worker to a VM without changing domain contracts.
