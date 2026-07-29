# Human Interaction Model

## Document purpose

Define what humans do through the Panam Web App and what technical operators do through CyberDeck.

## In-scope responsibilities

The Panam Web App is the primary human workflow cockpit. Its v1 areas are Development Dashboard, Projects, Phases, Milestones, Runs, Approvals, Recovery, and Settings. It owns project/phase workspaces, Milestone Contracts, approvals, run evidence, review display, Vault proposals, recovery decisions, and Phase Merge Packages.

CyberDeck owns worker health, command queue, processes, locks, operation journal, logs, recovery diagnostics, and emergency stop.

Discord sends notifications and brief status only. It must not be used for canonical approvals.

### Human decision surfaces

- Phase Start Approval;
- Approval 1;
- Awaiting Human Decision;
- Approval 2;
- Recovery Decision;
- Create Pull Request.

Web actions create durable commands. The Development Worker executes those commands; neither Web routes nor browser actions directly execute external side effects.

## Approved decisions

Approval 1 permits the bounded implementation/correction/source-completion scope stated in [the policy](05-safety-git-approval-policy.md). Approval 2 is limited to one exact Vault proposal and named paths. PR approval and merge remain human-controlled.

## Explicit boundaries and out of scope

CyberDeck does not replace the Web approval workspace. Discord does not provide approval records. Flask handlers do not perform Codex, Git, or Vault work inline.

## Cross-references

- [Actors and responsibilities](01-actors-and-responsibilities.md)
- [Workflow](02-end-to-end-workflow.md)
- [Failure and recovery](06-failure-and-recovery.md)

## Future considerations

Presentation design, authentication details, and notification delivery guarantees are future implementation work.
