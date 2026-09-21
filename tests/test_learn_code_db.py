import re
import sqlite3
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent.parent / "skills" / "learn-code" / "learn_code_db.py"
SKILL_MD = SPEC.parent / "SKILL.md"
REFERENCE_DIR = SPEC.parent / "reference"
_spec = importlib.util.spec_from_file_location("learn_code_db", SPEC)
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)


def _tutor_docs() -> str:
    """Every document the tutor can read: SKILL.md plus its reference files.

    The 2026-09-20 restructure split the CLI documentation out of SKILL.md into
    reference/tracking.md, so a check that reads only SKILL.md now measures
    where a command is written down rather than whether it is.

    >>> "learn-code" in _tutor_docs()
    True
    """
    parts = [SKILL_MD.read_text()]
    parts.extend(p.read_text() for p in sorted(REFERENCE_DIR.glob("*.md")))
    return "\n".join(parts)


def _cli_subcommands() -> set[str]:
    """Subcommand names argparse actually exposes, read from --help.

    >>> "brief" in _cli_subcommands()
    True
    """
    out = subprocess.run([sys.executable, str(SPEC), "--help"],
                         capture_output=True, text=True, check=True).stdout
    return set(re.search(r"\{([a-z0-9,\-]+)\}", out).group(1).split(","))


def test_init_creates_tables_idempotently(tmp_path):
    db = str(tmp_path / "state.db")
    assert lc.main(["--db", db, "init"]) == 0
    assert lc.main(["--db", db, "init"]) == 0  # idempotent
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"topics", "concepts", "misconceptions", "to_deepen", "sessions"} <= names


def test_record_concept_starts_learning_and_is_idempotent(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.dictionaries", "python"])
    lc.main(["--db", db, "record-concept",
             "python.dictionaries/setdefault", "python.dictionaries", "setdefault"])
    lc.main(["--db", db, "record-concept",
             "python.dictionaries/setdefault", "python.dictionaries", "setdefault"])
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT state, COUNT(*) FROM concepts "
                        "WHERE id='python.dictionaries/setdefault'").fetchone()
    assert rows == ("learning", 1)


def test_state_machine_promotion_and_regression(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "t", "python"])
    lc.main(["--db", db, "record-concept", "t/c", "t", "c"])

    conn = sqlite3.connect(db)
    # learning + pass -> still learning (not eligible until a later session)
    assert lc.cold_result(conn, "t/c", True) == "learning"

    # end of session flips learning -> shaky
    lc.end_session(conn, "t")
    assert conn.execute("SELECT state FROM concepts WHERE id='t/c'").fetchone()[0] == "shaky"

    # shaky + pass -> solid (this is structurally a later session)
    assert lc.cold_result(conn, "t/c", True) == "solid"
    # solid + fail -> shaky (regression)
    assert lc.cold_result(conn, "t/c", False) == "shaky"
    # shaky + fail -> stays shaky (one of the four EXACT transitions)
    assert lc.cold_result(conn, "t/c", False) == "shaky"
    # counters: 2 passes, 2 fails
    p, f = conn.execute("SELECT cold_passes, cold_fails FROM concepts "
                        "WHERE id='t/c'").fetchone()
    assert (p, f) == (2, 2)


def test_cold_result_unknown_concept_raises(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    conn = sqlite3.connect(db)
    try:
        lc.cold_result(conn, "nope", True)
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_warmup_and_misconception(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "t", "python"])
    for c in ("a", "b"):
        lc.main(["--db", db, "record-concept", f"t/{c}", "t", c])
    conn = sqlite3.connect(db)
    lc.end_session(conn, "t")                 # both -> shaky
    lc.cold_result(conn, "t/a", False)        # a fails once -> more at risk
    top = lc.warmup(conn, limit=1)
    assert top == [("t/a", "a")]

    lc.record_misconception(conn, "mutate-vs-rebind", "t/a")
    lc.record_misconception(conn, "mutate-vs-rebind", "t/a")
    s = conn.execute("SELECT stumbles FROM misconceptions "
                     "WHERE label='mutate-vs-rebind' AND resolved=0").fetchone()[0]
    assert s == 2


def test_topic_mastery_and_map(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.dictionaries", "python"])
    for c in ("a", "b", "c", "d"):
        lc.main(["--db", db, "record-concept",
                 f"python.dictionaries/{c}", "python.dictionaries", c])
    conn = sqlite3.connect(db)
    lc.end_session(conn, "python.dictionaries")          # 4 shaky
    lc.cold_result(conn, "python.dictionaries/a", True)  # a -> solid
    lc.cold_result(conn, "python.dictionaries/b", True)  # b -> solid
    # 2 solid + 2 shaky over 4 = (2 + 1) / 4 = 75%
    mastery = lc.topic_mastery(conn)
    assert mastery == [("python", "python.dictionaries", 75.0)]

    # bars show the short label under the area header, not the redundant full id
    bars = lc.render_bars(conn)
    # a, b cold-tested and passed -> solid; c, d never cold-tested -> never tested
    assert "Python" in bars and "dictionaries" in bars and "solid" in bars
    assert "2/4" in bars
    assert "python.dictionaries" not in bars

    out = lc.render_map(conn, str(tmp_path / "growth-map.md"))
    assert out.exists() and "dictionaries" in out.read_text()


def test_re_record_does_not_downgrade_state(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "t", "python"])
    lc.main(["--db", db, "record-concept", "t/c", "t", "c"])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE concepts SET state='solid' WHERE id='t/c'")
    conn.commit()
    # re-teaching an advanced concept only touches last_seen, never the state
    lc.record_concept(conn, "t/c", "t", "c")
    assert conn.execute("SELECT state FROM concepts WHERE id='t/c'").fetchone()[0] == "solid"


def test_fresh_db_needs_no_explicit_init(tmp_path):
    db = str(tmp_path / "state.db")
    # no `init` call: the first real subcommand must work on a brand new file
    assert lc.main(["--db", db, "record-topic", "t", "python"]) == 0
    assert lc.main(["--db", db, "record-concept", "t/c", "t", "c"]) == 0


def test_cli_error_paths_exit_nonzero_without_traceback(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    # unknown concept -> KeyError, and a concept whose topic does not exist -> FK failure
    assert lc.main(["--db", db, "cold-result", "nope", "pass"]) == 1
    assert lc.main(["--db", db, "record-concept", "t/c", "missing-topic", "c"]) == 1
    assert "learn_code_db:" in capsys.readouterr().err


def test_deepen_queue_can_be_marked_done(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "record-topic", "python", "python"])
    lc.main(["--db", db, "to-deepen", "python", "decorators"])
    assert lc.main(["--db", db, "deepen-done", "decorators"]) == 0
    out = lc.render_map(sqlite3.connect(db), str(tmp_path / "growth-map.md"))
    assert "decorators" not in out.read_text(encoding="utf-8")


def test_misconceptions_do_not_merge_across_concepts(tmp_path):
    db = str(tmp_path / "state.db")
    conn = lc.connect(db)
    lc.record_topic(conn, "t", "python")
    for c in ("a", "b"):
        lc.record_concept(conn, f"t/{c}", "t", c)
    lc.record_misconception(conn, "off-by-one", "t/a")
    lc.record_misconception(conn, "off-by-one", "t/b")
    rows = conn.execute("SELECT concept_id, stumbles FROM misconceptions "
                        "WHERE label='off-by-one' ORDER BY concept_id").fetchall()
    assert rows == [("t/a", 1), ("t/b", 1)]


def test_acronym_areas_are_not_mangled(tmp_path):
    conn = lc.connect(str(tmp_path / "state.db"))
    lc.record_topic(conn, "sql.joins", "sql")
    lc.record_concept(conn, "sql.joins/inner", "sql.joins", "inner join")
    assert "SQL" in lc.render_bars(conn)


def test_render_map_via_cli_writes_next_to_the_db(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    assert lc.main(["--db", db, "render-map"]) == 0
    assert (tmp_path / "growth-map.md").exists()


def test_link_is_bidirectional_and_deduplicated(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "sql.aggregation", "sql"])
    lc.main(["--db", db, "record-topic", "python.pandas", "python"])
    lc.main(["--db", db, "record-concept", "sql.aggregation/group-by",
             "sql.aggregation", "GROUP BY"])
    lc.main(["--db", db, "record-concept", "python.pandas/groupby",
             "python.pandas", "df.groupby"])
    assert lc.main(["--db", db, "link", "sql.aggregation/group-by",
                    "python.pandas/groupby", "same split-apply-combine idea"]) == 0
    # reversed order is the same edge, not a second one
    assert lc.main(["--db", db, "link", "python.pandas/groupby",
                    "sql.aggregation/group-by", "same idea"]) == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1
    lc.init_db(conn)
    assert lc.links_for(conn, "python.pandas/groupby") == [
        ("sql.aggregation/group-by", "same idea")]
    assert lc.links_for(conn, "sql.aggregation/group-by") == [
        ("python.pandas/groupby", "same idea")]


def _seed_topic(db, topic_id="t", area="python", n_solid=0, n_shaky=0, passes=0):
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", topic_id, area])
    conn = sqlite3.connect(db)
    for i in range(n_solid):
        conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,"
                     "last_seen,cold_passes) VALUES (?,?,?,'solid','x','x',?)",
                     (f"{topic_id}/s{i}", topic_id, f"s{i}", passes))
    for i in range(n_shaky):
        conn.execute("INSERT INTO concepts (id,topic_id,label,state,intro_session,"
                     "last_seen) VALUES (?,?,?,'shaky','x','x')",
                     (f"{topic_id}/k{i}", topic_id, f"k{i}"))
    conn.commit()
    conn.close()


def test_support_level_drops_as_mastery_holds(tmp_path):
    db = str(tmp_path / "state.db")
    _seed_topic(db, n_solid=0, n_shaky=4)
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    assert lc.support_level(conn, "t")[0] == 3
    conn.close()

    db2 = str(tmp_path / "state2.db")
    _seed_topic(db2, n_solid=9, n_shaky=1, passes=2)
    conn2 = sqlite3.connect(db2)
    lc.init_db(conn2)
    assert lc.support_level(conn2, "t")[0] == 0


def test_support_floor_is_never_undercut(tmp_path):
    db = str(tmp_path / "state.db")
    _seed_topic(db, n_solid=9, n_shaky=1, passes=2)
    assert lc.main(["--db", db, "support-floor", "t", "2"]) == 0
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    assert lc.support_level(conn, "t")[0] == 2  # floor wins over the computed 0
    conn.close()
    assert lc.main(["--db", db, "support-floor", "t", "clear"]) == 0
    conn = sqlite3.connect(db)
    assert lc.support_level(conn, "t")[0] == 0


def test_support_floor_on_unrecorded_topic_raises(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    conn = sqlite3.connect(db)
    try:
        lc.set_support_floor(conn, "never.recorded", 2)
        assert False, "expected KeyError"
    except KeyError:
        pass
    # CLI path maps it to a clean exit 1, no traceback
    assert lc.main(["--db", db, "support-floor", "never.recorded", "2"]) == 1


def test_unknown_topic_defaults_to_full_support(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    assert lc.support_level(conn, "never.seen")[0] == 3


def test_brief_reports_every_channel(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.dicts", "python"])
    lc.main(["--db", db, "record-topic", "sql.joins", "sql"])
    lc.main(["--db", db, "record-concept", "python.dicts/setdefault",
             "python.dicts", "setdefault"])
    lc.main(["--db", db, "record-concept", "sql.joins/hash-join",
             "sql.joins", "hash join"])
    lc.main(["--db", db, "end-session", "--topics", "python.dicts"])
    lc.main(["--db", db, "misconception", "dict copy is deep",
             "--concept", "python.dicts/setdefault"])
    lc.main(["--db", db, "link", "python.dicts/setdefault",
             "sql.joins/hash-join", "both are hash lookups"])
    lc.main(["--db", db, "to-deepen", "python.dicts", "comprehensions"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    out = lc.brief(conn, "python.dicts")
    lines = out.split("\n")
    assert "SUPPORT\tpython.dicts\t3\t0/1 solid" in lines
    assert "WARMUP\tpython.dicts/setdefault\tsetdefault" in out
    assert "MISCONCEPTION\tdict copy is deep\t1" in out
    assert "BRIDGE\tpython.dicts/setdefault\tsql.joins/hash-join\t" in out
    assert "DEEPEN\tpython.dicts\tcomprehensions" in lines


def test_brief_on_empty_db_is_a_single_marker(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    assert lc.brief(conn) == "EMPTY"


def test_brief_includes_topic_with_no_concepts(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.generators", "python"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    out = lc.brief(conn)
    assert "SUPPORT\tpython.generators\t3\tnew topic" in out.split("\n")


def test_brief_topic_scoped_includes_conceptless_misconception(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.dicts", "python"])
    lc.main(["--db", db, "misconception", "general confusion"])  # no --concept
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    out = lc.brief(conn, "python.dicts")
    assert "MISCONCEPTION\tgeneral confusion\t1" in out


def test_brief_topic_filter_excludes_other_topics_deepen(tmp_path):
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "topic.a", "python"])
    lc.main(["--db", db, "record-topic", "topic.b", "python"])
    lc.main(["--db", db, "to-deepen", "topic.b", "b concept"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    out = lc.brief(conn, "topic.a")
    assert "DEEPEN" not in out


def test_challenge_needs_solid_concepts_that_are_not_fresh(tmp_path):
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "stale", "python"])
    lc.main(["--db", db, "record-topic", "fresh", "python"])
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db)
    for tid, ts in (("stale", old), ("fresh", recent)):
        for i in range(2):
            conn.execute(
                "INSERT INTO concepts (id,topic_id,label,state,intro_session,"
                "last_seen,last_cold,cold_passes) "
                "VALUES (?,?,?,'solid','x',?,?,1)",
                (f"{tid}/c{i}", tid, f"c{i}", ts, ts))
    conn.commit()
    lc.init_db(conn)
    got = lc.challenge_candidates(conn, min_days=10)
    assert [row[0] for row in got] == ["stale"]


def test_challenge_ranks_bridged_topics_first(tmp_path):
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(db)
    # three topics: "aaa" sorts first but has no bridges, "bridged" is linked to "other"
    for tid in ("aaa", "bridged", "other"):
        conn.execute("INSERT INTO topics (id,area,created_at,updated_at) "
                     "VALUES (?,'python','x','x')", (tid,))
        for i in range(2):
            conn.execute(
                "INSERT INTO concepts (id,topic_id,label,state,intro_session,"
                "last_seen,last_cold,cold_passes) "
                "VALUES (?,?,?,'solid','x',?,?,1)",
                (f"{tid}/c{i}", tid, f"c{i}", old, old))
    conn.commit()
    lc.init_db(conn)
    lc.add_link(conn, "bridged/c0", "other/c0", "related")
    lc.add_link(conn, "bridged/c1", "other/c1", "also related")
    got = lc.challenge_candidates(conn, min_days=10)
    assert [row[0] for row in got] == ["bridged", "other", "aaa"]
    assert dict((r[0], r[2]) for r in got) == {"bridged": 2, "other": 2, "aaa": 0}


def _solid_topic(conn: sqlite3.Connection, tid: str, old: str) -> None:
    conn.execute("INSERT INTO topics (id,area,created_at,updated_at) "
                 "VALUES (?,'python','x','x')", (tid,))
    for i in range(2):
        conn.execute(
            "INSERT INTO concepts (id,topic_id,label,state,intro_session,"
            "last_seen,last_cold,cold_passes) "
            "VALUES (?,?,?,'solid','x',?,?,1)",
            (f"{tid}/c{i}", tid, f"c{i}", old, old))
    conn.commit()


def test_challenge_intra_topic_link_is_not_a_bridge(tmp_path):
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(db)
    for tid in ("solo", "bridged", "other"):
        _solid_topic(conn, tid, old)
    lc.init_db(conn)
    lc.add_link(conn, "solo/c0", "solo/c1", "same topic, not a bridge")
    lc.add_link(conn, "bridged/c0", "other/c0", "cross-topic bridge")
    got = dict((r[0], r[2]) for r in lc.challenge_candidates(conn, min_days=10))
    assert got["solo"] == 0
    assert got["bridged"] == 1
    ranked = [row[0] for row in lc.challenge_candidates(conn, min_days=10)]
    assert ranked.index("bridged") < ranked.index("solo")


def test_challenge_single_cross_topic_bridge_counts_once(tmp_path):
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(db)
    for tid in ("bridged", "other"):
        _solid_topic(conn, tid, old)
    lc.init_db(conn)
    lc.add_link(conn, "bridged/c0", "other/c0", "cross-topic bridge")
    got = dict((r[0], r[2]) for r in lc.challenge_candidates(conn, min_days=10))
    assert got["bridged"] == 1
    assert got["other"] == 1


def test_skill_doc_references_only_real_subcommands():
    # A doc telling the tutor to run a command the CLI does not expose is a
    # runtime failure the tutor cannot recover from, so it is a test, not a review nit.
    text = _tutor_docs()
    spans = re.findall(r"`([^`]+)`", text)
    referenced = {s.strip().split()[0] for s in spans if s.strip()}
    hyphenated = {t for t in referenced
                  if re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)+", t)}
    assert hyphenated <= _cli_subcommands(), \
        f"docs reference non-existent subcommands: {hyphenated - _cli_subcommands()}"


def test_every_subcommand_is_documented_in_skill_doc():
    # The reverse drift: a command ships but the tutor never learns it exists.
    text = _tutor_docs()
    undocumented = {c for c in _cli_subcommands()
                    if c != "init" and f"`{c}" not in text}
    assert not undocumented, f"CLI subcommands missing from the tutor docs: {undocumented}"


def test_bars_separates_never_tested_from_recalled(tmp_path):
    """A concept recalled once is not solid, and must not read as nothing."""
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.pandas", "python"])
    for c in ("a", "b", "c"):
        lc.main(["--db", db, "record-concept",
                 f"python.pandas/{c}", "python.pandas", c])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    lc.end_session(conn, "python.pandas")            # all three -> shaky
    # a: never cold-tested. b: recalled once, still shaky. c: solid.
    lc.cold_result(conn, "python.pandas/b", False)   # counts, stays shaky
    lc.cold_result(conn, "python.pandas/c", True)    # -> solid

    out = lc.render_bars(conn)

    assert "never tested" in out
    assert "recalled" in out
    assert "solid" in out


def _arc_db(tmp_path, topic="stats.p-values", area="stats"):
    """Build a database with one topic and return an open connection."""
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", topic, area])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    return conn


def test_arc_open_requires_thesis_or_reason(tmp_path):
    """An arc with no idea and no stated reason is the failure this prevents."""
    conn = _arc_db(tmp_path)
    with pytest.raises(ValueError, match="thesis"):
        lc.arc_open(conn, "stats.p-values", None, None, None)


def test_arc_open_accepts_a_declared_absence(tmp_path):
    """Pure volume has no thesis, and saying so is legitimate."""
    conn = _arc_db(tmp_path, "python.pandas", "python")
    arc_id = lc.arc_open(conn, "python.pandas", None,
                         "pure volume, this is for the fingers", None)
    assert arc_id > 0


def test_arc_current_returns_only_open_arcs(tmp_path):
    conn = _arc_db(tmp_path)
    arc_id = lc.arc_open(conn, "stats.p-values",
                         "the threshold IS the false positive rate", None, None)
    assert len(lc.arc_current(conn, None)) == 1
    lc.arc_close(conn, arc_id)
    assert lc.arc_current(conn, None) == []


def test_arc_open_on_unknown_topic_raises(tmp_path):
    conn = _arc_db(tmp_path)
    with pytest.raises(KeyError):
        lc.arc_open(conn, "nope.nothing", "a thesis", None, None)


def test_brief_reports_an_open_arc(tmp_path):
    conn = _arc_db(tmp_path)
    lc.arc_open(conn, "stats.p-values",
                "the threshold IS the false positive rate", None, None)
    out = lc.brief(conn)
    assert "ARC\t" in out
    assert "the threshold IS the false positive rate" in out


def test_brief_offers_a_closed_thesis_for_cold_recall(tmp_path):
    """A thesis closed long enough ago is the strongest recall probe available."""
    conn = _arc_db(tmp_path)
    arc_id = lc.arc_open(conn, "stats.p-values",
                         "the threshold IS the false positive rate", None, None)
    lc.arc_close(conn, arc_id)
    conn.execute("UPDATE arcs SET closed_at='2020-01-01T00:00:00' WHERE id=?",
                 (arc_id,))
    conn.commit()
    assert "THESIS\t" in lc.brief(conn)


def test_brief_does_not_offer_a_freshly_closed_thesis(tmp_path):
    """Recalling a thesis ten minutes after closing it tests nothing."""
    conn = _arc_db(tmp_path)
    arc_id = lc.arc_open(conn, "stats.p-values", "a thesis worth recalling",
                         None, None)
    lc.arc_close(conn, arc_id)
    assert "THESIS\t" not in lc.brief(conn)


def test_brief_never_offers_a_no_thesis_arc_for_recall(tmp_path):
    """There is nothing to restate when the block declared it had no idea."""
    conn = _arc_db(tmp_path, "python.pandas", "python")
    arc_id = lc.arc_open(conn, "python.pandas", None, "pure volume", None)
    lc.arc_close(conn, arc_id)
    conn.execute("UPDATE arcs SET closed_at='2020-01-01T00:00:00' WHERE id=?",
                 (arc_id,))
    conn.commit()
    assert "THESIS\t" not in lc.brief(conn)


def test_brief_caps_deepen_at_the_three_oldest(tmp_path):
    """Unclosed notes accumulate for months: brief shows the stale ones, not all."""
    db = str(tmp_path / "state.db")
    lc.main(["--db", db, "init"])
    lc.main(["--db", db, "record-topic", "python.pandas", "python"])
    for i in range(6):
        lc.main(["--db", db, "to-deepen", "python.pandas", f"note{i}"])
    conn = sqlite3.connect(db)
    lc.init_db(conn)
    # created_at is identical for all six, so order by rowid decides: make it explicit
    for i in range(6):
        conn.execute("UPDATE to_deepen SET created_at=? WHERE label=?",
                     (f"2026-0{i + 1}-01T00:00:00", f"note{i}"))
    conn.commit()

    deepen = [l for l in lc.brief(conn).split("\n") if l.startswith("DEEPEN")]

    assert len(deepen) == 3, "brief must cap DEEPEN, or it grows without bound"
    assert "note0" in deepen[0], "the oldest note comes first: stale items must surface"
    assert not any("note5" in l for l in deepen), "the newest note is not the urgent one"
