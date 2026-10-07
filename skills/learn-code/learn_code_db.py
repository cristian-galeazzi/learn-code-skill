"""learn-code tracking CLI: SQLite-backed concept mastery for /learn-code."""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".claude" / "learn-code" / "state.db"

# acronym areas that .capitalize() would mangle into Sql, Ml, Dl
AREA_LABELS = {"sql": "SQL", "ml": "ML", "dl": "DL", "html": "HTML", "css": "CSS"}

# "not recalled recently" decay window, shared by concepts (challenge_candidates)
# and arc theses (brief): a single number for what "recent" means.
COLD_DECAY_DAYS = 10

# How many unclosed to-deepen notes brief shows. The rest live in growth-map.md.
DEEPEN_SHOWN = 3

# Spaced recall (2026-10-07). A solid concept used to stay solid forever, so it
# was never asked again and quietly faded (linspace: solid on 09-25, forgotten by
# 10-06). Now it comes back after RECALL_BASE_DAYS * RECALL_GROWTH**(net - 1)
# days, net = passes - fails. No cap on purpose: a capped interval makes every
# solid concept return forever, and daily load would grow with the whole DB.
RECALL_BASE_DAYS = 7
RECALL_GROWTH = 3
# Practice lane size and per-area cap: 10 hands-on items per Hands block, and no
# area may take more than 3 of them, so a topic with many fails (stats) cannot
# crowd out the others.
PRACTICE_MAX = 10
AREA_CAP = 3
# Never-tested backlog must drain faster than new concepts arrive: the daily
# quota is yesterday's new concepts plus 1/BACKLOG_DAYS of the backlog.
BACKLOG_DAYS = 7
# A check in the session that taught a concept shows understanding, not memory:
# the first real recall comes FIRST_RECALL_DAYS later. A cold fail is retested
# from the next day.
FIRST_RECALL_DAYS = 2
FAIL_RETEST_DAYS = 1
# Coverage outranks every other rule: a topic with no cold recall in this many
# days gets one concept in today's queue, whatever its due dates say. Weeks
# without touching studied material (20 days in Sept-Oct 2026) must not recur.
COVERAGE_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, area TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id),
    label TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('learning','shaky','solid')),
    intro_session TEXT NOT NULL,
    cold_passes INTEGER NOT NULL DEFAULT 0,
    cold_fails INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL, last_cold TEXT,
    source TEXT);
CREATE TABLE IF NOT EXISTS recalls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    ts TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('pass','fail','hint')));
CREATE TABLE IF NOT EXISTS misconceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id TEXT REFERENCES concepts(id),
    label TEXT NOT NULL, stumbles INTEGER NOT NULL DEFAULT 1,
    resolved INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS to_deepen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT REFERENCES topics(id),
    label TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, topics_seen TEXT);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    a_id TEXT NOT NULL REFERENCES concepts(id),
    b_id TEXT NOT NULL REFERENCES concepts(id),
    note TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (a_id, b_id));
CREATE TABLE IF NOT EXISTS arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL REFERENCES topics(id),
    thesis TEXT,
    no_thesis_reason TEXT,
    direction TEXT,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    last_recalled TEXT);
CREATE TABLE IF NOT EXISTS arc_concepts (
    arc_id INTEGER NOT NULL REFERENCES arcs(id),
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    PRIMARY KEY (arc_id, concept_id));
"""


def now() -> str:
    """Return current UTC time as an ISO string.

    >>> isinstance(now(), str)
    True
    """
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    """Return current UTC date as YYYY-MM-DD.

    >>> len(today()) == 10
    True
    """
    return datetime.now(timezone.utc).date().isoformat()


def db_path(override: str | None) -> Path:
    """Resolve the DB path, creating the parent directory.

    >>> db_path("/tmp/lc/state.db").name  # doctest: +SKIP
    'state.db'
    """
    p = Path(override) if override else DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect(override: str | None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys on and the schema in place.

    >>> connect("/tmp/lc/state.db")  # doctest: +SKIP
    <sqlite3.Connection object at ...>
    """
    conn = sqlite3.connect(db_path(override))
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)  # schema is CREATE IF NOT EXISTS, so a fresh DB never needs `init`
    migrate(conn)  # older personal DBs predate support_floor
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns missing from DBs created by an earlier version.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> migrate(conn)
    >>> migrate(conn)  # idempotent
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topics)")}
    if "support_floor" not in cols:
        conn.execute("ALTER TABLE topics ADD COLUMN support_floor INTEGER")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(concepts)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN source TEXT")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables idempotently.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    0
    """
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)


def record_topic(conn: sqlite3.Connection, topic_id: str, area: str) -> None:
    """Insert or update a topic.

    >>> conn = sqlite3.connect(":memory:")  # doctest: +SKIP
    >>> init_db(conn)  # doctest: +SKIP
    >>> record_topic(conn, "python.basics", "python")  # doctest: +SKIP
    """
    ts = now()
    conn.execute(
        "INSERT INTO topics (id, area, created_at, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET area=excluded.area, updated_at=excluded.updated_at",
        (topic_id, area, ts, ts))
    conn.commit()


def record_concept(conn: sqlite3.Connection, concept_id: str,
                   topic_id: str, label: str, source: str | None = None) -> None:
    """Insert a concept as 'learning' if new, else touch last_seen.

    `source` is where it was learned (book and page, course section, official
    docs), so review can reopen the real page instead of recalling from memory.

    >>> conn = sqlite3.connect(":memory:")  # doctest: +SKIP
    >>> init_db(conn)  # doctest: +SKIP
    >>> record_concept(conn, "c1", "t1", "label")  # doctest: +SKIP
    """
    ts = now()
    exists = conn.execute("SELECT 1 FROM concepts WHERE id=?",
                          (concept_id,)).fetchone()
    if exists:
        conn.execute("UPDATE concepts SET last_seen=?, source=COALESCE(source, ?) "
                     "WHERE id=?", (ts, source, concept_id))
    else:
        conn.execute(
            "INSERT INTO concepts (id, topic_id, label, state, intro_session, "
            "last_seen, source) VALUES (?,?,?,'learning',?,?,?)",
            (concept_id, topic_id, label, today(), ts, source))
    conn.commit()


def cold_result(conn: sqlite3.Connection, concept_id: str, passed: bool,
                hint: bool = False) -> str:
    """Apply the mastery state machine for a cold-recall result; return new state.

    `hint=True` is a recall that needed a nudge: it moves the state like a fail
    (a nudged answer is not memory) but is logged apart, so `report` can tell
    "forgot" from "almost".

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','a','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts "
    ...     "(id, topic_id, label, state, intro_session, last_seen) "
    ...     "VALUES ('t/c','t','c','shaky','x','x')")
    >>> cold_result(conn, "t/c", True)
    'solid'
    >>> cold_result(conn, "t/c", False)
    'shaky'

    A concept still `learning` was taught this session, so its check is not
    cold: the state and counters stay as they are.
    """
    row = conn.execute("SELECT state FROM concepts WHERE id=?",
                       (concept_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown concept: {concept_id}")
    state = row[0]
    if state == "learning":
        # same-session check: understanding, not recall, so no cold counters
        conn.execute("UPDATE concepts SET last_seen=? WHERE id=?", (now(), concept_id))
        conn.commit()
        return state
    if hint:
        passed = False
    if passed and state == "shaky":
        state = "solid"
    elif not passed and state == "solid":
        state = "shaky"
    # shaky+fail stays shaky
    counter = "cold_passes" if passed else "cold_fails"
    ts = now()
    conn.execute(
        f"UPDATE concepts SET state=?, {counter}={counter}+1, "
        "last_cold=?, last_seen=? WHERE id=?",
        (state, ts, ts, concept_id))
    conn.execute("INSERT INTO recalls (concept_id, ts, result) VALUES (?,?,?)",
                 (concept_id, ts, "hint" if hint else "pass" if passed else "fail"))
    conn.commit()
    return state


def end_session(conn: sqlite3.Connection, topics_seen: str) -> None:
    """Flip this session's 'learning' concepts to 'shaky'; record the session.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','a','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts "
    ...     "(id, topic_id, label, state, intro_session, last_seen) "
    ...     "VALUES ('t/c','t','c','learning','x','x')")
    >>> end_session(conn, "t")
    >>> conn.execute("SELECT state FROM concepts WHERE id='t/c'").fetchone()[0]
    'shaky'
    """
    conn.execute("UPDATE concepts SET state='shaky' WHERE state='learning'")
    conn.execute("INSERT OR REPLACE INTO sessions (id, topics_seen) VALUES (?,?)",
                (now(), topics_seen))
    conn.commit()


def recall_interval_days(passes: int, fails: int) -> int:
    """Days a solid concept rests before it is due for cold recall again.

    >>> recall_interval_days(2, 0)
    21
    >>> recall_interval_days(3, 2)  # fails shorten the rest
    7
    """
    net = max(1, passes - fails)
    return RECALL_BASE_DAYS * RECALL_GROWTH ** (net - 1)


def _parse_ts(ts: str) -> datetime:
    """Parse a stored ISO timestamp; naive values are UTC (older rows).

    >>> _parse_ts("2026-10-01T00:00:00").tzinfo is not None
    True
    """
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _eligible_cutoff() -> str:
    """Latest intro_session date old enough for a first real recall.

    >>> _eligible_cutoff() < today()
    True
    """
    return (datetime.now(timezone.utc).date()
            - timedelta(days=FIRST_RECALL_DAYS)).isoformat()


def warmup(conn: sqlite3.Connection, limit: int = PRACTICE_MAX,
           area_cap: int = AREA_CAP,
           exclude: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Return the practice lane: failed concepts due again, then solid ones due.

    Failed first, each group most overdue first, at most `area_cap` per area.
    Never-tested concepts are the flash lane's job (see `flash`).

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','python','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts "
    ...     "(id, topic_id, label, state, intro_session, last_seen, cold_fails, last_cold) "
    ...     "VALUES ('t/c','t','c','shaky','x','x',1,'2026-01-01T00:00:00+00:00')")
    >>> warmup(conn, limit=1)
    [('t/c', 'c')]
    """
    rows = conn.execute(
        "SELECT c.id, c.label, c.state, c.cold_passes, c.cold_fails, c.last_cold, t.area "
        "FROM concepts c JOIN topics t ON t.id = c.topic_id "
        "WHERE c.last_cold IS NOT NULL AND c.state IN ('shaky','solid')").fetchall()
    now_dt = datetime.now(timezone.utc)
    due: list[tuple[int, datetime, str, str, str]] = []
    for cid, label, state, passes, fails, last_cold, area in rows:
        if cid in exclude:
            continue
        if state == "solid":
            rank, wait = 1, recall_interval_days(passes, fails)
        else:
            rank, wait = 0, FAIL_RETEST_DAYS
        due_at = _parse_ts(last_cold) + timedelta(days=wait)
        if due_at <= now_dt:
            due.append((rank, due_at, cid, label, area))
    due.sort()
    picked: list[tuple[str, str]] = []
    per_area: dict[str, int] = {}
    for _, _, cid, label, area in due:
        if len(picked) >= limit:
            break
        if per_area.get(area, 0) >= area_cap:
            continue
        per_area[area] = per_area.get(area, 0) + 1
        picked.append((cid, label))
    return picked


def flash(conn: sqlite3.Connection, limit: int,
          exclude: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Return never-tested concepts at least FIRST_RECALL_DAYS old, oldest first.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> flash(conn, 5)
    []
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT id, label FROM concepts WHERE state='shaky' "
        "AND cold_passes=0 AND cold_fails=0 AND intro_session <= ? "
        "ORDER BY intro_session ASC, id ASC", (_eligible_cutoff(),)).fetchall()
    return [(r[0], r[1]) for r in rows if r[0] not in exclude][:limit]


def coverage(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return one concept per topic with no cold recall in COVERAGE_DAYS days.

    The pick is the topic's most at-risk eligible concept: a failed one first,
    then the one untouched the longest (never tested counts from its intro).

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> coverage(conn)
    []
    """
    recent = (datetime.now(timezone.utc) - timedelta(days=COVERAGE_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT c.topic_id, c.id, c.label, c.cold_fails, "
        "       COALESCE(c.last_cold, c.intro_session) AS touched "
        "FROM concepts c WHERE c.state IN ('shaky','solid') AND c.intro_session <= ? "
        "AND c.topic_id NOT IN (SELECT topic_id FROM concepts "
        "                       WHERE last_cold IS NOT NULL AND last_cold >= ?) "
        "ORDER BY c.topic_id, (c.cold_fails > 0 AND c.state='shaky') DESC, touched ASC",
        (_eligible_cutoff(), recent)).fetchall()
    picked: dict[str, tuple[str, str]] = {}
    for topic, cid, label, _, _ in rows:
        picked.setdefault(topic, (cid, label))
    return list(picked.values())


GAP_BUCKETS = ((0, 1, "<1"), (1, 4, "1-3"), (4, 11, "4-10"),
               (11, 31, "11-30"), (31, 10**6, "30+"))


def report(conn: sqlite3.Connection, days: int | None = 28) -> str:
    """Return recall outcomes by gap since the previous recall and by area.

    The gap of a first recall is counted from the day the concept was
    introduced. It is the number the weekly check needs: does memory hold at
    7, 21, 63 days, or is the schedule too long?

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> report(conn)
    'NO-RECALLS'
    """
    rows = conn.execute(
        "SELECT r.concept_id, r.ts, r.result, c.intro_session, t.area "
        "FROM recalls r JOIN concepts c ON c.id = r.concept_id "
        "JOIN topics t ON t.id = c.topic_id ORDER BY r.concept_id, r.ts").fetchall()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    gaps: dict[str, list[str]] = {}
    areas: dict[str, list[str]] = {}
    prev: dict[str, datetime] = {}
    for cid, ts, result, intro, area in rows:
        when = _parse_ts(ts)
        start = prev.get(cid) or _parse_ts(intro + "T00:00:00+00:00")
        prev[cid] = when
        if cutoff and when < cutoff:
            continue
        gap = (when - start).total_seconds() / 86400
        label = next(lab for lo, hi, lab in GAP_BUCKETS if lo <= gap < hi)
        gaps.setdefault(label, []).append(result)
        areas.setdefault(area, []).append(result)
    if not gaps:
        return "NO-RECALLS"

    def rates(results: list[str]) -> str:
        n = len(results)
        return (f"n={n}\tpass={round(100 * results.count('pass') / n)}%"
                f"\thint={round(100 * results.count('hint') / n)}%")

    lines = [f"GAP\t{lab}\t{rates(gaps[lab])}" for _, _, lab in GAP_BUCKETS if lab in gaps]
    lines += [f"AREA\t{a}\t{rates(r)}" for a, r in sorted(areas.items())]
    lines.append(f"UNCOVERED\t{len(coverage(conn))}")
    return "\n".join(lines)


def balance(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Return (backlog, new concepts of the previous session day, daily quota).

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> balance(conn)
    (0, 0, 0)
    """
    backlog = conn.execute(
        "SELECT COUNT(*) FROM concepts WHERE state='shaky' "
        "AND cold_passes=0 AND cold_fails=0").fetchone()[0]
    new_last = conn.execute(
        "SELECT COUNT(*) FROM concepts WHERE intro_session = "
        "(SELECT MAX(intro_session) FROM concepts WHERE intro_session < ?)",
        (today(),)).fetchone()[0]
    return backlog, new_last, new_last + math.ceil(backlog / BACKLOG_DAYS)


def record_misconception(conn: sqlite3.Connection, label: str,
                         concept_id: str | None) -> None:
    """Increment an unresolved misconception's stumbles, or insert it.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> record_misconception(conn, "off-by-one", None)
    >>> record_misconception(conn, "off-by-one", None)
    >>> conn.execute("SELECT stumbles FROM misconceptions "
    ...     "WHERE label='off-by-one'").fetchone()[0]
    2
    """
    ts = now()
    # same label under a different concept is a different stumble, not the same one
    row = conn.execute(
        "SELECT id FROM misconceptions WHERE label=? AND resolved=0 AND concept_id IS ?",
        (label, concept_id)).fetchone()
    if row:
        conn.execute("UPDATE misconceptions SET stumbles=stumbles+1, updated_at=? "
                    "WHERE id=?", (ts, row[0]))
    else:
        conn.execute("INSERT INTO misconceptions (concept_id, label, updated_at) "
                    "VALUES (?,?,?)", (concept_id, label, ts))
    conn.commit()


def resolve_misconception(conn: sqlite3.Connection, label: str) -> None:
    """Mark all rows with this label resolved.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> record_misconception(conn, "off-by-one", None)
    >>> resolve_misconception(conn, "off-by-one")
    >>> conn.execute("SELECT resolved FROM misconceptions "
    ...     "WHERE label='off-by-one'").fetchone()[0]
    1
    """
    conn.execute("UPDATE misconceptions SET resolved=1, updated_at=? WHERE label=?",
                (now(), label))
    conn.commit()


def add_to_deepen(conn: sqlite3.Connection, topic_id: str | None, label: str) -> None:
    """Queue a 'deepen later' item.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> add_to_deepen(conn, "python", "decorators")
    >>> conn.execute("SELECT label FROM to_deepen").fetchone()[0]
    'decorators'
    """
    conn.execute("INSERT INTO to_deepen (topic_id, label, created_at) VALUES (?,?,?)",
                (topic_id, label, now()))
    conn.commit()


def mark_deepen_done(conn: sqlite3.Connection, label: str) -> None:
    """Mark queued 'deepen later' items with this label as covered.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> add_to_deepen(conn, "python", "decorators")
    >>> mark_deepen_done(conn, "decorators")
    >>> conn.execute("SELECT done FROM to_deepen").fetchone()[0]
    1
    """
    conn.execute("UPDATE to_deepen SET done=1 WHERE label=?", (label,))
    conn.commit()


def add_link(conn: sqlite3.Connection, a_id: str, b_id: str, note: str) -> None:
    """Link two concepts as the same underlying idea; direction does not matter.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','python','x','x')")
    >>> for cid in ("t/a", "t/b"):
    ...     _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,"
    ...         "intro_session,last_seen) VALUES (?,'t','l','learning','x','x')", (cid,))
    >>> add_link(conn, "t/b", "t/a", "same idea")
    >>> links_for(conn, "t/a")
    [('t/b', 'same idea')]
    """
    # store the pair sorted so (a,b) and (b,a) collide on the UNIQUE index
    lo, hi = sorted((a_id, b_id))
    conn.execute(
        "INSERT INTO links (a_id, b_id, note, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(a_id, b_id) DO UPDATE SET note=excluded.note",
        (lo, hi, note, now()))
    conn.commit()


def links_for(conn: sqlite3.Connection, concept_id: str) -> list[tuple[str, str]]:
    """Return (other_concept_id, note) for every bridge touching this concept.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> links_for(conn, "nothing/here")
    []
    """
    rows = conn.execute(
        "SELECT CASE WHEN a_id=? THEN b_id ELSE a_id END, note FROM links "
        "WHERE a_id=? OR b_id=? ORDER BY created_at",
        (concept_id, concept_id, concept_id)).fetchall()
    return [(r[0], r[1]) for r in rows]


def topic_mastery(conn: sqlite3.Connection) -> list[tuple[str, str, float]]:
    """Return (area, topic_id, mastery_pct) per topic; shaky counts half.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','python','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/a','t','a','solid','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/b','t','b','shaky','x','x')")
    >>> conn.commit()
    >>> topic_mastery(conn)
    [('python', 't', 75.0)]
    """
    rows = conn.execute(
        "SELECT t.area, t.id, "
        "  SUM(CASE c.state WHEN 'solid' THEN 1.0 "
        "                   WHEN 'shaky' THEN 0.5 ELSE 0 END), COUNT(c.id) "
        "FROM topics t JOIN concepts c ON c.topic_id = t.id "
        "GROUP BY t.id ORDER BY t.area, t.id").fetchall()
    out: list[tuple[str, str, float]] = []
    for area, tid, score, total in rows:
        pct = round((score / total) * 100, 1) if total else 0.0
        out.append((area, tid, pct))
    return out


def set_support_floor(conn: sqlite3.Connection, topic_id: str,
                      level: int | None) -> None:
    """Pin a minimum scaffolding level for a topic (None clears it).

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) "
    ...     "VALUES ('t','python','x','x')")
    >>> set_support_floor(conn, "t", 2)
    >>> conn.execute("SELECT support_floor FROM topics WHERE id='t'").fetchone()[0]
    2
    """
    cur = conn.execute("UPDATE topics SET support_floor=?, updated_at=? WHERE id=?",
                       (level, now(), topic_id))
    if cur.rowcount == 0:
        raise KeyError(f"unknown topic: {topic_id}")
    conn.commit()


def support_level(conn: sqlite3.Connection, topic_id: str) -> tuple[int, str]:
    """Return (scaffolding level 0-3, one-line reason). 3 is maximum support.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> support_level(conn, "unknown.topic")
    (3, 'new topic')
    """
    row = conn.execute(
        "SELECT SUM(CASE state WHEN 'solid' THEN 1 ELSE 0 END), COUNT(*), "
        "       SUM(CASE WHEN state='solid' AND cold_passes>=2 THEN 1 ELSE 0 END) "
        "FROM concepts WHERE topic_id=?", (topic_id,)).fetchone()
    solid, total, deep = (row[0] or 0), (row[1] or 0), (row[2] or 0)
    if total == 0:
        computed, reason = 3, "new topic"
    else:
        pct = solid / total
        if pct < 0.25:
            computed, reason = 3, f"{solid}/{total} solid"
        elif pct < 0.55:
            computed, reason = 2, f"{solid}/{total} solid"
        elif pct < 0.8 or deep == 0:
            computed, reason = 1, f"{solid}/{total} solid, {deep} deeply recalled"
        else:
            computed, reason = 0, f"{solid}/{total} solid, {deep} deeply recalled"
    floor_row = conn.execute("SELECT support_floor FROM topics WHERE id=?",
                             (topic_id,)).fetchone()
    floor = floor_row[0] if floor_row else None
    if floor is not None and floor > computed:
        return floor, f"floor set by user (computed {computed}: {reason})"
    return computed, reason


def brief(conn: sqlite3.Connection, topic_id: str | None = None,
          warm_limit: int = PRACTICE_MAX, quota: int | None = None) -> str:
    """Return everything the tutor should know before teaching, one tag per line.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> brief(conn)
    'EMPTY'
    """
    lines: list[str] = []
    # Review queue first: it decides which topics and bridges are worth showing.
    # Coverage outranks everything, then practice, then flash fills the quota.
    cover = coverage(conn)
    taken = frozenset(cid for cid, _ in cover)
    practice = warmup(conn, warm_limit, exclude=taken)
    taken |= {cid for cid, _ in practice}
    backlog, new_last, daily_quota = balance(conn)
    if quota is not None:
        daily_quota = quota
    flashed = flash(conn, daily_quota - len(cover) - len(practice), exclude=taken)
    queued = [cid for cid, _ in cover + practice + flashed]
    if topic_id:
        topics = [topic_id]
    else:
        # one SUPPORT line per topic in today's queue, not all 30+ topics
        topics = sorted({row[0] for row in conn.execute(
            f"SELECT topic_id FROM concepts WHERE id IN ({','.join('?' * len(queued))})",
            queued)}) if queued else []
        if not queued and not conn.execute("SELECT 1 FROM concepts").fetchone():
            topics = [row[0] for row in conn.execute("SELECT id FROM topics ORDER BY area, id")]
    for tid in topics:
        level, reason = support_level(conn, tid)
        lines.append(f"SUPPORT\t{tid}\t{level}\t{reason}")
    sources = dict(conn.execute(
        f"SELECT id, source FROM concepts WHERE source IS NOT NULL "
        f"AND id IN ({','.join('?' * len(queued))})", queued)) if queued else {}

    def tagged(tag: str, cid: str, label: str) -> str:
        src = sources.get(cid)
        return f"{tag}\t{cid}\t{label}" + (f"\t{src}" if src else "")

    for cid, label in cover:
        lines.append(tagged("COVER", cid, label))
    for cid, label in practice:
        lines.append(tagged("WARMUP", cid, label))
    if backlog or queued or quota is not None:
        lines.append(f"BALANCE\tbacklog={backlog}\tnew_last={new_last}"
                     f"\tquota={daily_quota}\tuncovered={len(cover)}")
    for cid, label in flashed:
        lines.append(tagged("FLASH", cid, label))
    where = " AND (m.concept_id IS NULL OR c.topic_id=?)" if topic_id else ""
    params = (topic_id,) if topic_id else ()
    for label, stumbles in conn.execute(
            "SELECT m.label, m.stumbles FROM misconceptions m "
            "LEFT JOIN concepts c ON c.id = m.concept_id "
            f"WHERE m.resolved=0{where} ORDER BY m.stumbles DESC", params):
        lines.append(f"MISCONCEPTION\t{label}\t{stumbles}")
    # Bridges stay cross-area (scoping them to one topic would hide the link),
    # but only those touching today's queue: all 47 at once buried every signal.
    queued_set = set(queued)
    for a_id, b_id, note in conn.execute(
            "SELECT a_id, b_id, note FROM links ORDER BY created_at"):
        if a_id in queued_set or b_id in queued_set:
            lines.append(f"BRIDGE\t{a_id}\t{b_id}\t{note}")
    deepen_where = " AND topic_id=?" if topic_id else ""
    # Oldest first and capped: unclosed notes accumulate for months (21 open
    # against 7 ever closed, 2026-09-20), so an uncapped list buries every other
    # tag. Oldest first on purpose: a note sitting for two months is either
    # important or dead, and both cases need it in view, not hidden behind
    # fresher ones. The full list stays in growth-map.md.
    for tid, label in conn.execute(
            "SELECT topic_id, label FROM to_deepen WHERE done=0"
            f"{deepen_where} ORDER BY created_at LIMIT {DEEPEN_SHOWN}", params):
        lines.append(f"DEEPEN\t{tid}\t{label}")
    for tid, solid, bridges in challenge_candidates(conn):
        if topic_id is None or tid == topic_id:
            lines.append(f"CHALLENGE\t{tid}\t{solid}\t{bridges}")
    arc_where = " AND topic_id=?" if topic_id else ""
    arc_params = (topic_id,) if topic_id else ()
    for arc_id, tid, label in conn.execute(
            "SELECT id, topic_id, COALESCE(thesis, no_thesis_reason) FROM arcs "
            f"WHERE state='open'{arc_where} ORDER BY opened_at", arc_params):
        lines.append(f"ARC\t{arc_id}\t{tid}\t{label}")
    # a thesis is a cold-recall probe only once it has had time to fade: fresh
    # off a close it is still in working memory, so restating it tests nothing.
    thesis_cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    recall_cutoff = (datetime.now(timezone.utc)
                     - timedelta(days=COLD_DECAY_DAYS)).isoformat()
    for arc_id, thesis in conn.execute(
            "SELECT id, thesis FROM arcs WHERE state='closed' AND thesis IS NOT NULL "
            "AND closed_at<=? AND (last_recalled IS NULL OR last_recalled<=?)"
            f"{arc_where} ORDER BY closed_at",
            (thesis_cutoff, recall_cutoff) + arc_params):
        lines.append(f"THESIS\t{arc_id}\t{thesis}")
    return "\n".join(lines) if lines else "EMPTY"


def challenge_candidates(conn: sqlite3.Connection, min_days: int = COLD_DECAY_DAYS,
                         min_solid: int = 2) -> list[tuple[str, int, int]]:
    """Return (topic_id, solid_count, bridge_count) for topics ripe for a hard problem.

    A topic is ripe when enough concepts are solid and none of them was cold
    recalled recently, so the knowledge has survived storage rather than being
    fresh in working memory. bridge_count only counts links that cross into a
    *different* topic, once per link: an intra-topic link bridges no areas.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> challenge_candidates(conn)
    []
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_days)).isoformat()
    rows = conn.execute(
        "SELECT c.topic_id, COUNT(*), "
        "  (SELECT COUNT(*) FROM links l "
        "   JOIN concepts ca ON ca.id = l.a_id "
        "   JOIN concepts cb ON cb.id = l.b_id "
        "   WHERE ca.topic_id <> cb.topic_id "
        "     AND (ca.topic_id = c.topic_id OR cb.topic_id = c.topic_id)) "
        "FROM concepts c WHERE c.state='solid' "
        "GROUP BY c.topic_id "
        "HAVING COUNT(*) >= ? AND MAX(COALESCE(c.last_cold, c.last_seen)) < ? "
        "ORDER BY 3 DESC, 2 DESC, 1 ASC", (min_solid, cutoff)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def render_bars(conn: sqlite3.Connection, width: int = 20) -> str:
    """Render never-tested/recalled/solid counts per topic, grouped by area.

    A single mastery percentage reads as 0% right after a session where every
    concept was recalled cold for the first time, since `solid` needs more
    than one spaced pass. Three counted buckets, summing to the topic total,
    keep that first pass visible instead of hiding it behind "0%".

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','python','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/abc','t','a','solid','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/def','t','b','shaky','x','x')")
    >>> conn.commit()
    >>> out = render_bars(conn)
    >>> "Python" in out and "solid" in out and "1/2" in out
    True
    """
    rows = conn.execute(
        "SELECT t.area, t.id, "
        "  SUM(CASE WHEN c.cold_passes = 0 AND c.cold_fails = 0 THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN (c.cold_passes > 0 OR c.cold_fails > 0) "
        "            AND c.state != 'solid' THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN c.state = 'solid' THEN 1 ELSE 0 END), "
        "  COUNT(c.id) "
        "FROM topics t JOIN concepts c ON c.topic_id = t.id "
        "GROUP BY t.id ORDER BY t.area, t.id").fetchall()
    lines: list[str] = []
    current_area = None
    for area, tid, never_tested, recalled, solid, total in rows:
        if area != current_area:
            lines.append(AREA_LABELS.get(area, area.capitalize()))
            current_area = area
        short = tid.split(".", 1)[-1]  # drop the area prefix already in the header
        lines.append(f"  {short}")
        for label, count in (
            ("never tested", never_tested),
            ("recalled", recalled),
            ("solid", solid),
        ):
            filled = round(count / total * width) if total else 0
            bar = "█" * filled + "░" * (width - filled)
            lines.append(f"    {label:<13} {bar}  {count}/{total}")
    return "\n".join(lines) if lines else "(no concepts tracked yet)"


def arc_open(conn: sqlite3.Connection, topic_id: str, thesis: str | None,
            no_thesis_reason: str | None, direction: str | None) -> int:
    """Open a governing-idea arc for a topic; return the new arc id.

    A thesis or an explicit reason for having none is mandatory: an arc with
    neither is exactly the unexamined drift this table exists to prevent.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','a','x','x')")
    >>> arc_open(conn, "t", "the idea", None, None) > 0
    True
    """
    if not thesis and not no_thesis_reason:
        raise ValueError("arc needs a thesis or a stated reason for having none")
    exists = conn.execute("SELECT 1 FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not exists:
        raise KeyError(f"unknown topic: {topic_id}")
    cur = conn.execute(
        "INSERT INTO arcs (topic_id, thesis, no_thesis_reason, direction, "
        "opened_at) VALUES (?,?,?,?,?)",
        (topic_id, thesis, no_thesis_reason, direction, now()))
    conn.commit()
    return cur.lastrowid


def arc_close(conn: sqlite3.Connection, arc_id: int) -> None:
    """Close an open arc.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics (id,area,created_at,updated_at) VALUES ('t','a','x','x')")
    >>> arc_id = arc_open(conn, "t", "the idea", None, None)
    >>> arc_close(conn, arc_id)
    >>> arc_current(conn, None)
    []
    """
    conn.execute("UPDATE arcs SET state='closed', closed_at=? WHERE id=?",
                (now(), arc_id))
    conn.commit()


def arc_current(conn: sqlite3.Connection, topic_id: str | None) -> list[tuple]:
    """Return open arcs, optionally scoped to one topic.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> arc_current(conn, None)
    []
    """
    where = " AND topic_id=?" if topic_id else ""
    params = (topic_id,) if topic_id else ()
    rows = conn.execute(
        "SELECT id, topic_id, thesis, no_thesis_reason, direction, opened_at "
        f"FROM arcs WHERE state='open'{where} ORDER BY opened_at", params).fetchall()
    return [tuple(r) for r in rows]


def _conn_dir(conn: sqlite3.Connection) -> Path:
    """Return the directory of the connection's main DB file (or the default).

    >>> _conn_dir(sqlite3.connect(":memory:")) == DEFAULT_DB.parent
    True
    """
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            return Path(file).parent
    return DEFAULT_DB.parent


def render_map(conn: sqlite3.Connection, path: str | None) -> Path:
    """Write growth-map.md next to the DB (or at `path`); return its Path.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> out = render_map(conn, "/tmp/test-growth-map.md")  # doctest: +SKIP
    >>> out.exists()  # doctest: +SKIP
    True
    """
    target = Path(path) if path else _conn_dir(conn) / "growth-map.md"
    deepen = conn.execute(
        "SELECT label FROM to_deepen WHERE done=0 ORDER BY created_at").fetchall()
    mis = conn.execute(
        "SELECT label, stumbles FROM misconceptions WHERE resolved=0 "
        "ORDER BY stumbles DESC").fetchall()
    bridges = conn.execute(
        "SELECT a_id, b_id, note FROM links ORDER BY created_at").fetchall()
    parts = ["# learn-code growth map", "", "```", render_bars(conn), "```", ""]
    if deepen:
        parts += ["## To deepen", *[f"- {r[0]}" for r in deepen], ""]
    if mis:
        parts += ["## Open misconceptions",
                  *[f"- {r[0]} (x{r[1]})" for r in mis], ""]
    if bridges:
        parts += ["## Bridges",
                  *[f"- {a} <-> {b}: {note}" for a, b, note in bridges], ""]
    # explicit utf-8: the bars are block glyphs, the locale encoding may not cover them
    target.write_text("\n".join(parts), encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    """CLI entry point. Returns a process exit code.

    >>> main(["--db", "/tmp/lc/state.db", "init"])  # doctest: +SKIP
    0
    """
    parser = argparse.ArgumentParser(prog="learn_code_db")
    parser.add_argument("--db", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p_topic = sub.add_parser("record-topic")
    p_topic.add_argument("topic_id"); p_topic.add_argument("area")
    p_concept = sub.add_parser("record-concept")
    p_concept.add_argument("concept_id"); p_concept.add_argument("topic_id")
    p_concept.add_argument("label")
    p_concept.add_argument("--arc", type=int, default=None)
    p_concept.add_argument("--source", default=None)
    p_cold = sub.add_parser("cold-result")
    p_cold.add_argument("concept_id")
    p_cold.add_argument("result", choices=["pass", "fail", "hint"])
    p_report = sub.add_parser("report")
    p_report.add_argument("--days", type=int, default=28)
    p_end = sub.add_parser("end-session")
    p_end.add_argument("--topics", default="")
    p_warm = sub.add_parser("warmup")
    p_warm.add_argument("--limit", type=int, default=2)
    p_mis = sub.add_parser("misconception")
    p_mis.add_argument("label"); p_mis.add_argument("--concept", default=None)
    p_res = sub.add_parser("resolve-misconception")
    p_res.add_argument("label")
    p_deep = sub.add_parser("to-deepen")
    p_deep.add_argument("topic_id"); p_deep.add_argument("label")
    p_deep_done = sub.add_parser("deepen-done")
    p_deep_done.add_argument("label")
    p_link = sub.add_parser("link")
    p_link.add_argument("a_id"); p_link.add_argument("b_id")
    p_link.add_argument("note")
    p_links = sub.add_parser("links")
    p_links.add_argument("concept_id")
    p_sup = sub.add_parser("support-level")
    p_sup.add_argument("topic_id")
    p_floor = sub.add_parser("support-floor")
    p_floor.add_argument("topic_id")
    p_floor.add_argument("level", choices=["0", "1", "2", "3", "clear"])
    p_brief = sub.add_parser("brief")
    p_brief.add_argument("--topic", default=None)
    p_brief.add_argument("--limit", type=int, default=PRACTICE_MAX)
    p_brief.add_argument("--quota", type=int, default=None)
    p_chal = sub.add_parser("challenge")
    p_chal.add_argument("--days", type=int, default=10)
    p_chal.add_argument("--min-solid", type=int, default=2)
    sub.add_parser("bars")
    sub.add_parser("render-map")
    p_arc_open = sub.add_parser("arc-open")
    p_arc_open.add_argument("topic_id")
    p_arc_thesis = p_arc_open.add_mutually_exclusive_group(required=True)
    p_arc_thesis.add_argument("--thesis", default=None)
    p_arc_thesis.add_argument("--no-thesis", default=None)
    p_arc_open.add_argument("--direction", default=None)
    p_arc_close = sub.add_parser("arc-close")
    p_arc_close.add_argument("arc_id", type=int)
    p_arc_cur = sub.add_parser("arc-current")
    p_arc_cur.add_argument("--topic", default=None)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        conn = connect(args.db)
    except (sqlite3.Error, OSError) as exc:
        print(f"learn_code_db: {exc}", file=sys.stderr)
        return 1
    try:
        if args.cmd == "init":
            init_db(conn); return 0
        if args.cmd == "record-topic":
            record_topic(conn, args.topic_id, args.area); return 0
        if args.cmd == "record-concept":
            record_concept(conn, args.concept_id, args.topic_id, args.label, args.source)
            if args.arc is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO arc_concepts (arc_id, concept_id) "
                    "VALUES (?,?)", (args.arc, args.concept_id))
                conn.commit()
            return 0
        if args.cmd == "cold-result":
            print(cold_result(conn, args.concept_id, args.result == "pass",
                              hint=args.result == "hint")); return 0
        if args.cmd == "report":
            print(report(conn, args.days)); return 0
        if args.cmd == "end-session":
            end_session(conn, args.topics); return 0
        if args.cmd == "warmup":
            for cid, label in warmup(conn, args.limit):
                print(f"{cid}\t{label}")
            return 0
        if args.cmd == "misconception":
            record_misconception(conn, args.label, args.concept); return 0
        if args.cmd == "resolve-misconception":
            resolve_misconception(conn, args.label); return 0
        if args.cmd == "to-deepen":
            add_to_deepen(conn, args.topic_id, args.label); return 0
        if args.cmd == "deepen-done":
            mark_deepen_done(conn, args.label); return 0
        if args.cmd == "link":
            add_link(conn, args.a_id, args.b_id, args.note)
            return 0
        if args.cmd == "links":
            for other, note in links_for(conn, args.concept_id):
                print(f"{other}\t{note}")
            return 0
        if args.cmd == "support-level":
            level, reason = support_level(conn, args.topic_id)
            print(f"{level}\t{reason}")
            return 0
        if args.cmd == "support-floor":
            set_support_floor(conn, args.topic_id,
                              None if args.level == "clear" else int(args.level))
            return 0
        if args.cmd == "brief":
            print(brief(conn, args.topic, args.limit, args.quota))
            return 0
        if args.cmd == "challenge":
            for tid, solid, bridges in challenge_candidates(
                    conn, args.days, args.min_solid):
                print(f"{tid}\t{solid}\t{bridges}")
            return 0
        if args.cmd == "bars":
            print(render_bars(conn)); return 0
        if args.cmd == "render-map":
            print(render_map(conn, None)); return 0
        if args.cmd == "arc-open":
            print(arc_open(conn, args.topic_id, args.thesis,
                           args.no_thesis, args.direction))
            return 0
        if args.cmd == "arc-close":
            arc_close(conn, args.arc_id); return 0
        if args.cmd == "arc-current":
            for row in arc_current(conn, args.topic):
                print("\t".join("" if v is None else str(v) for v in row))
            return 0
    except (sqlite3.Error, KeyError, OSError) as exc:
        # the tutor drives this over Bash: a traceback would leak local paths into the transcript
        print(f"learn_code_db: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
