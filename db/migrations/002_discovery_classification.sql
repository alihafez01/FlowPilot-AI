BEGIN;

-- Distinguish real business records from historical category/listicle pages.
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS record_type TEXT NOT NULL DEFAULT 'business';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'companies_record_type_check'
          AND conrelid = 'companies'::regclass
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT companies_record_type_check
            CHECK (record_type IN ('business', 'discovery_page'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_companies_record_type_created
    ON companies (record_type, created_at DESC);

-- Persist category pages independently. These are discovery inputs, not leads.
CREATE TABLE IF NOT EXISTS discovery_pages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discovery_run_id UUID NOT NULL
        REFERENCES discovery_runs(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    source_query TEXT NOT NULL,
    title TEXT,
    snippet TEXT,
    published_date TEXT,
    source_score REAL
        CHECK (source_score IS NULL OR source_score BETWEEN 0 AND 1),
    page_type TEXT NOT NULL
        CHECK (page_type IN ('discovery_page', 'directory_listing')),
    extraction_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (extraction_status IN ('not_requested', 'extracted', 'failed')),
    extracted_content TEXT,
    content_sha256 TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (discovery_run_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_discovery_pages_run
    ON discovery_pages (discovery_run_id);

CREATE INDEX IF NOT EXISTS idx_discovery_pages_domain
    ON discovery_pages (source_domain, last_seen_at DESC);

ALTER TABLE discovery_runs
    ADD COLUMN IF NOT EXISTS discovery_pages_saved INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'discovery_runs_discovery_pages_saved_check'
          AND conrelid = 'discovery_runs'::regclass
    ) THEN
        ALTER TABLE discovery_runs
            ADD CONSTRAINT discovery_runs_discovery_pages_saved_check
            CHECK (discovery_pages_saved >= 0);
    END IF;
END
$$;

-- Expand the source taxonomy. Existing data currently uses only the first
-- and last values; the others are introduced for website/discovery triage.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'company_sources_source_type_check'
          AND conrelid = 'company_sources'::regclass
    ) THEN
        ALTER TABLE company_sources
            ADD CONSTRAINT company_sources_source_type_check
            CHECK (
                source_type IN (
                    'official_website',
                    'website_candidate',
                    'directory_listing',
                    'discovery_page',
                    'osm_listing'
                )
            );
    END IF;
END
$$;

-- The following domains/paths were verified from the current discovery
-- output as category/listicle sources, not individual business websites.
UPDATE company_sources
SET source_type = 'discovery_page',
    source_status = 'needs_review',
    metadata = COALESCE(metadata, '{}'::jsonb)
        || jsonb_build_object(
            'classification', 'discovery_page',
            'classification_note', 'Migrated from legacy official_website classification'
        ),
    last_seen_at = now()
WHERE source_type = 'official_website'
  AND (
        source_domain IN (
            'agencies.semrush.com',
            'digitalagencynetwork.com',
            'builtinaustin.com',
            'themanifest.com'
        )
        OR (
            source_domain = 'inbeat.agency'
            AND source_url ILIKE '%/blog/%'
        )
  );

-- A source that has not passed the existing likely_official heuristic remains
-- a website candidate; it is not counted as official evidence.
UPDATE company_sources
SET source_type = 'website_candidate',
    metadata = COALESCE(metadata, '{}'::jsonb)
        || jsonb_build_object(
            'classification', 'website_candidate',
            'classification_note', 'Legacy source had not reached likely_official status'
        )
WHERE source_type = 'official_website'
  AND source_status <> 'likely_official';

-- Preserve legacy directory-page observations in their own table. Keep the
-- original company_sources rows as an audit/history trail.
INSERT INTO discovery_pages (
    discovery_run_id,
    source_url,
    source_domain,
    source_query,
    title,
    snippet,
    published_date,
    source_score,
    page_type,
    extraction_status,
    extracted_content,
    content_sha256,
    metadata,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (s.discovery_run_id, s.source_url)
    s.discovery_run_id,
    s.source_url,
    s.source_domain,
    s.source_query,
    s.title,
    s.snippet,
    s.published_date,
    s.source_score,
    CASE
        WHEN s.source_url ~* '/(profile|company|agency)/[^/]+/?$'
            THEN 'directory_listing'
        ELSE 'discovery_page'
    END,
    s.extraction_status,
    s.extracted_content,
    s.content_sha256,
    COALESCE(s.metadata, '{}'::jsonb)
        || jsonb_build_object('migrated_from_company_id', s.company_id::text),
    s.first_seen_at,
    s.last_seen_at
FROM company_sources s
WHERE s.source_type = 'discovery_page'
ORDER BY s.discovery_run_id, s.source_url, s.last_seen_at DESC, s.id DESC
ON CONFLICT (discovery_run_id, source_url)
DO UPDATE SET
    title = EXCLUDED.title,
    snippet = EXCLUDED.snippet,
    source_score = EXCLUDED.source_score,
    extraction_status = EXCLUDED.extraction_status,
    extracted_content = COALESCE(
        EXCLUDED.extracted_content,
        discovery_pages.extracted_content
    ),
    content_sha256 = COALESCE(
        EXCLUDED.content_sha256,
        discovery_pages.content_sha256
    ),
    last_seen_at = GREATEST(discovery_pages.last_seen_at, EXCLUDED.last_seen_at);

-- Keep the old company rows for referential integrity/audit, but stop treating
-- known category pages as businesses or websites.
UPDATE companies c
SET record_type = 'discovery_page',
    website_url = NULL,
    normalized_domain = NULL,
    website_status = 'unknown',
    website_validation_status = 'needs_review',
    identity_key = 'discovery_page:' || c.id::text,
    updated_at = now()
WHERE EXISTS (
    SELECT 1
    FROM company_sources s
    WHERE s.company_id = c.id
      AND s.source_type = 'discovery_page'
      AND s.source_domain IN (
          'agencies.semrush.com',
          'digitalagencynetwork.com',
          'builtinaustin.com',
          'themanifest.com',
          'inbeat.agency'
      )
);

UPDATE discovery_runs dr
SET discovery_pages_saved = page_counts.total
FROM (
    SELECT discovery_run_id, COUNT(*)::INTEGER AS total
    FROM discovery_pages
    GROUP BY discovery_run_id
) AS page_counts
WHERE dr.id = page_counts.discovery_run_id;

COMMIT;
