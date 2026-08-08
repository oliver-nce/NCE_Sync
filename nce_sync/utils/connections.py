# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
WordPress database connection helpers.

Provides both a plain ``get_wp_connection()`` factory and a
``wp_connection()`` context manager that guarantees the connection is
closed even when an exception is raised.

Usage::

    from nce_sync.utils.connections import wp_connection

    with wp_connection(wp_conn_doc) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        ...
    # conn is automatically closed here
"""

from contextlib import contextmanager

import frappe
import pymysql

from nce_sync.utils.constants import (
	WP_CONNECT_TIMEOUT_SEC,
	WP_READ_TIMEOUT_SEC,
	WP_WRITE_TIMEOUT_SEC,
)


def get_wp_connection(wp_conn_doc):
	"""
	Establish a PyMySQL connection to the WordPress database.

	Args:
		wp_conn_doc: WordPress Connection document (must have host, port,
		             username, password, database fields).

	Returns:
		pymysql.Connection

	Raises:
		Exception: if the connection cannot be established.
	"""
	try:
		conn = pymysql.connect(
			host=wp_conn_doc.host,
			port=wp_conn_doc.port or 3306,
			user=wp_conn_doc.username,
			password=wp_conn_doc.get_password("password"),
			database=wp_conn_doc.database,
			charset="utf8mb4",
			cursorclass=pymysql.cursors.DictCursor,
			autocommit=True,
			# Socket timeouts so a network blip to the WP DB fails fast instead
			# of blocking a worker indefinitely (the "stuck for hours" case).
			connect_timeout=WP_CONNECT_TIMEOUT_SEC,
			read_timeout=WP_READ_TIMEOUT_SEC,
			write_timeout=WP_WRITE_TIMEOUT_SEC,
		)
		return conn
	except Exception as e:
		frappe.log_error(title="WordPress Connection Error", message=str(e))
		raise


@contextmanager
def wp_connection(wp_conn_doc):
	"""
	Context manager that yields a PyMySQL connection and closes it on exit.

	Usage::

		with wp_connection(wp_conn_doc) as conn:
			cursor = conn.cursor()
			...
	"""
	conn = get_wp_connection(wp_conn_doc)
	try:
		yield conn
		try:
			conn.commit()
		except Exception:
			pass
	except Exception:
		try:
			conn.rollback()
		except Exception:
			pass
		raise
	finally:
		try:
			conn.close()
		except Exception:
			pass
