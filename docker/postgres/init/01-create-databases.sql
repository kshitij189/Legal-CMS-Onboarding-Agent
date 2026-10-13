-- Runs once when the Postgres volume is first initialised.
CREATE DATABASE mock_cms;
CREATE DATABASE mock_cms_test;
CREATE DATABASE onboarding;
CREATE DATABASE onboarding_test;
-- Isolated mock CMS database for backend browser end-to-end tests (Phase 4).
CREATE DATABASE mock_cms_e2e;
-- Evaluation suite database (Phase 6), recreated from migrations on every run.
CREATE DATABASE onboarding_eval;
