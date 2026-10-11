-- =============================================================================
-- TimescaleDB Schema for Forex Algo Trading
-- Pairs: XAUUSD, EURUSD, USDJPY, DXY
-- Timeframes: 1m (primary), 1d
-- =============================================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Core OHLCV table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ohlcv (
    ts          TIMESTAMPTZ         NOT NULL,
    symbol      TEXT                NOT NULL,
    timeframe   TEXT                NOT NULL,   -- '1m', '5m', '15m', '1h', '1d'
    open        DOUBLE PRECISION    NOT NULL,
    high        DOUBLE PRECISION    NOT NULL,
    low         DOUBLE PRECISION    NOT NULL,
    close       DOUBLE PRECISION    NOT NULL,
    volume      DOUBLE PRECISION    NOT NULL DEFAULT 0.0,
    source      TEXT                NOT NULL    -- 'polygon', 'alpha_vantage', 'yfinance'
);

-- Make it a hypertable partitioned by time (1-week chunks for 1m data)
SELECT create_hypertable(
    'ohlcv',
    'ts',
    chunk_time_interval => INTERVAL '1 week',
    if_not_exists => TRUE
);

-- Unique constraint prevents duplicate bars on re-ingestion
CREATE UNIQUE INDEX IF NOT EXISTS uix_ohlcv ON ohlcv (symbol, timeframe, ts);

-- Covering index for typical range queries
CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_tf_ts ON ohlcv (symbol, timeframe, ts DESC);

-- ---------------------------------------------------------------------------
-- Continuous aggregates (auto-derive 5m, 15m, 1h from 1m bars)
-- ---------------------------------------------------------------------------

-- 5-minute bars
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_5m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', ts)   AS ts,
    symbol,
    '5m'                           AS timeframe,
    first(open,  ts)               AS open,
    max(high)                      AS high,
    min(low)                       AS low,
    last(close, ts)                AS close,
    sum(volume)                    AS volume,
    last(source, ts)               AS source
FROM ohlcv
WHERE timeframe = '1m'
GROUP BY time_bucket('5 minutes', ts), symbol
WITH NO DATA;

-- 15-minute bars
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_15m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('15 minutes', ts)  AS ts,
    symbol,
    '15m'                          AS timeframe,
    first(open,  ts)               AS open,
    max(high)                      AS high,
    min(low)                       AS low,
    last(close, ts)                AS close,
    sum(volume)                    AS volume,
    last(source, ts)               AS source
FROM ohlcv
WHERE timeframe = '1m'
GROUP BY time_bucket('15 minutes', ts), symbol
WITH NO DATA;

-- 1-hour bars
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts)      AS ts,
    symbol,
    '1h'                           AS timeframe,
    first(open,  ts)               AS open,
    max(high)                      AS high,
    min(low)                       AS low,
    last(close, ts)                AS close,
    sum(volume)                    AS volume,
    last(source, ts)               AS source
FROM ohlcv
WHERE timeframe = '1m'
GROUP BY time_bucket('1 hour', ts), symbol
WITH NO DATA;

-- ---------------------------------------------------------------------------
-- Refresh policies for continuous aggregates
-- ---------------------------------------------------------------------------
SELECT add_continuous_aggregate_policy('ohlcv_5m',
    start_offset  => INTERVAL '1 day',
    end_offset    => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy('ohlcv_15m',
    start_offset  => INTERVAL '1 day',
    end_offset    => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy('ohlcv_1h',
    start_offset  => INTERVAL '1 day',
    end_offset    => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Data coverage tracking table (populated by ingestion pipeline)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          SERIAL PRIMARY KEY,
    symbol      TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    bars_fetched INT,
    bars_inserted INT,
    date_from   TIMESTAMPTZ,
    date_to     TIMESTAMPTZ,
    status      TEXT        NOT NULL DEFAULT 'running',  -- 'running', 'success', 'error'
    error_msg   TEXT
);

-- ---------------------------------------------------------------------------
-- Compression policy (compress chunks older than 7 days)
-- ---------------------------------------------------------------------------
ALTER TABLE ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, timeframe'
);

SELECT add_compression_policy('ohlcv',
    compress_after => INTERVAL '7 days',
    if_not_exists  => TRUE);
