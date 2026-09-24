"""驗證中心資料庫遷移。"""

from sqlalchemy import create_engine, text


def test_m120_adds_agent_prediction_evaluation_columns(tmp_path):
    """舊建議後驗表升級後帶分組 ID 與口徑欄位。"""
    from src.platform.persistence.migrations import _m120_agent_prediction_evaluation

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """CREATE TABLE agent_prediction_outcomes (
                  id INTEGER PRIMARY KEY, agent_name TEXT, stock_symbol TEXT,
                  stock_market TEXT, prediction_date TEXT, horizon_days INTEGER,
                  action TEXT, action_label TEXT, outcome_status TEXT
                )"""
            )
        )
        _m120_agent_prediction_evaluation(conn)
        columns = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(agent_prediction_outcomes)")
            )
        }
    # Windows 會因連線池保留 sqlite 檔案控制程式碼而無法清理臨時目錄。
    engine.dispose()

    assert {"prediction_group_id", "horizon_unit"} <= columns


def test_m121_creates_backtest_runs_table(tmp_path):
    """遷移會在已有資料庫中建立可持久化的回測執行表。"""
    from src.platform.persistence.migrations import _m121_backtest_runs

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-backtest.db'}")
    with engine.begin() as conn:
        _m121_backtest_runs(conn)
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(backtest_runs)"))
        }
    engine.dispose()

    assert {"status", "strategy_code", "config", "input_snapshot", "result"} <= columns
