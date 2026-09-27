# SENTINEL

Sentinel collects real telemetry, builds feature windows, scores a frozen model and displays recorded incidents.

This new public repository is receiving a staged import of validated local work. There are three commits per phase for Phases 1–9 and five for Phase 10, pushed at least five minutes apart. Commit messages describe publication batches, not historical development checkpoints. Intermediate snapshots may reference files that arrive later; wait for the final CI and documentation batch before trying to build or deploy.

Phase 10 is incomplete. A live Railway deployment, trusted HTTPS, published images and remote CI have not been verified. The frozen model missed all ten held-out positive windows (recall and F1 were zero); deploying it does not establish predictive usefulness. No threshold or artifact is changed to force a demonstration.

See [the publication plan](docs/publication-plan.md) for the commit scopes. The complete project README will replace this temporary import notice in the final batch.
