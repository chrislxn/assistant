# Dual AI Development Workflow

## Directories

- `agents-a8f9af1c4a-dev`: DeepSeek V4 Pro development worktree.
- `agents-a8f9af1c4a-review`: Claude Sonnet review worktree.

## Development

```bash
cd ~/projects/agents-a8f9af1c4a-dev
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
cd ~/projects/agents-a8f9af1c4a-dev
DEV_COMMIT=$(git rev-parse HEAD)

cd ~/projects/agents-a8f9af1c4a-review
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
cd ~/projects/agents-a8f9af1c4a
git checkout master
git merge ai/dev-deepseek
git push
```
