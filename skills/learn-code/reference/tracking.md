# Tracking and accuracy

The database CLI, what every tag means, what gets logged, and the rules for
being right. Consulted at session start, at session end, and whenever a fact
needs checking.

Loaded from `SKILL.md`.

## Tracking

Persistent mastery lives in a SQLite DB at `~/.claude/learn-code/state.db`,
driven by the CLI shipped next to this file. Call it via Bash:
`python3 ~/.claude/skills/learn-code/learn_code_db.py <subcommand>`. The database
is created on first use. The user never sees the plumbing, never sees the
commands, never sees the tags.

Concepts are two-level: a dotted topic id (`python.dictionaries`) and atomic
concept ids under it (`python.dictionaries/setdefault`). Name concepts yourself
as you teach; the user fills in nothing.

**Session start, one call:** run `brief` (or `brief --topic <topic_id>` when
the topic is already clear from their question). It returns one tagged line per
signal:

| Tag | Meaning | What to do with it |
|-----|---------|--------------------|
| `SUPPORT` | topic, level 0-3, reason | set the scaffolding for that topic |
| `WARMUP` | concept id, label | fold a quick free-text cold check into the opening |
| `MISCONCEPTION` | label, stumble count | watch for it, do not mention it up front |
| `BRIDGE` | concept a, concept b, note | reuse the bridge when either side comes up |
| `DEEPEN` | topic, label | offer it when the current thread is done |
| `CHALLENGE` | topic, solid count, bridges | open with a complex problem on that topic |

`EMPTY` means a fresh learner: start normally, no warm-up.

`CHALLENGE` lines arrive with `brief` and are the normal path. Run
`challenge [--days N] [--min-solid N]` directly (defaults 10 and 2) when the
user asks for something harder or to check ripeness mid-session; it prints
`topic_id<TAB>solid_count<TAB>bridge_count`.

Same logic for warm-ups: `brief` already carries the `WARMUP` lines at session
start, so never call both at the opening. `warmup [--limit N]` (default 2) is
only for later in a long session, when the opening ones are spent and a fresh
cold check is needed mid-thread.

**While teaching:**
- `record-topic <topic_id> <area>` then `record-concept <concept_id> <topic_id>
  <label>` for each atomic concept introduced.
- Only what the learner produced themselves counts as a cold result: an
  explanation in their own words, or working code and commands they wrote
  (see Hands-on review). Never send a closed-choice (A/B/C) answer to
  `cold-result`.
- When a new concept depends on an older tracked one, verify the old one in
  context; a free-text pass there is a real `cold-result <id> pass`.
- A wrong free-text answer also logs: `cold-result <id> fail` (a native
  free-text miss, or the free-text retry that follows a wrong closed-choice
  answer, see Quiz). This feeds `cold_fails`, the objective signal for
  whether corrections are actually landing: compare its trend across
  sessions over weeks or months rather than assuming the teaching style
  works.
- On a stumble tied to a known trap: `misconception <label> --concept <id>`.
  When later demonstrated cleanly: `resolve-misconception <label>`.
- On a real cross-area bridge that landed: `link <a> <b> "<why>"` (both sides
  need `record-topic`/`record-concept` first).
- On a "remember to deepen X": `to-deepen <topic_id> <label>`, closed later
  with `deepen-done <label>`.
- When the user asks for more help: `support-floor <topic_id> <level>` (run
  `record-topic` first if the topic isn't tracked yet, or it fails).
- On a governing idea for a topic: `arc-open <topic_id> --thesis "<sentence>"
  [--direction "<what it unlocks>"]`, or `arc-open <topic_id> --no-thesis
  "<why there isn't one>"` when the topic is pure volume with no thesis to
  state. Close it with `arc-close <arc_id>` when the idea has run its course.
  `arc-current [--topic <topic_id>]` lists open arcs. Link a concept to the
  arc it belongs to with `record-concept ... --arc <arc_id>`.

**Session end:** run `end-session --topics <csv>`, then `render-map`.

**Showing progress:** on "how am I doing?", in any language, run `bars` and
show the output. `render-map` rewrites `~/.claude/learn-code/growth-map.md`.
The DB is also authentic material for the SQL parts of the skill: real queries
against the user's own progress.

- Bridges show up in the growth map, so the learner sees their own knowledge
  becoming one network instead of separate boxes.

## Accuracy

Consult `context7` when unsure about an external library. Unsure about stdlib:
`context7`, then execute it to confirm. State versions when behavior depends on
them. Contested best practices: present the positions. Anything past the
knowledge cutoff: web search. Verify non-trivial examples by running them; mark
anything unverifiable as untested. Never present an invented command as real.

If the tutor stated something wrong: correct silently if the user hasn't acted
on it, explicitly if they already wrote it down or used it.

Brevity governs the form, never the substance.


## The plan file

`~/.claude/learn-code/plan.md`, optional, owned by the learner. Read it at
session start beside `brief`: the database says what has been learned, the plan
says where it is going. Together they give a block its direction, which is what
fills an arc's `--direction`.

Three sections: `Now`, `Next`, `Not now`. The last one is the one that earns
its keep: it lists what was deliberately ruled out and why, so a suggestion
already rejected is not offered again.

**Never ask the learner to create this file.** A missing plan is a supported
state, not a problem to report: point at the next concept instead of a
milestone and say nothing about plans. No wizard, no warning, no first-run
prompt. `reference/plan-template.md` is the shape, for a learner who goes
looking.
