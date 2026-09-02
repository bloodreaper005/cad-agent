CREATE TABLE knowledge_schema_migrations (
    version integer PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    sha256 text NOT NULL CHECK (length(sha256) = 64),
    applied_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE product_families (
    id text PRIMARY KEY CHECK (trim(id) <> ''),
    organization_id text NOT NULL CHECK (trim(organization_id) <> ''),
    design_group_id text NOT NULL CHECK (trim(design_group_id) <> ''),
    canonical_name text NOT NULL CHECK (trim(canonical_name) <> ''),
    aliases text NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),
    profile text NOT NULL DEFAULT '{}' CHECK (json_valid(profile)),
    search_terms text NOT NULL DEFAULT '[]' CHECK (json_valid(search_terms)),
    search_text text NOT NULL CHECK (trim(search_text) <> ''),
    search_blob text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'revoked')),
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (organization_id, design_group_id, id)
);

CREATE TABLE knowledge_assertions (
    id text PRIMARY KEY CHECK (trim(id) <> ''),
    organization_id text NOT NULL CHECK (trim(organization_id) <> ''),
    design_group_id text NOT NULL CHECK (trim(design_group_id) <> ''),
    product_family_id text,
    subject text NOT NULL CHECK (trim(subject) <> ''),
    predicate text NOT NULL CHECK (trim(predicate) <> ''),
    object_value text NOT NULL CHECK (json_valid(object_value)),
    applicability text NOT NULL DEFAULT '{}' CHECK (json_valid(applicability)),
    evidence text NOT NULL DEFAULT '[]' CHECK (json_valid(evidence)),
    search_terms text NOT NULL DEFAULT '[]' CHECK (json_valid(search_terms)),
    search_text text NOT NULL CHECK (trim(search_text) <> ''),
    search_blob text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'revoked')),
    supersedes_id text,
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (organization_id, design_group_id, id),
    FOREIGN KEY (organization_id, design_group_id, product_family_id)
        REFERENCES product_families(organization_id, design_group_id, id),
    FOREIGN KEY (organization_id, design_group_id, supersedes_id)
        REFERENCES knowledge_assertions(organization_id, design_group_id, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);

CREATE TABLE design_lessons (
    id text PRIMARY KEY CHECK (trim(id) <> ''),
    organization_id text NOT NULL CHECK (trim(organization_id) <> ''),
    design_group_id text NOT NULL CHECK (trim(design_group_id) <> ''),
    product_family_id text,
    content text NOT NULL CHECK (json_valid(content)),
    applicability text NOT NULL DEFAULT '{}' CHECK (json_valid(applicability)),
    provenance text NOT NULL DEFAULT '{}' CHECK (json_valid(provenance)),
    search_terms text NOT NULL DEFAULT '[]' CHECK (json_valid(search_terms)),
    search_text text NOT NULL CHECK (trim(search_text) <> ''),
    search_blob text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'revoked')),
    supersedes_id text,
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (organization_id, design_group_id, id),
    FOREIGN KEY (organization_id, design_group_id, product_family_id)
        REFERENCES product_families(organization_id, design_group_id, id),
    FOREIGN KEY (organization_id, design_group_id, supersedes_id)
        REFERENCES design_lessons(organization_id, design_group_id, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);

CREATE INDEX product_families_scope_idx
    ON product_families(organization_id, design_group_id, status);

CREATE INDEX knowledge_assertions_scope_idx
    ON knowledge_assertions(organization_id, design_group_id, product_family_id, status);

CREATE INDEX design_lessons_scope_idx
    ON design_lessons(organization_id, design_group_id, product_family_id, status);

CREATE INDEX design_lessons_review_idx
    ON design_lessons(organization_id, design_group_id,
                      json_extract(provenance, '$.source_review_sha256'));
