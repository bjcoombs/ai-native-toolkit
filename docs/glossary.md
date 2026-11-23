# Glossary

Plain-English meanings of the words the `/assess` report uses, one or two sentences each. The [scoring model](../skills/assess/references/scoring-model.md) holds the full technical definitions.

### Step

How much an AI agent can safely do in your repository on its own, from Step 1, where an agent can read and help with your code, to Step 4, where agents run longer loops on their own. It describes what the repository's safeguards support, not how your team works today.

### Gate

A single check that a Step needs: an instruction file agents read, CI that runs the tests, or a minimum test coverage that CI enforces. A Step is reached when all of its gates are met, and a gate the run could not check does not count as met yet.

### Practice

One area of good engineering habits that `/assess` looks at, such as agent instructions or review on merged pull requests. Each practice is checked at one or more levels.

### Level

One numbered check within a practice: level 1 is the first thing to put in place, level 2 the next. In the evidence chart, each row is a practice and each square one of its levels.

### Condition

One level of one practice, as a single yes-or-no check. Each run reads it as met, not yet met, not measured, or set aside by the repository's owner.

### Evidence grade

How directly the run saw a condition: verified means a check runs in CI on every change or the GitHub or GitLab platform reports it, and measured means it was read from the code, its history or the output of a run. A setting in a config file or a statement in a document is listed but not counted, and the grade never moves the Step.

### Truth tag

What a condition helps keep true, such as behaviour, docs, structure or agent context. Tags sit side by side with none ranked above another, and a condition can carry more than one.

### Not measured

The run could not see this condition, for example because it had no network access or ran without the full scan, so it is shown with its reason and never counted as zero.

### Catalogue coverage

What this run found in place: of the conditions that count, how many are met out of how many it could measure, and how many of those are verified. It is a reading of what the run saw, not a score, and it never moves the Step.

### Maturity model

The versioned list of practices, their levels and the Steps that `/assess` checks a repository against. Every result names the model version it used.
