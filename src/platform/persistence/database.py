import json
import logging
import os
import shutil
import time
from datetime import datetime

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from src.platform.persistence.migrations import (
    has_pending_migrations,
    run_versioned_migrations,
)

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "panwatch.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# SQLite 適合本地開發和單例項部署，但併發寫入時不能無限等待鎖。
# 將等待限制在數秒內，讓上層事務可以回滾/重試或返回明確錯誤，而不是
# 讓瀏覽器請求長時間表現為“卡死”。
SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_INIT_RETRY_DELAYS = (0.5, 1.0, 2.0)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={
        "timeout": SQLITE_BUSY_TIMEOUT_MS / 1_000,
        "check_same_thread": False,
    },
    poolclass=NullPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化資料庫，並容忍開發熱過載期間的短暫跨程式鎖。"""
    for attempt in range(len(SQLITE_INIT_RETRY_DELAYS) + 1):
        try:
            _init_db_once()
            return
        except Exception as exc:
            if not _is_sqlite_lock_error(exc) or attempt >= len(SQLITE_INIT_RETRY_DELAYS):
                raise
            delay = SQLITE_INIT_RETRY_DELAYS[attempt]
            logger.warning(
                "資料庫初始化遇到鎖，%ss 後重試 (%s/%s): %s",
                delay,
                attempt + 1,
                len(SQLITE_INIT_RETRY_DELAYS),
                exc,
            )
            time.sleep(delay)


def _init_db_once() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate(engine)
    _migrate_old_providers(engine)
    _migrate_settings_to_models(engine)
    _migrate_positions_to_accounts(engine)
    _migrate_remove_stock_enabled(engine)
    if has_pending_migrations(engine):
        _backup_db_before_migration()
    run_versioned_migrations(engine)


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    """識別 SQLAlchemy 包裝後的 SQLite 鎖異常。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if "database is locked" in message or "database table is locked" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _has_column(conn, table: str, column: str) -> bool:
    try:
        conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _has_table(conn, table: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _backfill_sort_order(conn, table: str) -> None:
    """只在確有待回填資料時申請 SQLite 寫鎖。"""
    if not _has_column(conn, table, "sort_order"):
        return
    pending = conn.execute(
        text(
            f"SELECT 1 FROM {table} "
            "WHERE sort_order IS NULL OR sort_order = 0 LIMIT 1"
        )
    ).first()
    if not pending:
        return
    conn.execute(
        text(
            f"UPDATE {table} SET sort_order = id "
            "WHERE sort_order IS NULL OR sort_order = 0"
        )
    )
    conn.commit()


def _drop_dangling_ai_provider_fk(conn, table: str) -> None:
    """刪掉指向已不存在的 ai_providers 表的懸空外部索引鍵列。

    背景: _migrate_old_providers 把 ai_providers 表刪了,但
    agent_configs.ai_provider_id / stock_agents.ai_provider_id 這兩列上的 FK 沒清。
    SQLite 預設 PRAGMA foreign_keys 不開,所以歷史 INSERT 沒事;但某些路徑下
    (比如 INSERT ... RETURNING + SQLAlchemy 校驗)會報 "no such table: ai_providers"。

    SQLite 3.35+ 支援 ALTER TABLE DROP COLUMN,直接 drop 即可。
    """
    if not _has_column(conn, table, "ai_provider_id"):
        return
    # ai_providers 還存在的話先不動(讓 _migrate_old_providers 先遷移)
    if _has_table(conn, "ai_providers"):
        return
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN ai_provider_id"))
        conn.commit()
        logger.info(f"已清理 {table}.ai_provider_id 懸空外部索引鍵列")
    except Exception as e:
        # 老 SQLite 不支援 DROP COLUMN — fallback 留 schema 不動,改用 PRAGMA foreign_keys=OFF
        # (本程式級別,不影響其他業務,因為 ai_providers 不存在,FK 永遠無效)
        logger.warning(
            f"DROP COLUMN {table}.ai_provider_id 失敗 (SQLite < 3.35?): {e}; "
            f"將透過 PRAGMA foreign_keys=OFF 繞開"
        )
        try:
            conn.execute(text("PRAGMA foreign_keys = OFF"))
            conn.commit()
        except Exception:
            pass


def _backup_db_before_migration() -> None:
    """Create a timestamped sqlite backup before versioned migrations."""
    if not os.path.exists(DB_PATH):
        return
    try:
        size = os.path.getsize(DB_PATH)
        if size <= 0:
            return
    except Exception:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.bak.{ts}"
    try:
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"資料庫遷移前備份已建立: {backup_path}")
    except Exception as e:
        logger.warning(f"資料庫遷移前備份失敗: {e}")


def _migrate(engine):
    """增量 schema 遷移（SQLite ALTER TABLE ADD COLUMN）"""
    migrations = [
        # Phase 1(模擬交易求真):持倉期最高價,移動停損用
        (
            "paper_trading_positions",
            "highest_price",
            "ALTER TABLE paper_trading_positions ADD COLUMN highest_price REAL",
        ),
        (
            "stock_agents",
            "schedule",
            "ALTER TABLE stock_agents ADD COLUMN schedule TEXT DEFAULT ''",
        ),
        (
            "agent_configs",
            "ai_model_id",
            "ALTER TABLE agent_configs ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "agent_configs",
            "notify_channel_ids",
            "ALTER TABLE agent_configs ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        (
            "stock_agents",
            "ai_model_id",
            "ALTER TABLE stock_agents ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "stock_agents",
            "notify_channel_ids",
            "ALTER TABLE stock_agents ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        # Phase 3: 持倉增強
        (
            "stocks",
            "invested_amount",
            "ALTER TABLE stocks ADD COLUMN invested_amount REAL",
        ),
        # Phase 4: Agent 執行模式
        (
            "agent_configs",
            "execution_mode",
            "ALTER TABLE agent_configs ADD COLUMN execution_mode TEXT DEFAULT 'batch'",
        ),
        # Phase 4: 持倉交易風格
        (
            "positions",
            "trading_style",
            "ALTER TABLE positions ADD COLUMN trading_style TEXT DEFAULT 'swing'",
        ),
        # 排序欄位：關注列表/持倉拖拽排序
        (
            "stocks",
            "sort_order",
            "ALTER TABLE stocks ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        (
            "positions",
            "sort_order",
            "ALTER TABLE positions ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        # 資料來源增強
        (
            "data_sources",
            "supports_batch",
            "ALTER TABLE data_sources ADD COLUMN supports_batch INTEGER DEFAULT 0",
        ),
        (
            "data_sources",
            "test_symbols",
            "ALTER TABLE data_sources ADD COLUMN test_symbols TEXT DEFAULT '[]'",
        ),
        # Phase 5: 建議池後設資料
        (
            "stock_suggestions",
            "meta",
            "ALTER TABLE stock_suggestions ADD COLUMN meta TEXT DEFAULT '{}'",
        ),
    ]
    with engine.connect() as conn:
        for table, column, sql in migrations:
            if not _has_column(conn, table, column):
                conn.execute(text(sql))
                conn.commit()

        # 清理 legacy 懸空外部索引鍵:agent_configs.ai_provider_id / stock_agents.ai_provider_id
        # 這兩列原本 REFERENCES ai_providers(id),但 _migrate_old_providers 已經把
        # ai_providers 表刪了。如果保留 FK,新 INSERT 會觸發 SQLite "no such table" 錯誤
        # (因為 SQLite 在 INSERT 時校驗 FK 引用的表是否存在)。
        _drop_dangling_ai_provider_fk(conn, "agent_configs")
        _drop_dangling_ai_provider_fk(conn, "stock_agents")

        # 初始化排序欄位（僅對未初始化資料）
        _backfill_sort_order(conn, "stocks")
        _backfill_sort_order(conn, "positions")

        # Create new tables if missing (SQLite)
        if not _has_table(conn, "suggestion_feedback"):
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS suggestion_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  suggestion_id INTEGER NOT NULL REFERENCES stock_suggestions(id) ON DELETE CASCADE,
  useful INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_suggestion_id ON suggestion_feedback(suggestion_id);"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON suggestion_feedback(created_at);"
                )
            )
            conn.commit()


def _migrate_old_providers(engine):
    """如果存在舊的 ai_providers 表，遷移資料到 ai_services + ai_models"""
    with engine.connect() as conn:
        if not _has_table(conn, "ai_providers"):
            return
        # Check if it has the old schema (has base_url column)
        if not _has_column(conn, "ai_providers", "base_url"):
            return

        rows = conn.execute(
            text(
                "SELECT id, name, base_url, api_key, model, is_default FROM ai_providers"
            )
        ).fetchall()
        if not rows:
            conn.execute(text("DROP TABLE IF EXISTS ai_providers"))
            conn.commit()
            return

        # Group by base_url+api_key to create services
        service_map = {}  # (base_url, api_key) -> service_id
        for row in rows:
            old_id, name, base_url, api_key, model, is_default = row
            key = (base_url, api_key)
            if key not in service_map:
                # Create service
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": name, "base_url": base_url, "api_key": api_key},
                )
                result = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                service_map[key] = result

            service_id = service_map[key]
            conn.execute(
                text(
                    "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, :is_default)"
                ),
                {
                    "name": name,
                    "service_id": service_id,
                    "model": model,
                    "is_default": is_default,
                },
            )
            new_model_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

            # Update references: agent_configs.ai_provider_id → ai_model_id
            if _has_column(conn, "agent_configs", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE agent_configs SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )
            # stock_agents.ai_provider_id → ai_model_id
            if _has_column(conn, "stock_agents", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE stock_agents SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )

        conn.execute(text("DROP TABLE ai_providers"))
        conn.commit()
        logger.info(
            f"已遷移 {len(rows)} 條舊 AI Provider 資料到 ai_services + ai_models"
        )


def _migrate_settings_to_models(engine):
    """將舊的 app_settings 中的 AI/通知配置遷移為 AIService+AIModel / NotifyChannel 記錄"""
    with engine.connect() as conn:
        if not _has_table(conn, "app_settings"):
            return

        rows = conn.execute(text("SELECT key, value FROM app_settings")).fetchall()
        settings_map = {row[0]: row[1] for row in rows}

        ai_base_url = settings_map.get("ai_base_url", "")
        ai_api_key = settings_map.get("ai_api_key", "")
        ai_model = settings_map.get("ai_model", "")

        # Migrate AI settings if present and no services exist yet
        if ai_base_url and ai_model:
            existing = conn.execute(text("SELECT COUNT(*) FROM ai_services")).scalar()
            if existing == 0:
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": ai_model, "base_url": ai_base_url, "api_key": ai_api_key},
                )
                service_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                conn.execute(
                    text(
                        "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, 1)"
                    ),
                    {"name": ai_model, "service_id": service_id, "model": ai_model},
                )
                logger.info(f"已遷移 AI 配置: {ai_model}")

        # Migrate Telegram settings if present and no channels exist yet
        bot_token = settings_map.get("notify_telegram_bot_token", "")
        chat_id = settings_map.get("notify_telegram_chat_id", "")

        if bot_token:
            existing = conn.execute(
                text("SELECT COUNT(*) FROM notify_channels")
            ).scalar()
            if existing == 0:
                config_json = json.dumps({"bot_token": bot_token, "chat_id": chat_id})
                conn.execute(
                    text(
                        "INSERT INTO notify_channels (name, type, config, enabled, is_default) VALUES (:name, :type, :config, 1, 1)"
                    ),
                    {"name": "Telegram", "type": "telegram", "config": config_json},
                )
                logger.info("已遷移 Telegram 配置為 NotifyChannel")

        # Remove old settings keys
        old_keys = [
            "ai_base_url",
            "ai_api_key",
            "ai_model",
            "notify_telegram_bot_token",
            "notify_telegram_chat_id",
        ]
        for key in old_keys:
            if key in settings_map:
                conn.execute(
                    text("DELETE FROM app_settings WHERE key = :key"), {"key": key}
                )

        conn.commit()


def _migrate_positions_to_accounts(engine):
    """
    將舊的 stocks 表中的持倉資料遷移到 accounts + positions 表
    建立一個預設帳戶，並將有持倉的股票資料遷移過去
    """
    with engine.connect() as conn:
        # 檢查是否已有帳戶資料（避免重複遷移）
        if not _has_table(conn, "accounts"):
            return

        existing_accounts = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        if existing_accounts > 0:
            return

        # 檢查 stocks 表是否有持倉資料需要遷移
        if not _has_column(conn, "stocks", "cost_price"):
            return

        stocks_with_position = conn.execute(
            text(
                "SELECT id, cost_price, quantity, invested_amount FROM stocks "
                "WHERE cost_price IS NOT NULL AND quantity IS NOT NULL"
            )
        ).fetchall()

        if not stocks_with_position:
            # 沒有持倉資料，建立一個空的預設帳戶
            conn.execute(
                text(
                    "INSERT INTO accounts (name, available_funds, enabled) VALUES ('預設帳戶', 0, 1)"
                )
            )
            conn.commit()
            logger.info("已建立預設帳戶")
            return

        # 建立預設帳戶
        # 先獲取舊的 available_funds 設定
        old_funds = conn.execute(
            text("SELECT value FROM app_settings WHERE key = 'available_funds'")
        ).scalar()
        available_funds = float(old_funds) if old_funds else 0

        conn.execute(
            text(
                "INSERT INTO accounts (name, available_funds, enabled) VALUES (:name, :funds, 1)"
            ),
            {"name": "預設帳戶", "funds": available_funds},
        )
        account_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        # 遷移持倉資料
        for row in stocks_with_position:
            stock_id, cost_price, quantity, invested_amount = row
            conn.execute(
                text(
                    "INSERT INTO positions (account_id, stock_id, cost_price, quantity, invested_amount) "
                    "VALUES (:account_id, :stock_id, :cost_price, :quantity, :invested_amount)"
                ),
                {
                    "account_id": account_id,
                    "stock_id": stock_id,
                    "cost_price": cost_price,
                    "quantity": quantity,
                    "invested_amount": invested_amount,
                },
            )

        # 刪除舊的 available_funds 設定
        conn.execute(text("DELETE FROM app_settings WHERE key = 'available_funds'"))

        conn.commit()
        logger.info(f"已遷移 {len(stocks_with_position)} 條持倉資料到預設帳戶")


def _migrate_remove_stock_enabled(engine):
    """移除歷史 stocks.enabled 軟刪除欄位並清理殘留資料。"""
    with engine.connect() as conn:
        if not _has_table(conn, "stocks") or not _has_column(conn, "stocks", "enabled"):
            return

        # 歷史軟刪除資料：無任何關聯則直接刪除；有關聯則恢復為有效股票。
        conn.execute(
            text(
                """
DELETE FROM stocks
WHERE COALESCE(enabled, 1) = 0
  AND id NOT IN (SELECT DISTINCT stock_id FROM positions)
  AND id NOT IN (SELECT DISTINCT stock_id FROM stock_agents)
  AND id NOT IN (SELECT DISTINCT stock_id FROM price_alert_rules)
"""
            )
        )
        conn.execute(text("UPDATE stocks SET enabled = 1 WHERE COALESCE(enabled, 1) = 0"))
        conn.commit()

        # 優先直接刪列；舊版 SQLite 不支援時，重建表以確保物理移除。
        try:
            conn.execute(text("ALTER TABLE stocks DROP COLUMN enabled"))
            conn.commit()
            logger.info("已移除 stocks.enabled 列")
        except Exception:
            conn.rollback()
            logger.info("當前 SQLite 不支援 DROP COLUMN，改為重建 stocks 表移除 enabled")
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS stocks__new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  market VARCHAR NOT NULL,
  cost_price FLOAT,
  quantity INTEGER,
  invested_amount FLOAT,
  sort_order INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
                )
            )
            conn.execute(
                text(
                    """
INSERT INTO stocks__new (
  id, symbol, name, market, cost_price, quantity, invested_amount, sort_order, created_at, updated_at
)
SELECT
  id, symbol, name, market, cost_price, quantity, invested_amount, COALESCE(sort_order, 0), created_at, updated_at
FROM stocks
"""
                )
            )
            conn.execute(text("DROP TABLE stocks"))
            conn.execute(text("ALTER TABLE stocks__new RENAME TO stocks"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            logger.info("已透過重建表移除 stocks.enabled 列")
