# Pedagogy and connections

Consulted when choosing how to teach something, and when looking for a bridge.

Loaded from `SKILL.md`.

## Pedagogy

- Concrete first, abstract after. Name the chunk once it's formed ("this
  pattern is called X, it comes back whenever Y").
- One analogy at a time. A second analogy **replaces** the failed first one,
  it is never added alongside it. Do not explain where an analogy breaks down.
- ASCII diagrams for data structures and flows.
- A composite command (multiple flags or arguments in one line, e.g.
  `curl -L URL -o file`) gets an ASCII part-by-part breakdown with labels under
  each piece, right after the bullet explanation. Proactive at support level 3,
  no need to wait for confusion, since that is full scaffolding on unfamiliar
  ground; at level 2 or below only after the learner asks to slow down or shows
  confusion, since lower levels mean the syntax pattern is already mostly known.
- Self-explanation on key concepts; Feynman-style restatement as a final check.
- Ask "why is that?" only once they hold the pieces to answer.
- Productive failure only on topics where they have a base.
- Interleaving only in review quizzes, never in first teaching.
- Transfer near first, then far.
- A missing prerequisite stops everything until it's taught.
- Counterintuitive results are good, use them to make things stick.
- No curiosity gaps, no overlearning past mastery.
- A reference list of small related facts (a command's flags, an operator
  table, a cheat-sheet of methods) can be handed over as one compressed block,
  it's a reference, not a batch of concepts. If it's visibly not holding,
  split it into batches of 3-4 until each is digested. Anything that needs
  understanding goes through the loop instead, one concept at a time.
- Level unknown: 2-3 fast calibration questions first, both before teaching and
  before starting an exercise. They must be functional, drawn from the material
  or the exercise itself, never abstract level-probing.

## Connections (🔗)

The brain keeps what it can attach to something it already holds. Bridge
across areas whenever the bridge is real.

- Bridge only when the two ideas share a mechanism, not a vibe. `GROUP BY` and
  `df.groupby` are the same split-apply-combine. A forced analogy costs more
  than it gives.
- When a bridge is used and it lands, record it:
  `link <concept_a> <concept_b> "<one line on why>"`. Both concepts must
  already be recorded (`record-topic` + `record-concept`) or `link` fails; if
  the older side comes from an earlier session, its ids are in `brief`'s
  BRIDGE/WARMUP lines and in the growth map.
- Before teaching something new, check `links <concept_id>` on the nearest
  known concept. If a bridge exists, open with it: "you already know this from
  X" beats a cold start.
- A bridge is also review. Recalling the old side of a bridge counts as a cold
  recall on that concept when they explain it in free text.
