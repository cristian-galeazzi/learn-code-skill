"""learn-code tracking CLI: SQLite-backed concept mastery for /learn-code."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".claude" / "learn-code" / "state.db"

# acronym areas that .capitalize() would mangle into Sql, Ml, Dl
AREA_LABELS = {"sql": "SQL", "ml": "ML", "dl": "DL", "html": "HTML", "css": "CSS"}

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
    last_seen TEXT NOT NULL, last_cold TEXT);
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
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables idempotently.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    0
    """
    conn.executescript(SCHEMA)
    conn.commit()


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
                   topic_id: str, label: str) -> None:
    """Insert a concept as 'learning' if new, else touch last_seen.

    >>> conn = sqlite3.connect(":memory:")  # doctest: +SKIP
    >>> init_db(conn)  # doctest: +SKIP
    >>> record_concept(conn, "c1", "t1", "label")  # doctest: +SKIP
    """
    ts = now()
    exists = conn.execute("SELECT 1 FROM concepts WHERE id=?",
                          (concept_id,)).fetchone()
    if exists:
        conn.execute("UPDATE concepts SET last_seen=? WHERE id=?", (ts, concept_id))
    else:
        conn.execute(
            "INSERT INTO concepts (id, topic_id, label, state, intro_session, "
            "last_seen) VALUES (?,?,?,'learning',?,?)",
            (concept_id, topic_id, label, today(), ts))
    conn.commit()


def cold_result(conn: sqlite3.Connection, concept_id: str, passed: bool) -> str:
    """Apply the mastery state machine for a cold-recall result; return new state.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics VALUES ('t','a','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts "
    ...     "(id, topic_id, label, state, intro_session, last_seen) "
    ...     "VALUES ('t/c','t','c','shaky','x','x')")
    >>> cold_result(conn, "t/c", True)
    'solid'
    >>> cold_result(conn, "t/c", False)
    'shaky'
    """
    row = conn.execute("SELECT state FROM concepts WHERE id=?",
                       (concept_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown concept: {concept_id}")
    state = row[0]
    if passed and state == "shaky":
        state = "solid"
    elif not passed and state == "solid":
        state = "shaky"
    # learning stays learning; shaky+fail stays shaky
    counter = "cold_passes" if passed else "cold_fails"
    ts = now()
    conn.execute(
        f"UPDATE concepts SET state=?, {counter}={counter}+1, "
        "last_cold=?, last_seen=? WHERE id=?",
        (state, ts, ts, concept_id))
    conn.commit()
    return state


def end_session(conn: sqlite3.Connection, topics_seen: str) -> None:
    """Flip this session's 'learning' concepts to 'shaky'; record the session.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics VALUES ('t','a','x','x')")
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


def warmup(conn: sqlite3.Connection, limit: int = 2) -> list[tuple[str, str]]:
    """Return the most at-risk shaky concepts as (concept_id, label) pairs.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics VALUES ('t','python','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts "
    ...     "(id, topic_id, label, state, intro_session, last_seen, cold_fails) "
    ...     "VALUES ('t/c','t','c','shaky','x','x',1)")
    >>> warmup(conn, limit=1)
    [('t/c', 'c')]
    """
    rows = conn.execute(
        "SELECT id, label FROM concepts WHERE state='shaky' "
        "ORDER BY cold_fails DESC, (last_cold IS NULL) DESC, last_cold ASC "
        "LIMIT ?", (limit,)).fetchall()
    return [(r[0], r[1]) for r in rows]


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


def topic_mastery(conn: sqlite3.Connection) -> list[tuple[str, str, float]]:
    """Return (area, topic_id, mastery_pct) per topic; shaky counts half.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics VALUES ('t','python','x','x')")
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


def render_bars(conn: sqlite3.Connection, width: int = 20) -> str:
    """Render an ASCII mastery bar chart grouped by area then topic.

    >>> conn = sqlite3.connect(":memory:")
    >>> init_db(conn)
    >>> _ = conn.execute("INSERT INTO topics VALUES ('t','python','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/abc','t','a','solid','x','x')")
    >>> _ = conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,last_seen) VALUES ('t/def','t','b','shaky','x','x')")
    >>> conn.commit()
    >>> "75%" in render_bars(conn) and "Python" in render_bars(conn)
    True
    """
    lines: list[str] = []
    current_area = None
    for area, tid, pct in topic_mastery(conn):
        if area != current_area:
            lines.append(AREA_LABELS.get(area, area.capitalize()))
            current_area = area
        filled = round(pct / 100 * width)
        bar = "█" * filled + "░" * (width - filled)
        short = tid.split(".", 1)[-1]  # drop the area prefix already in the header
        lines.append(f"  {short:<14} {bar} {pct:.0f}%")
    return "\n".join(lines) if lines else "(no concepts tracked yet)"


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
    parts = ["# learn-code growth map", "", "```", render_bars(conn), "```", ""]
    if deepen:
        parts += ["## To deepen", *[f"- {r[0]}" for r in deepen], ""]
    if mis:
        parts += ["## Open misconceptions",
                  *[f"- {r[0]} (x{r[1]})" for r in mis], ""]
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
    p_cold = sub.add_parser("cold-result")
    p_cold.add_argument("concept_id")
    p_cold.add_argument("result", choices=["pass", "fail"])
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
    sub.add_parser("bars")
    sub.add_parser("render-map")
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
            record_concept(conn, args.concept_id, args.topic_id, args.label); return 0
        if args.cmd == "cold-result":
            print(cold_result(conn, args.concept_id, args.result == "pass")); return 0
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
        if args.cmd == "bars":
            print(render_bars(conn)); return 0
        if args.cmd == "render-map":
            print(render_map(conn, None)); return 0
    except (sqlite3.Error, KeyError, OSError) as exc:
        # the tutor drives this over Bash: a traceback would leak local paths into the transcript
        print(f"learn_code_db: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
