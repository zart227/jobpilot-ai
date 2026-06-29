-- JobPilot AI PostgreSQL Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform VARCHAR(64) NOT NULL,
    external_id VARCHAR(255),
    name VARCHAR(255),
    rating NUMERIC(3, 2),
    reviews_count INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform VARCHAR(64) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL,
    description TEXT NOT NULL,
    budget_min NUMERIC(12, 2),
    budget_max NUMERIC(12, 2),
    budget_currency VARCHAR(8) DEFAULT 'USD',
    skills JSONB DEFAULT '[]',
    deadline TIMESTAMPTZ,
    url VARCHAR(1024),
    client_id UUID REFERENCES clients(id) ON DELETE SET NULL,
    raw_data JSONB DEFAULT '{}',
    is_relevant BOOLEAN,
    relevance_reason TEXT,
    score INTEGER CHECK (score >= 0 AND score <= 100),
    score_breakdown JSONB DEFAULT '{}',
    status VARCHAR(32) NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_platform ON jobs(platform);

CREATE TABLE IF NOT EXISTS proposals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    execution_plan TEXT,
    timeline TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    approved_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proposals_job_id ON proposals(job_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

CREATE TABLE IF NOT EXISTS interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    proposal_id UUID REFERENCES proposals(id) ON DELETE SET NULL,
    client_id UUID REFERENCES clients(id) ON DELETE SET NULL,
    direction VARCHAR(16) NOT NULL,
    channel VARCHAR(32) NOT NULL DEFAULT 'platform',
    message TEXT NOT NULL,
    intent VARCHAR(64),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interactions_job_id ON interactions(job_id);

CREATE TABLE IF NOT EXISTS outcomes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    proposal_id UUID REFERENCES proposals(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL,
    reward INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes(status);

CREATE TABLE IF NOT EXISTS rewards (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    proposal_id UUID REFERENCES proposals(id) ON DELETE SET NULL,
    event_type VARCHAR(32) NOT NULL,
    reward_value INTEGER NOT NULL DEFAULT 0,
    context JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scoring_weights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    weights JSONB NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'learning_agent',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS telegram_pending (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    proposal_id UUID NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    message_id BIGINT,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telegram_pending_status ON telegram_pending(status);
