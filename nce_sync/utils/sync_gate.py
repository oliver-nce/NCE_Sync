# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Sync gate: block user saves on mirrored DocTypes while a WP→Frappe sync is running.

Three cases for any write to a mirrored DocType:
  1. frappe.flags.in_sync is set  → sync process itself is writing; allow, skip write-back.
  2. Flag not set AND DocType is busy → user edit during sync; frappe.throw so no save occurs.
  3. Flag not set AND not busy        → normal user edit; allow save, write-back will be enqueued.

enforce_sync_gate (validate hook) covers all save paths including Submit.
Desk-specific JS is intentionally absent — frappe.throw renders natively in every client.
"""

import frappe
from frappe import _

from nce_sync.utils.constants import CRUD_LOCK_TTL_SEC

BUSY_KEY_PREFIX = "nce_sync:sync_busy:"

# ---------------------------------------------------------------------------
# CRUD-lock layer (dialog write-back + readback ops)
# ---------------------------------------------------------------------------
# A *separate* lock namespace from the sync-busy gate above. The dialog claims
# CRUD-locks on all its DocTypes at submit; syncs stand down while they are held.
# It is deliberately distinct from the sync-busy key so that ``enforce_sync_gate``
# (which reads only sync-busy) does not block the dialog's own save.

CRUD_LOCK_PREFIX = "nce_sync:crud_lock:"

#: Release the key only if the caller still owns it (avoids deleting a lock that
#: expired and was re-claimed by someone else).
_RELEASE_IF_OWNER_LUA = (
	"if redis.call('get', KEYS[1]) == ARGV[1] then "
	"return redis.call('del', KEYS[1]) else return 0 end"
)


def _busy_key(frappe_doctype: str) -> str:
	return f"{BUSY_KEY_PREFIX}{frappe_doctype}"


def _crud_key(frappe_doctype: str) -> str:
	return f"{CRUD_LOCK_PREFIX}{frappe_doctype}"


def _norm_dts(doctypes) -> list:
	return sorted({d for d in (doctypes or []) if d})


def mark_doctype_syncing(frappe_doctype: str | None) -> None:
	"""Set the Redis busy flag for a DocType at sync start."""
	if not frappe_doctype:
		return
	frappe.cache().set_value(_busy_key(frappe_doctype), 1, expires_in_sec=900)


def clear_doctype_syncing(frappe_doctype: str | None) -> None:
	"""Clear the Redis busy flag for a DocType at sync end."""
	if not frappe_doctype:
		return
	frappe.cache().delete_value(_busy_key(frappe_doctype))


def is_doctype_syncing(doctype: str | None) -> bool:
	"""True if a WP→Frappe sync is currently running for this DocType."""
	if not doctype:
		return False
	return frappe.cache().get_value(_busy_key(doctype)) is not None


def enforce_sync_gate(doc, method=None):
	"""
	Wildcard validate hook.

	Allow the write if called by the sync process (frappe.flags.in_sync).
	Throw for every other write while the DocType is sync-busy.
	"""
	if getattr(frappe.flags, "in_sync", False):
		return
	if not doc.doctype:
		return
	if not is_doctype_syncing(doc.doctype):
		return
	frappe.throw(
		_("Sync in progress — wait for it to finish, then save again."),
		title=_("Sync in progress"),
	)


# ---------------------------------------------------------------------------
# CRUD-lock API
# ---------------------------------------------------------------------------


def crud_lock_holder(frappe_doctype: str | None):
	"""Return the owner currently holding the CRUD-lock on a DocType, or None."""
	if not frappe_doctype:
		return None
	cache = frappe.cache()
	val = cache.get(cache.make_key(_crud_key(frappe_doctype)))
	if val is None:
		return None
	return val.decode() if isinstance(val, bytes) else val


def is_crud_locked(frappe_doctype: str | None) -> bool:
	"""True if a dialog CRUD op currently holds this DocType."""
	return crud_lock_holder(frappe_doctype) is not None


def _release_crud_key(cache, dt: str, owner: str) -> None:
	key = cache.make_key(_crud_key(dt))
	try:
		cache.eval(_RELEASE_IF_OWNER_LUA, 1, key, owner)
	except Exception:
		# Fallback: best-effort owner-checked delete.
		try:
			if crud_lock_holder(dt) == owner:
				cache.delete(key)
		except Exception:
			pass


def acquire_crud_lock(doctypes, owner: str, ttl: int | None = None):
	"""
	Atomically claim CRUD-locks on every DocType in ``doctypes`` for ``owner``.

	All-or-nothing: if any table is already held by a *different* owner, any
	locks claimed in this call are rolled back and ``(False, conflict_dt)`` is
	returned. After claiming, it re-checks the sync-busy gate on each table and
	backs off if a sync grabbed one meanwhile — this closes the sync-vs-dialog
	start race (both sides claim-then-recheck-the-other, so they can never both
	proceed).

	Returns ``(True, None)`` on success, ``(False, conflict_dt)`` on conflict.
	"""
	ttl = ttl or CRUD_LOCK_TTL_SEC
	cache = frappe.cache()
	dts = _norm_dts(doctypes)
	newly = []
	for dt in dts:
		got = cache.set(cache.make_key(_crud_key(dt)), owner, nx=True, ex=ttl)
		if got:
			newly.append(dt)
		elif crud_lock_holder(dt) == owner:
			continue  # already ours — idempotent
		else:
			for d in newly:
				_release_crud_key(cache, d, owner)
			return False, dt

	# set-then-check: bow out if a sync is (now) running on any of these tables.
	for dt in dts:
		if is_doctype_syncing(dt):
			for d in newly:
				_release_crud_key(cache, d, owner)
			return False, dt

	return True, None


def release_crud_lock(doctypes, owner: str) -> None:
	"""Release CRUD-locks on ``doctypes`` that are still owned by ``owner``."""
	cache = frappe.cache()
	for dt in _norm_dts(doctypes):
		_release_crud_key(cache, dt, owner)
