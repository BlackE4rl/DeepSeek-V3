-- Policy-Ack: Datenmodell (SQLite)
-- Stufen: 1 = nur Information, 2 = Bestätigung per Link, 3 = Bestätigung per Link + MFA

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT NOT NULL,
    unit        TEXT NOT NULL DEFAULT '',
    country     TEXT NOT NULL DEFAULT '',   -- DE | AT | CH | ISO-Code
    language    TEXT NOT NULL DEFAULT 'de', -- de | en
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id    INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    person_id   INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, person_id)
);

-- Eine Verteilung (Kampagne): Information oder Anweisung an Gruppen/Einzelpersonen.
CREATE TABLE IF NOT EXISTS campaigns (
    id             INTEGER PRIMARY KEY,
    key            TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    level          INTEGER NOT NULL CHECK (level IN (1, 2, 3)),
    body           TEXT NOT NULL,
    policy_version TEXT NOT NULL DEFAULT '',
    statement      TEXT NOT NULL DEFAULT '',  -- Bestätigungstext (Stufe 2/3)
    deadline       TEXT,                      -- YYYY-MM-DD, optional
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    closed_at      TEXT
);

-- Eine Zustellung je Empfänger:in und Verteilung.
CREATE TABLE IF NOT EXISTS deliveries (
    id                INTEGER PRIMARY KEY,
    campaign_id       INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    person_id         INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    token_hash        TEXT UNIQUE,            -- NULL bei Stufe 1 (kein Link)
    token_expires_at  TEXT,
    created_at        TEXT NOT NULL,
    sent_at           TEXT,
    reminded_at       TEXT,
    reminder_count    INTEGER NOT NULL DEFAULT 0,
    first_opened_at   TEXT,
    confirmed_at      TEXT,
    confirm_ip        TEXT,
    confirm_ua        TEXT,
    mfa_method        TEXT,                   -- totp | NULL
    failed_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until      TEXT,
    revoked_at        TEXT,
    UNIQUE (campaign_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_campaign ON deliveries(campaign_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_person   ON deliveries(person_id);

-- MFA-Registrierung je Person (Stufe 3). Secret ist ein TOTP-Seed (Base32).
CREATE TABLE IF NOT EXISTS mfa_enrollments (
    person_id     INTEGER PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    secret        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    confirmed_at  TEXT,
    last_counter  INTEGER NOT NULL DEFAULT 0,  -- Replay-Schutz
    reset_at      TEXT
);

-- Fortlaufend gehashtes Protokoll (Nachweisführung). Nur anfügen, nie ändern.
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '{}',
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
