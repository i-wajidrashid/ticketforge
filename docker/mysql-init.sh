#!/bin/bash
# First-start bootstrap for the MySQL container (runs only on an empty data
# directory). The image itself has already created MYSQL_DATABASE (core) and
# MYSQL_USER with MYSQL_PASSWORD from .env; this adds the second schema and
# the grants, using the same variables so nothing here is hard-coded.
#
#   core      Django owns the schema (migrations)
#   checkout  FastAPI owns the schema (Alembic)
#
# MYSQL_TEST_GRANTS=1 (the default) also lets the user create the test_*
# databases the test suite uses. The production overlay sets it to 0, so the
# application user gets rights on core and checkout and nothing else.
set -euo pipefail

core="${MYSQL_DATABASE:-core}"
checkout="${CHECKOUT_DATABASE:-checkout}"
user="${MYSQL_USER:?MYSQL_USER must be set}"

sql="
CREATE DATABASE IF NOT EXISTS \`${core}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS \`${checkout}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON \`${core}\`.* TO '${user}'@'%';
GRANT ALL PRIVILEGES ON \`${checkout}\`.* TO '${user}'@'%';
"
if [ "${MYSQL_TEST_GRANTS:-1}" = "1" ]; then
  sql+="
GRANT ALL PRIVILEGES ON \`test_core\`.* TO '${user}'@'%';
GRANT ALL PRIVILEGES ON \`test_checkout\`.* TO '${user}'@'%';
GRANT CREATE, DROP, ALTER ON *.* TO '${user}'@'%';
"
fi

# MYSQL_HOST and MYSQL_PORT in .env are for the application; the mysql client
# would read them too, so drop them and use the init-time socket.
env -u MYSQL_HOST -u MYSQL_PORT mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<<"${sql}
FLUSH PRIVILEGES;"
