# PR authoring

## Contents

- [Sizing a PR](#sizing-a-pr)
- [Branch and commit naming](#branch-and-commit-naming)
- [The PR body template](#the-pr-body-template)
- [The two human gates](#the-two-human-gates)
- [Local gates before pushing](#local-gates-before-pushing)
- [Opening it](#opening-it)

## Sizing a PR

One task, one PR, one squashed commit on main. The constraint is not aesthetic: post-merge verification asks "did *this* change break main," and that question only has an answer when the commit is one change.

Split when a task contains any of these:

- A behaviour change **and** a refactor of the code around it. Ship the refactor first, green, then the behaviour change against a clean baseline.
- A change to a scoring, blocking, or clustering model **and** a change to its baseline. The baseline regeneration is its own PR with its own review — otherwise the PR that moves the numbers is also the PR that says the new numbers are correct.
- A dependency pin bump alongside anything else. §16's bump ritual is a PR of its own.

Do not split a model's `.sql` from its `.yml`. §6's 1:1 rule means either half alone fails the bouncer, so a split there produces a red PR by construction.

## Branch and commit naming

```
<type>/<short-slug>
```

`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `revert`. Slug is two to four words, hyphenated: `feat/blocking-rule-coverage`, `fix/parity-harness-lock-order`.

Commits use the same types. The squash commit title becomes the PR title, so write the PR title as the commit message you want on main.

```
feat: add coverage floor to the blocking rule bouncer check
fix: order the comparator output before hashing
```

End commit messages with the session's standard `Co-Authored-By:` trailer.

## The PR body template

```markdown
## What

<One paragraph. What changed and why, in terms of behaviour, not files.>

## How it was verified

- [ ] `make lint`
- [ ] `make build`
- [ ] `make ci`
- <Anything CI cannot run, and how you covered it instead.>

## Gates

- **Splink source permalink:** <permalink, or "n/a — not parity-affecting">
- **Divergence-log entry:** <link, or "n/a — no deliberate difference from the oracle">

## Risk

<What breaks if this is wrong, and what would catch it. "Nothing would catch it"
is a valid and useful answer — it names a gap in the gate topology.>
```

Write the body to a file and pass `--body-file`. A heredoc through the shell mangles backticks and `$`.

If the task came from a ticket written by `er-ticket-writer`, the ticket already carries Given/When/Then acceptance criteria, a named oracle, and the definition of done. Restate those in **How it was verified** and link the ticket — do not paraphrase the criteria into something weaker.

## The two human gates

§17 puts two things in the PR template that no gate can mechanise. They are labelled convention rather than enforcement in §3 *because a checkbox is not a gate* — which means they depend entirely on being filled in honestly.

**Splink source permalink.** Required for any parity-affecting change: anything touching scoring, blocking, comparison, or clustering. The permalink points at the Splink source that defines the behaviour being matched — a specific line range at a specific commit, not a branch URL, which moves.

**Divergence-log entry.** Required for any deliberate difference from the oracle. A divergence that is not logged is indistinguishable from a bug, and the parity harness will eventually flag it as one.

If a change is parity-affecting and you cannot produce the permalink, that is a signal the change is not understood well enough to ship. Say so rather than writing "n/a".

## Local gates before pushing

§17: every Make target is also a CI step. A gate that only exists in CI does not exist, because nobody can run it before pushing.

```bash
make lint     # sqlfluff, yamllint, ruff, mypy, the four repo checks
make build    # dbt seed && dbt build --full-refresh (unit + data tests)
make docs     # catalog.json, needed by the bouncer catalog tier
make bouncer  # all three artifact tiers
make ci       # everything CI runs, in CI's order
```

`make ci` before every push. It costs minutes locally and saves a round trip through the whole PR loop.

Two ordering constraints that bite when running gates by hand (§15):

- `dbt seed` must run before `dbt build`. The package reads a *source*, and a source creates no DAG edge, so nothing orders the fixture's creation.
- The parity harness cannot run while dbt holds the DuckDB file. DuckDB takes a process-level lock; read-only does not help.

If the Makefile does not exist, run the underlying tools directly and **name the gates you could not run** in the PR body. An unrun gate reported as passed is the failure mode §21 is about.

## Opening it

```bash
git push -u origin HEAD
gh pr create --base main --title "<type>: <what changed>" --body-file /path/to/body.md
```

Ready for review, not draft, unless the user asked for a draft — `babysit-pr` treats `DRAFT` as merge-blocking and the ship loop will stall waiting on it.

Then hand straight to `babysit-pr`. Do not poll the PR yourself in the meantime; two watchers on one PR each read the other's silence as calm.
