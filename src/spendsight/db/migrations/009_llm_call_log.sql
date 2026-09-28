-- Usage log for every LLM request (SPEC.md §7): token counts and outcome, never content.

CREATE SEQUENCE seq_llm_calls START 1;

CREATE TABLE llm_calls (
    id             INTEGER PRIMARY KEY DEFAULT nextval('seq_llm_calls'),
    created_at     TIMESTAMP NOT NULL DEFAULT current_timestamp,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt         TEXT NOT NULL,            -- e.g. 'merchant_normalize'
    prompt_version INTEGER NOT NULL,
    cache_hit      BOOLEAN NOT NULL,
    ok             BOOLEAN NOT NULL,
    error          TEXT,                     -- short error class only, never payloads
    input_tokens   INTEGER,
    output_tokens  INTEGER
);
