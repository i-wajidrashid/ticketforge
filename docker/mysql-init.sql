-- Local development bootstrap only.
-- Production uses a least-privilege user provisioned by the platform.
-- Two logical databases, one per service owner:
--   core     -> Django owns the schema (migrations)
--   checkout -> FastAPI owns the schema (Alembic)
CREATE DATABASE IF NOT EXISTS core CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS checkout CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'ticketforge'@'%' IDENTIFIED BY 'ticketforge';
GRANT ALL PRIVILEGES ON core.* TO 'ticketforge'@'%';
GRANT ALL PRIVILEGES ON checkout.* TO 'ticketforge'@'%';
-- The test suite creates test_core / test_checkout databases at runtime.
GRANT ALL PRIVILEGES ON `test_core`.* TO 'ticketforge'@'%';
GRANT ALL PRIVILEGES ON `test_checkout`.* TO 'ticketforge'@'%';
GRANT CREATE, DROP, ALTER ON *.* TO 'ticketforge'@'%';
FLUSH PRIVILEGES;
