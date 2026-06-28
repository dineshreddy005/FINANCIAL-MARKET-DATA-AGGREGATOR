"""
Async database layer. Uses a connection pool (asyncpg via SQLAlchemy's async
engine) sized for a single small instance -- tune pool_size/max_overflow as
traffic grows. Pooling matters here for *performance*: every websocket tick
and every batch row write reuses a live connection instead of paying
TCP+TLS+auth handshake cost per query.
"""
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import get_settings

from sqlalchemy import text, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import get_settings

settings = get_settings()

is_sqlite = settings.mock_services or "sqlite" in settings.database_url

if is_sqlite:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
    )
    
    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()
        
        driver_conn = dbapi_connection.driver_connection
        driver_conn.current_actor = "system"
        
        def get_actor():
            return getattr(driver_conn, "current_actor", "system")
            
        dbapi_connection.create_function("get_current_actor", 0, get_actor)
else:
    engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_pre_ping=True,   # avoids "server closed the connection unexpectedly" under load
        echo=False,
    )

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    """FastAPI dependency -- yields a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def set_audit_actor(session: AsyncSession, actor: str) -> None:
    """
    Requirement 5 (audit trail): tells the database WHO is about to perform
    a write, so the `fn_audit_log()` trigger (sql/schema.sql) can stamp the
    resulting audit_logs row with a real identity instead of 'system'.
    """
    if is_sqlite:
        conn = await session.connection()
        dbapi_conn = await conn.get_raw_connection()
        driver_conn = dbapi_conn.driver_connection
        driver_conn.current_actor = actor
    else:
        await session.execute(text("SELECT set_config('app.current_actor', :actor, true)"), {"actor": actor})


@asynccontextmanager
async def session_scope():
    """For use outside request context (e.g. background ingestion tasks)."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --- SQLite Schema and Triggers for Fallback Mode ---------------------------
SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        asset_type TEXT NOT NULL CHECK (asset_type IN ('equity', 'crypto', 'fx', 'index')),
        display_name TEXT,
        currency TEXT NOT NULL DEFAULT 'USD',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        sector TEXT,
        market_cap REAL,
        description TEXT,
        week52_high REAL,
        week52_low REAL,
        profile_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (symbol, asset_type)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS price_ticks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        event_time TIMESTAMP NOT NULL,
        price REAL NOT NULL,
        volume REAL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        payload_hash TEXT NOT NULL,
        ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (asset_id, source, event_time)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('stream', 'batch')),
        file_name TEXT,
        rows_received INTEGER NOT NULL DEFAULT 0,
        rows_inserted INTEGER NOT NULL DEFAULT 0,
        rows_deduped INTEGER NOT NULL DEFAULT 0,
        rows_updated INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS circuit_breaker_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        reason TEXT,
        occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'client', 'service')),
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login_at TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS client_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_number TEXT NOT NULL,
        routing_number TEXT,
        broker_name TEXT NOT NULL DEFAULT 'FMDA Prime Brokerage',
        account_type TEXT NOT NULL DEFAULT 'individual' CHECK (account_type IN ('individual', 'ira', 'corporate')),
        cash_balance REAL NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS account_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES client_accounts(id) ON DELETE CASCADE,
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        quantity REAL NOT NULL DEFAULT 0,
        avg_cost REAL NOT NULL DEFAULT 0,
        UNIQUE (account_id, asset_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        record_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
        changed_by TEXT NOT NULL,
        old_data TEXT,
        new_data TEXT,
        changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
]

SQLITE_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_price_ticks_update
    AFTER UPDATE ON price_ticks
    FOR EACH ROW
    BEGIN
        INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
        VALUES (
            'price_ticks',
            CAST(NEW.id AS TEXT),
            'UPDATE',
            get_current_actor(),
            json_object(
                'id', OLD.id, 'asset_id', OLD.asset_id, 'source', OLD.source, 'event_time', strftime('%Y-%m-%dT%H:%M:%f', OLD.event_time),
                'price', OLD.price, 'volume', OLD.volume, 'open', OLD.open, 'high', OLD.high, 'low', OLD.low,
                'close', OLD.close, 'payload_hash', OLD.payload_hash, 'ingested_at', strftime('%Y-%m-%dT%H:%M:%f', OLD.ingested_at)
            ),
            json_object(
                'id', NEW.id, 'asset_id', NEW.asset_id, 'source', NEW.source, 'event_time', strftime('%Y-%m-%dT%H:%M:%f', NEW.event_time),
                'price', NEW.price, 'volume', NEW.volume, 'open', NEW.open, 'high', NEW.high, 'low', NEW.low,
                'close', NEW.close, 'payload_hash', NEW.payload_hash, 'ingested_at', strftime('%Y-%m-%dT%H:%M:%f', NEW.ingested_at)
            ),
            CURRENT_TIMESTAMP
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_client_accounts_insert
    AFTER INSERT ON client_accounts
    FOR EACH ROW
    BEGIN
        INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
        VALUES (
            'client_accounts',
            CAST(NEW.id AS TEXT),
            'INSERT',
            get_current_actor(),
            NULL,
            json_object(
                'id', NEW.id, 'user_id', NEW.user_id, 'account_number', NEW.account_number,
                'routing_number', NEW.routing_number, 'broker_name', NEW.broker_name,
                'account_type', NEW.account_type, 'cash_balance', NEW.cash_balance,
                'created_at', strftime('%Y-%m-%dT%H:%M:%f', NEW.created_at), 'updated_at', strftime('%Y-%m-%dT%H:%M:%f', NEW.updated_at)
            ),
            CURRENT_TIMESTAMP
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_client_accounts_update
    AFTER UPDATE ON client_accounts
    FOR EACH ROW
    BEGIN
        INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
        VALUES (
            'client_accounts',
            CAST(NEW.id AS TEXT),
            'UPDATE',
            get_current_actor(),
            json_object(
                'id', OLD.id, 'user_id', OLD.user_id, 'account_number', OLD.account_number,
                'routing_number', OLD.routing_number, 'broker_name', OLD.broker_name,
                'account_type', OLD.account_type, 'cash_balance', OLD.cash_balance,
                'created_at', strftime('%Y-%m-%dT%H:%M:%f', OLD.created_at), 'updated_at', strftime('%Y-%m-%dT%H:%M:%f', OLD.updated_at)
            ),
            json_object(
                'id', NEW.id, 'user_id', NEW.user_id, 'account_number', NEW.account_number,
                'routing_number', NEW.routing_number, 'broker_name', NEW.broker_name,
                'account_type', NEW.account_type, 'cash_balance', NEW.cash_balance,
                'created_at', strftime('%Y-%m-%dT%H:%M:%f', NEW.created_at), 'updated_at', strftime('%Y-%m-%dT%H:%M:%f', NEW.updated_at)
            ),
            CURRENT_TIMESTAMP
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_account_holdings_insert
    AFTER INSERT ON account_holdings
    FOR EACH ROW
    BEGIN
        INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
        VALUES (
            'account_holdings',
            CAST(NEW.id AS TEXT),
            'INSERT',
            get_current_actor(),
            NULL,
            json_object(
                'id', NEW.id, 'account_id', NEW.account_id, 'asset_id', NEW.asset_id,
                'quantity', NEW.quantity, 'avg_cost', NEW.avg_cost
            ),
            CURRENT_TIMESTAMP
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_account_holdings_update
    AFTER UPDATE ON account_holdings
    FOR EACH ROW
    BEGIN
        INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
        VALUES (
            'account_holdings',
            CAST(NEW.id AS TEXT),
            'UPDATE',
            get_current_actor(),
            json_object(
                'id', OLD.id, 'account_id', OLD.account_id, 'asset_id', OLD.asset_id,
                'quantity', OLD.quantity, 'avg_cost', OLD.avg_cost
            ),
            json_object(
                'id', NEW.id, 'account_id', NEW.account_id, 'asset_id', NEW.asset_id,
                'quantity', NEW.quantity, 'avg_cost', NEW.avg_cost
            ),
            CURRENT_TIMESTAMP
        );
    END;
    """
]


async def init_db() -> None:
    if not is_sqlite:
        return

    # Check if database is already seeded
    async with SessionLocal() as session:
        try:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            count = result.scalar()
            if count is not None and count > 0:
                return  # already seeded
        except Exception:
            pass  # tables don't exist yet

    # Create tables
    async with engine.begin() as conn:
        for stmt in SQLITE_SCHEMA:
            await conn.execute(text(stmt))
        for stmt in SQLITE_TRIGGERS:
            await conn.execute(text(stmt))

    # Seed data
    async with SessionLocal() as session:
        # Seed users
        await session.execute(text(
            """
            INSERT OR IGNORE INTO users (username, email, full_name, password_hash, role)
            VALUES
                ('admin', 'admin@fmda.local', 'Priya Admin',
                 'pbkdf2_sha256$260000$dfc1dfbb978c4b50cee9650a2e287331$aa74ef77b53b279e6c60649daf1f315e38868360bbc6a6bbbb248022a870270a',
                 'admin'),
                ('jane.c', 'jane.c@fmda.local', 'Jane Client',
                 'pbkdf2_sha256$260000$9cfc79772685c57ef617dc7dc620ce69$d8f348215a9c8fcc6b7a7ab9d7ad07734655a1d1660c34057caf5c06517a48d8',
                 'client'),
                ('ingest-svc', 'ingest-svc@fmda.local', 'Ingestion Service Account',
                 'pbkdf2_sha256$260000$4f301d43de4ac63cf16eb6ac2c76f151$cf3c29a1b1639719df8215ef238ea81b4b2f8e53353a40b582fc05c8e5dbe38d',
                 'service')
            """
        ))

        # Seed client accounts
        await session.execute(text(
            """
            INSERT OR IGNORE INTO client_accounts (user_id, account_number, routing_number, broker_name, account_type, cash_balance)
            SELECT id, '4400123456789104', '021000021', 'FMDA Prime Brokerage', 'individual', 18250.50
            FROM users WHERE username = 'jane.c' AND NOT EXISTS (
                SELECT 1 FROM client_accounts WHERE user_id = users.id
            )
            """
        ))

        # Seed assets
        await session.execute(text(
            """
            INSERT OR IGNORE INTO assets (symbol, asset_type, display_name, currency, sector, market_cap, description, week52_high, week52_low)
            VALUES
                ('AAPL', 'equity', 'Apple Inc.', 'USD', 'Technology', 3450000000000,
                 'Designs, manufactures, and markets consumer electronics, software, and services.', 240.50, 164.08),
                ('MSFT', 'equity', 'Microsoft Corporation', 'USD', 'Technology', 3180000000000,
                 'Develops, licenses, and supports software, services, devices, and solutions worldwide.', 468.35, 309.45),
                ('TSLA', 'equity', 'Tesla, Inc.', 'USD', 'Consumer Cyclical', 920000000000,
                 'Designs, develops, manufactures, and sells electric vehicles and energy generation systems.', 488.54, 138.80)
            """
        ))

        # Seed holdings
        await session.execute(text(
            """
            INSERT OR IGNORE INTO account_holdings (account_id, asset_id, quantity, avg_cost)
            SELECT ca.id, a.id, 25, 178.30
            FROM client_accounts ca, assets a
            WHERE ca.user_id = (SELECT id FROM users WHERE username = 'jane.c') AND a.symbol = 'AAPL'
              AND NOT EXISTS (
                  SELECT 1 FROM account_holdings WHERE account_id = ca.id AND asset_id = a.id
              )
            """
        ))
        await session.commit()

