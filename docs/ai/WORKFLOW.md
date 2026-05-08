# Dual AI Development Workflow

## Directories

Check actual worktree names with `git worktree list` from the main repo.
This repo (`assistant-dev`) is the dev worktree; `assistant-review` is the review worktree.

Actual layout (example):
- `~/assistant` — master (main repo)
- `~/assistant-dev` — ai/dev-deepseek (dev worktree)
- `~/assistant-review` — ai/review-sonnet (review worktree)

## Development

```bash
cd ~/assistant-dev
claude-deepseek
```

Prompt:

```text
Read docs/ai/DEVELOPER_AGENT.md and follow it.
Implement the requested task.
Run tests.
Commit your changes.
Report summary, tests, commit hash, and risks.
```

## Review

First point the review worktree at the dev commit:

```bash
cd ~/assistant-dev
DEV_COMMIT=$(git rev-parse HEAD)

cd ~/assistant-review
git checkout -B ai/review-sonnet "$DEV_COMMIT"
```

Then run:

```bash
claude-sonnet-review
```

Prompt:

```text
Read docs/ai/REVIEWER_AGENT.md and follow it.
Review current branch against main.
Run tests if practical.
Do not modify files unless explicitly asked.
Return verdict, blocking issues, non-blocking issues, and merge recommendation.
```

## Apply review feedback

Preferred flow:

1. Copy reviewer feedback.
2. Go back to dev worktree.
3. Ask DeepSeek to fix only the blocking issues.
4. Run tests.
5. Commit fixes.
6. Repeat review.

## Merge

Only after review approval:

```bash
cd ~/assistant
git checkout master
git merge ai/dev-deepseek
git push
```
