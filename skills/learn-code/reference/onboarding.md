# First run

Loaded from `SKILL.md` when `brief` returns `EMPTY` and
`~/.claude/learn-code/plan.md` does not exist. It runs once, ever.

The goal is a usable plan in about two minutes, with the first lesson starting
right after. People skip long setups, so every question is short, one per
message, and answered with a click whenever the client offers selectable
options (in Claude Code, the AskUserQuestion tool). Every question can be
skipped; a skipped answer takes the default in brackets. If the learner says
"just teach me", or asks a real question instead of answering, stop at once and
teach: a missing plan is a supported state, and their question outranks setup.

Open with one line: what this is (a few quick questions, mostly one click) and
how to skip it.

## The seven questions

1. **Language.** The language every reply uses from now on: English, Italiano,
   Español, Deutsch, Français, or other. [the language of their first message]
2. **Topics.** What they want to learn now, one word per topic is enough:
   programming, computing, data, or the math and science behind them. If they
   name something outside that scope, say in one line that learn-code is built
   for code and its supporting subjects, and keep the topics that fit.
   [the topic of their first message]
3. **Level, per topic.** Never used · The basics · I use it already. It sets
   how much help each topic starts with. [never used]
4. **What it is for.** Work · Exam or course · Personal project · Curiosity.
   For work or an exam, ask for a rough date: within a month · 1-3 months ·
   later · none. It decides priorities and pace. [curiosity, no date]
5. **Time.** Per day: 15 min · 30 min · 1 hour · 2 hours or more. Per week:
   3 days · 5 days · every day. Propose the split: about two thirds new
   material, one third review, in separate blocks when there is room for two.
   Only if the session has calendar or reminder tools, add: "Put the study
   blocks on your calendar, and a reminder on review days?" Yes, both ·
   Calendar only · No. Never create anything before the learner has seen it.
   [30 min, 5 days, no calendar]
6. **Where you run code.** Jupyter or notebooks · VS Code · Terminal (python,
   ipython) · Not sure yet. Examples and tasks are written for that tool. With
   "not sure yet", the first task of the first lesson is setting one up.
   [terminal]
7. **Sources.** Create `~/learn-code/sources/` (this is the plugin's own setup,
   not a learning task, so the tutor creates it), then offer: I'll add my
   material there now · Later · I have none, suggest some. Material there is
   what lessons and reviews are built on. [later]

## Suggesting sources

When the learner has none, suggest two or three per topic and let them pick.
Only material that is free and legal to use: official documentation, free
courses from the people who make the tool, open-access textbooks and lecture
notes. Look each one up and check the link works before offering it; never
suggest from memory, and never point at copies of paid material. The picks go
into the plan as sources.

## The plan

Write `~/.claude/learn-code/plan.md` from `reference/plan-template.md`: the
answers go in **Profile**, the topics in their order go in **Now** and
**Next**. Show it as a short table, then any calendar events or reminders,
created only after a yes. Record each topic with `record-topic`. Tell the
learner they can change anything later just by saying so.

Then teach the first concept of the first topic, in the same reply or the next.
The setup ends where learning starts.
