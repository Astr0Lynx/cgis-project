# Git/GitHub workflow: 6 independent agents, no PRs

Repository: `https://github.com/Astr0Lynx/cgis-project` (PRIVATE).

## Required branches

1. Agent 1 alone creates and pushes `phase2/base` from the repository's actual default branch. It contains only common contracts, module skeletons, and non-breaking baseline setup. Publish its commit SHA as `BASE_SHA`.
2. Agent 1 creates `phase2/agent1-infrastructure` at `BASE_SHA`; Agent 2 creates `phase2/agent2-postgis`; Agent 3 `phase2/agent3-ingestion`; Agent 4 `phase2/agent4-ogc`; Agent 5 `phase2/agent5-processing`. Every feature branch starts from the **same frozen BASE_SHA** and receives no other feature merges while parallel work runs.
3. Agent 6 starts only after Agents 1–5 push final commits. It creates `phase2/integration` from `phase2/base`, merges their branches in the explicit sequence in `RUN_ORDER.md`, and tests everything before publishing main.

## Read-only repository discovery first

Agents must inspect `git status`, `git branch -a`, `git remote -v`, repository tree, README, licensing, existing workflows, and `.gitignore`. Never assume the repo is empty, `main` is default, or changes are disposable. If incompatible substantive code already exists, report and adapt safely; do not overwrite. If the remote truly has **zero commits**, Agent 1 may bootstrap a minimal README/.gitignore on the intended default branch as the initial root commit before creating `phase2/base`, documenting that one-time exception; do not overwrite an existing branch. Authentication must be supplied through the user's normal Git/GitHub setup, never copied into docs or logs.

## Isolation

- **One agent = one independent clone or Git worktree and one feature branch.** Agents may share a GitHub repo, but not a working directory.
- No agent except Agent 1 changes `phase2/base`; no one except Agent 6 merges feature branches.
- Agents 2–5 must not directly modify root Compose, `database/` owned by Agent 2, or others' module code. For integration needs, write contract-change or compose-change request docs in their module.
- Each branch commits code, tests, docs, and `HANDOFF_REPORT.md`, then pushes to origin. No PR is opened.
- Never push directly to the default branch from Agents 1–5; no `--force`, `reset --hard`, destructive cleanup, credential injection, or bypassing branch protection.
- If a branch already exists, inspect its history/owner before modifying it. Do not overwrite someone else's work.

## Agent 6 integration procedure

1. Verify all five branches have pushed clean, reproducible HEAD SHAs and their handoff reports.
2. Confirm each feature branch descends from `BASE_SHA`, with no unexpected default-branch mutations.
3. Create `phase2/integration` at BASE_SHA, then merge `agent1-infrastructure`, `agent2-postgis`, `agent3-ingestion`, `agent4-ogc`, `agent5-processing` with regular non-fast-forward merge commits to preserve provenance. Reorder only if necessary, documenting why.
4. Resolve conflicts by honoring ownership and frozen contracts. Never drop changes without documenting their resolution and rerunning tests. Contact the relevant owner if resolution changes its semantics.
5. Complete integration code in Agent 6-owned paths, then run fresh-clone Docker, functional, end-to-end, security smoke, and conformance tests. Record results and evidence.
6. Push `phase2/integration` to GitHub. If ALL exit gates pass and default branch remains unchanged since BASE_SHA, bring default branch forward by a **fast-forward merge** of the tested integration commit and push. No PR required.
7. If default branch changed, branch protection disallows push, tests fail, or permissions are missing: **STOP**. Leave a tested integration branch and report the exact blocker. Do not bypass protections or force push.

## Suggested Git command patterns (adapt to actual repo)

Agent 1 baseline:

```bash
git fetch origin
git switch -c phase2/base <actual-default-branch-ref>
# commit contracts/skeleton only
git push -u origin phase2/base
git rev-parse HEAD   # BASE_SHA
git switch -c phase2/agent1-infrastructure
```

Parallel Agents 2–5:

```bash
git fetch origin phase2/base
git switch -c phase2/agentN-name <BASE_SHA>
# edit owned module, test, commit
git push -u origin phase2/agentN-name
```

Agent 6 merges with `git merge --no-ff origin/phase2/agentN-name`, not `git cherry-pick` or blind file copies. After tests, fast-forward the actual default branch only if permitted and still based on the expected ancestor.

## Required end-of-run report for each agent

- Branch and immutable HEAD SHA; actual files changed; executable commands run and their results.
- Exact service/API/data contracts implemented, deviations, and any unresolved requests.
- Fixtures, test cases, known limitations, security or correctness caveats.
- Integration-specific notes; no secrets or sensitive terminal output.
