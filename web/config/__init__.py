"""Django project configuration.

PyMySQL is a pure-Python driver, which keeps the container image slim and makes
the project installable on any machine without a C toolchain. We advertise a
compatible mysqlclient version so Django's version gate accepts it.
"""

from __future__ import annotations

import pymysql

pymysql.version_info = (2, 2, 2, "final", 0)
pymysql.install_as_MySQLdb()
