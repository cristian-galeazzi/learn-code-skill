import sqlite3
import importlib.util
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent / "skills" / "learn-code" / "learn_code_db.py"
_spec = importlib.util.spec_from_file_location("learn_code_db", SPEC)
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)


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
    assert "Python" in bars and "dictionaries" in bars and "75%" in bars
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
