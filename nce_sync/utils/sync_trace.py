# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Step timing trace for queued NCE_Sync WP jobs (optional ``debug`` flag)."""

from __future__ import annotations

import time
from html import escape as html_escape

import frappe


def truthy_debug(value) -> bool:
	"""True for whitelist args: ``1``, ``"1"``, ``true``, ``True``."""
	if value is None or value == "":
		return False
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	from frappe.utils import cint

	return bool(cint(value))


class SyncTrace:
	"""Accumulate ``elapsed_ms | message`` lines; log to stderr logger; optionally push a Desk ``msgprint``."""

	_MAX_PUBLISH_CHARS = 100_000

	def __init__(self, enabled: bool, job_kind: str, label: str = ""):
		self.enabled = enabled
		self.job_kind = job_kind
		self.label = label or ""
		self._t0 = time.perf_counter()
		self.lines: list[str] = []

	def step(self, msg: str) -> None:
		if not self.enabled:
			return
		elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
		line = f"{elapsed_ms:9.2f} ms | {msg}"
		self.lines.append(line)
		frappe.logger("nce_sync.sync_trace").info("%s | %s", self.job_kind, line)

	def publish_dialog(self, user: str, title: str, extra_footer: str = "", indicator: str = "blue") -> None:
		if not self.enabled or not user:
			return
		body = "\n".join(self.lines)
		if extra_footer:
			body = f"{body}\n\n---\n{extra_footer}"
		if len(body) > self._MAX_PUBLISH_CHARS:
			body = body[: self._MAX_PUBLISH_CHARS] + "\n...[truncated]"
		header = f"{self.job_kind}"
		if self.label:
			header = f"{header} · {self.label}"
		safe = html_escape(body)
		html = (
			f"<p><strong>{html_escape(title)}</strong><br><code>{html_escape(header)}</code></p>"
			f"<pre style='max-height:55vh;overflow:auto;white-space:pre-wrap;font-size:11px;"
			f"user-select:all;font-family:ui-monospace,monospace'>{safe}</pre>"
			f"<p class='text-muted' style='font-size:11px'>Select the log above and copy (Ctrl/Cmd+A, then C). "
			f"Also check <strong>bench</strong> worker log for logger <code>nce_sync.sync_trace</code>.</p>"
		)
		frappe.publish_realtime(
			"msgprint",
			{"message": html, "indicator": indicator, "alert": True, "wide": True},
			user=user,
		)
