# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Pre-flight lock check for NCE Sync.

Before every sync run we query both databases for transactions and lock
waits that could cause ``1205 Lock wait timeout exceeded``:

* Frappe DB — uses ``frappe.db.sql``.
* WordPress DB — opens a short PyMySQL connection via ``wp_connection``.

Any transaction older than ``LOCK_AGE_THRESHOLD_S`` that is holding row
or table locks, plus any active row-lock wait, is reported back to the
user in a copyable msgprint dialog and the sync aborts before doing any
work. This converts what was previously a confusing 1205 traceback into
an actionable "kill thread N on database X" instruction.
"""

from __future__ import annotations

import frappe
from frappe import _

from nce_sync.utils.connections import wp_connection

# Open transactions older than this many seconds AND holding locks are
# treated as stale blockers. Anything younger is ignored as normal app
# write activity.
LOCK_AGE_THRESHOLD_S = 60


_TRX_SQL = """
SELECT trx_mysql_thread_id AS pid,
       trx_started,
       trx_state,
       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS age_s,
       trx_rows_locked,
       trx_tables_locked,
       SUBSTRING(COALESCE(trx_query, ''), 1, 120) AS q
FROM information_schema.innodb_trx
WHERE (trx_rows_locked > 0 OR trx_tables_locked > 0)
  AND TIMESTAMPDIFF(SECOND, trx_started, NOW()) >= %s
ORDER BY trx_started
"""

_LOCK_WAITS_SQL = """
SELECT r.trx_mysql_thread_id AS waiting_pid,
       SUBSTRING(COALESCE(r.trx_query, ''), 1, 120) AS waiting_q,
       b.trx_mysql_thread_id AS blocking_pid,
       TIMESTAMPDIFF(SECOND, b.trx_started, NOW()) AS blocking_age_s,
       SUBSTRING(COALESCE(b.trx_query, ''), 1, 120) AS blocking_q
FROM information_schema.innodb_lock_waits w
JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
"""


def check_locks(wp_conn_doc) -> dict:
	"""
	Run lock diagnostics on both Frappe and WordPress databases.

	Returns:
		dict with keys ``frappe_trx``, ``frappe_waits``, ``wp_trx``,
		``wp_waits``, ``frappe_error``, ``wp_error``. Each ``*_trx`` /
		``*_waits`` value is a list of dicts (possibly empty).
	"""
	report = {
		"frappe_trx": [],
		"frappe_waits": [],
		"wp_trx": [],
		"wp_waits": [],
		"frappe_error": None,
		"wp_error": None,
	}

	# --- Frappe DB ---
	try:
		report["frappe_trx"] = (
			frappe.db.sql(_TRX_SQL, (LOCK_AGE_THRESHOLD_S,), as_dict=True) or []
		)
		report["frappe_waits"] = frappe.db.sql(_LOCK_WAITS_SQL, as_dict=True) or []
	except Exception as e:
		report["frappe_error"] = str(e)

	# --- WordPress DB ---
	try:
		with wp_connection(wp_conn_doc) as conn:
			cursor = conn.cursor()
			try:
				cursor.execute(_TRX_SQL, (LOCK_AGE_THRESHOLD_S,))
				report["wp_trx"] = list(cursor.fetchall())
				cursor.execute(_LOCK_WAITS_SQL)
				report["wp_waits"] = list(cursor.fetchall())
			finally:
				cursor.close()
	except Exception as e:
		report["wp_error"] = str(e)

	return report


def has_blockers(report: dict) -> bool:
	"""True if the report contains any stale locks or active waits."""
	return bool(
		report.get("frappe_trx")
		or report.get("frappe_waits")
		or report.get("wp_trx")
		or report.get("wp_waits")
	)


def _render_table(rows, title, cols):
	if not rows:
		return ""
	esc = frappe.utils.escape_html
	html = [
		f"<p style='margin:8px 0 4px;font-weight:600'>{esc(title)}</p>",
		"<table border='1' cellspacing='0' cellpadding='4' "
		"style='border-collapse:collapse;font-size:11px;font-family:ui-monospace,monospace'>",
		"<tr>",
	]
	html.extend(f"<th style='background:#f5f5f5;text-align:left'>{esc(c)}</th>" for c in cols)
	html.append("</tr>")
	for row in rows:
		html.append("<tr>")
		for c in cols:
			val = row.get(c) if isinstance(row, dict) else ""
			html.append(f"<td>{esc('' if val is None else str(val))}</td>")
		html.append("</tr>")
	html.append("</table>")
	return "".join(html)


def format_report_html(report: dict, frappe_doctype: str, wp_table_name: str) -> str:
	"""Format the report as HTML suitable for ``frappe.publish_realtime('msgprint', ...)``."""
	parts = [
		"<p><strong>Pre-flight lock check found blockers.</strong></p>",
		(
			f"<p>One or more transactions are holding locks that will block the sync of "
			f"<code>{frappe.utils.escape_html(frappe_doctype)}</code> "
			f"(WP table <code>{frappe.utils.escape_html(wp_table_name)}</code>). "
			f"The sync has been aborted. Resolve the blockers below and retry.</p>"
		),
	]

	trx_cols = ["pid", "age_s", "trx_rows_locked", "trx_tables_locked", "trx_state", "q"]
	wait_cols = ["waiting_pid", "blocking_pid", "blocking_age_s", "blocking_q"]

	parts.append(_render_table(report.get("frappe_trx") or [],
		"Frappe DB — stale transactions holding locks", trx_cols))
	parts.append(_render_table(report.get("frappe_waits") or [],
		"Frappe DB — current row-lock waits", wait_cols))
	parts.append(_render_table(report.get("wp_trx") or [],
		"WordPress DB — stale transactions holding locks", trx_cols))
	parts.append(_render_table(report.get("wp_waits") or [],
		"WordPress DB — current row-lock waits", wait_cols))

	for key, label in (("frappe_error", "Frappe DB check error"),
	                   ("wp_error", "WordPress DB check error")):
		if report.get(key):
			parts.append(
				f"<p style='color:#b85c00'><em>{label}:</em> "
				f"{frappe.utils.escape_html(report[key])}</p>"
			)

	parts.append(
		"<p style='margin-top:12px'><strong>To clear a blocker:</strong><br>"
		"&nbsp;&nbsp;<code>CALL mysql.rds_kill(&lt;pid&gt;);</code> on the relevant DB.<br>"
		"&nbsp;&nbsp;If the holder is a Frappe worker, also run "
		"<code>sudo supervisorctl restart frappe-bench-workers:*</code> "
		"so its pooled connections are dropped.</p>"
	)
	return "\n".join(p for p in parts if p)


def preflight(wp_table_doc) -> tuple[bool, dict, str | None]:
	"""
	Run the pre-flight lock check.

	Returns:
		(ok, report, html). ``ok`` is True when no blockers were found.
		``html`` is the rendered dialog body when ``ok`` is False, else None.
	"""
	wp_conn_doc = frappe.get_single("WordPress Connection")
	report = check_locks(wp_conn_doc)
	if not has_blockers(report):
		return True, report, None
	html = format_report_html(
		report,
		wp_table_doc.frappe_doctype or "",
		wp_table_doc.table_name or "",
	)
	return False, report, html
