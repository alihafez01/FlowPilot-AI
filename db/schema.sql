-- Companies discovered by FlowPilot AI
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name TEXT NOT NULL,
    normalized_domain TEXT UNIQUE,
    website_url TEXT,

    industry TEXT,
    country_code CHAR(2),
    description TEXT,

    services JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(services) = 'array'),

    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(source_urls) = 'array'),

    source_last_checked_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        normalized_domain IS NULL
        OR normalized_domain = lower(btrim(normalized_domain))
    )
);

CREATE INDEX IF NOT EXISTS idx_companies_industry
    ON companies (industry);

CREATE INDEX IF NOT EXISTS idx_companies_created_at
    ON companies (created_at DESC);


-- People and publicly available business contacts
CREATE TABLE IF NOT EXISTS contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    company_id UUID NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    full_name TEXT,
    job_title TEXT,

    email TEXT,
    normalized_email TEXT,

    email_validation_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (
            email_validation_status IN (
                'missing',
                'unknown',
                'syntax_valid',
                'mx_valid',
                'verified',
                'invalid',
                'risky'
            )
        ),

    source_url TEXT,
    source_observed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        normalized_email IS NULL
        OR normalized_email = lower(btrim(normalized_email))
    )
);

-- Avoid creating two contact records for the same normalized email.
CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_normalized_email
    ON contacts (normalized_email)
    WHERE normalized_email IS NOT NULL
      AND normalized_email <> '';

CREATE INDEX IF NOT EXISTS idx_contacts_company_id
    ON contacts (company_id);


-- Sales outreach campaigns and scheduling configuration
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name TEXT NOT NULL,
    target_description TEXT,
    service_offering TEXT,

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (
            status IN (
                'draft',
                'running',
                'paused',
                'completed'
            )
        ),

    max_followups SMALLINT NOT NULL DEFAULT 2
        CHECK (max_followups BETWEEN 0 AND 5),

    -- Day offsets measured from the initial email.
    follow_up_offsets_days INTEGER[] NOT NULL
        DEFAULT ARRAY[4, 9],

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- Qualification and AI-analysis history
CREATE TABLE IF NOT EXISTS lead_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    company_id UUID NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,

    contact_id UUID
        REFERENCES contacts(id) ON DELETE SET NULL,

    fit_score SMALLINT NOT NULL
        CHECK (fit_score BETWEEN 0 AND 100),

    priority TEXT NOT NULL
        CHECK (priority IN ('low', 'medium', 'high')),

    fit_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,

    model_name TEXT,
    prompt_version TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lead_analyses_company
    ON lead_analyses (company_id, created_at DESC);


-- Drafts, approvals and email-sending history
CREATE TABLE IF NOT EXISTS outreach_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    campaign_id UUID NOT NULL
        REFERENCES campaigns(id) ON DELETE RESTRICT,

    company_id UUID NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    contact_id UUID NOT NULL
        REFERENCES contacts(id) ON DELETE RESTRICT,

    sequence_no SMALLINT NOT NULL DEFAULT 0
        CHECK (sequence_no >= 0),

    revision_no SMALLINT NOT NULL DEFAULT 1
        CHECK (revision_no >= 1),

    message_type TEXT NOT NULL
        CHECK (
            message_type IN ('initial', 'follow_up', 'manual')
        ),

    subject TEXT NOT NULL,
    body TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (
            status IN (
                'draft',
                'pending_approval',
                'approved',
                'queued',
                'sent',
                'delivered',
                'replied',
                'failed',
                'cancelled',
                'suppressed',
                'bounced'
            )
        ),

    idempotency_key TEXT UNIQUE,
    provider_message_id TEXT UNIQUE,
    thread_id TEXT,

    scheduled_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,

    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (
        campaign_id,
        contact_id,
        sequence_no,
        revision_no
    )
);

CREATE INDEX IF NOT EXISTS idx_outreach_due
    ON outreach_messages (status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_outreach_contact
    ON outreach_messages (contact_id, created_at DESC);


-- Emails that must not receive additional outreach
CREATE TABLE IF NOT EXISTS suppressions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    normalized_email TEXT NOT NULL UNIQUE,

    reason TEXT NOT NULL
        CHECK (
            reason IN (
                'opt_out',
                'bounce',
                'complaint',
                'manual',
                'invalid'
            )
        ),

    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (normalized_email = lower(btrim(normalized_email)))
);


-- Workflow execution history, including failures
CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    workflow_name TEXT NOT NULL,
    n8n_execution_id TEXT,

    status TEXT NOT NULL DEFAULT 'running'
        CHECK (
            status IN (
                'running',
                'succeeded',
                'failed',
                'cancelled'
            )
        ),

    attempt_number INTEGER NOT NULL DEFAULT 1
        CHECK (attempt_number >= 1),

    input_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_summary JSONB NOT NULL DEFAULT '{}'::jsonb,

    error_code TEXT,
    error_summary TEXT,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
    ON workflow_runs (status, started_at DESC);


-- Audit trail for important business actions
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT NOT NULL,

    entity_type TEXT NOT NULL,
    entity_id UUID,

    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created
    ON audit_events (created_at DESC);


-- Maintain updated_at on updates to core records
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_companies_updated_at ON companies;
CREATE TRIGGER trg_companies_updated_at
BEFORE UPDATE ON companies
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_contacts_updated_at ON contacts;
CREATE TRIGGER trg_contacts_updated_at
BEFORE UPDATE ON contacts
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_campaigns_updated_at ON campaigns;
CREATE TRIGGER trg_campaigns_updated_at
BEFORE UPDATE ON campaigns
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_outreach_messages_updated_at
    ON outreach_messages;

CREATE TRIGGER trg_outreach_messages_updated_at
BEFORE UPDATE ON outreach_messages
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- Phase 2: website validation status
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS website_validation_status TEXT
NOT NULL DEFAULT 'candidate';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'companies_website_validation_status_check'
    ) THEN
        ALTER TABLE companies
        ADD CONSTRAINT companies_website_validation_status_check
        CHECK (
            website_validation_status IN (
                'candidate',
                'likely_official',
                'needs_review',
                'rejected'
            )
        );
    END IF;
END;
$$;


-- Every search execution gets a durable record.
CREATE TABLE IF NOT EXISTS discovery_runs (
    id UUID PRIMARY KEY,

    status TEXT NOT NULL DEFAULT 'running'
        CHECK (
            status IN (
                'running',
                'completed',
                'partial',
                'failed'
            )
        ),

    icp_config JSONB NOT NULL,
    queries JSONB NOT NULL DEFAULT '[]'::jsonb,

    results_count INTEGER NOT NULL DEFAULT 0
        CHECK (results_count >= 0),

    companies_upserted INTEGER NOT NULL DEFAULT 0
        CHECK (companies_upserted >= 0),

    extracted_count INTEGER NOT NULL DEFAULT 0
        CHECK (extracted_count >= 0),

    tavily_credits INTEGER NOT NULL DEFAULT 0
        CHECK (tavily_credits >= 0),

    errors JSONB NOT NULL DEFAULT '[]'::jsonb,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_started
    ON discovery_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_status
    ON discovery_runs (status, started_at DESC);


-- Keep source-level evidence instead of storing only a company name.
CREATE TABLE IF NOT EXISTS company_sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    company_id UUID NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,

    discovery_run_id UUID NOT NULL
        REFERENCES discovery_runs(id) ON DELETE RESTRICT,

    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    source_query TEXT NOT NULL,

    title TEXT,
    snippet TEXT,
    published_date TEXT,

    source_score REAL
        CHECK (
            source_score IS NULL
            OR source_score BETWEEN 0 AND 1
        ),

    source_status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (
            source_status IN (
                'candidate',
                'likely_official',
                'needs_review',
                'rejected'
            )
        ),

    extraction_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (
            extraction_status IN (
                'not_requested',
                'extracted',
                'failed'
            )
        ),

    extracted_content TEXT,
    content_sha256 TEXT,

    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, discovery_run_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_company_sources_company
    ON company_sources (company_id, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_company_sources_run
    ON company_sources (discovery_run_id);

CREATE INDEX IF NOT EXISTS idx_company_sources_domain
    ON company_sources (source_domain);



-- Phase 2.1: Unified website-aware discovery

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS identity_key TEXT;

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS website_status TEXT
NOT NULL DEFAULT 'unknown';

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS location_text TEXT;

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

ALTER TABLE companies
ADD COLUMN IF NOT EXISTS phone TEXT;


UPDATE companies
SET identity_key = 'domain:' || normalized_domain
WHERE identity_key IS NULL
  AND normalized_domain IS NOT NULL;

UPDATE companies
SET website_status = 'with_website'
WHERE normalized_domain IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_identity_key
ON companies(identity_key)
WHERE identity_key IS NOT NULL;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'companies_website_status_check'
    ) THEN
        ALTER TABLE companies
        ADD CONSTRAINT companies_website_status_check
        CHECK (
            website_status IN (
                'with_website',
                'without_website_candidate',
                'unknown'
            )
        );
    END IF;
END;
$$;


-- Identify the origin of every evidence source.
ALTER TABLE company_sources
ADD COLUMN IF NOT EXISTS source_type TEXT
NOT NULL DEFAULT 'official_website';

ALTER TABLE company_sources
ADD COLUMN IF NOT EXISTS metadata JSONB
NOT NULL DEFAULT '{}'::jsonb;


-- Cache public OpenStreetMap responses to avoid repeated requests.
CREATE TABLE IF NOT EXISTS osm_cache (
    cache_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_osm_cache_fetched
ON osm_cache(fetched_at);