# Plan

The shape of `~/.claude/learn-code/plan.md`. The first-run setup writes it
(`reference/onboarding.md`), and the tutor reads it at session start, beside
the progress database, to say where a block leads. It stays on the learner's
machine.

A learner who skipped the setup has no plan file, and that is fine: the tutor
points at the next concept instead of at a milestone.

## Profile

- **Language**: the language every reply uses.
- **Level**: per topic, never used, the basics, or already in use.
- **Purpose**: work, exam or course, project, or curiosity, with a date if any.
- **Time**: minutes per day and days per week, and how they split between new
  material and review.
- **Runs code in**: Jupyter, VS Code, a terminal, or not set up yet.
- **Sources**: what the learner studies from. Their own files live in
  `~/learn-code/sources/`.

## Now

One line per current thread: what is being studied, and in which block.

## Next

What each current thread unlocks, with a rough when.

## Not now

What was deliberately postponed, and why. The tutor reads this section before
suggesting anything new, so a thing ruled out once stays ruled out. The reason
matters more than the exclusion: without it the same idea comes back every few
weeks wearing a different hat.
