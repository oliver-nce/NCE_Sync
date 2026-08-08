# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Pre-flight stuck-task guard.

Runs as the FIRST step of every NCE_Sync / NCE_Events background sync job. It
finds background jobs belonging to our two apps that are genuinely stuck and
reaps them so the incoming sync starts from a clean slate. Without this, a
single hung job (e.g. a WordPress socket read that never returns) can occupy a
worker for hours and silently stop *all* WP exchanges.

Two questions this module answers
---------------------------------
1. Is a job *ours* (relevant to these two apps)?
   → Its RQ dotted method path (``func_name``) starts with one of APP_PREFIXES.
     The queue name is not used (``default`` is shared site-wide) and the
     job_id is not used (write-back jobs use random UUIDs). The module path is
     the only stable identifier.

2. Is a job *truly stuck* (not merely queued or briefly busy)?
   → It is actually executing (present in RQ's StartedJobRegistry) AND one of:
     - it has run longer than the hard cap (MAX_SYNC_JOB_RUNTIME_SEC), for the
       unit WP-exchange jobs (orchestrators/exports are exempt from this), OR
     - it is an orphan/zombie: its owning worker is gone or its heartbeat is
       older than WORKER_STALE_SEC (the classic "stuck for hours, silent" case).

Reaping (per approved design A/A/A)
-----------------------------------
- If a *live* worker still holds the job, send RQ's stop-job command to kill the
  work-horse.
- Evict the job from the Started registry and mark it failed.
- Clear any orphaned sync-busy gate for the DocType the job was working on.
- Write an Error Log entry describing what was reaped and why.
The guard never raises into the caller — a guard failure must not block a sync.
"""

from datetime import datetime, timezone

import frappe

from nce_sync.utils.constants import MAX_SYNC_JOB_RUNTIME_SEC, WORKER_STALE_SEC
from nce_sync.utils.sync_gate import clear_doctype_syncing

#: Module-path prefixes that mark a job as belonging to our two apps.
APP_PREFIXES = ("nce_sync.", "nce_events.")

#: Our jobs that are orchestrators / long batch exports rather than a single
#: WP exchange. These are exempt from the runtime cap (a scheduled batch of many
#: tables can legitimately exceed 5 min); they are still reaped if their worker
#: is dead/zombie.
TIME_CAP_EXEMPT = frozenset(
	{
		"nce_sync.utils.data_sync.run_scheduled_syncs",
		"nce_sync.api._build_excel_file",
	}
)


# ---------------------------------------------------------------------------
# Redis / RQ helpers
# ---------------------------------------------------------------------------


def _get_redis_conn():
	from frappe.utils.background_jobs import get_redis_conn

	return get_redis_conn()


def _current_job_id():
	"""ID of the job we are running inside, so the guard never reaps itself."""
	try:
		from rq import get_current_job

		job = get_current_job()
		return job.id if job else None
	except Exception:
		return None


def _as_utc(dt):
	"""Normalise an rq datetime (may be naive UTC) to timezone-aware UTC."""
	if dt is None:
		return None
	if dt.tzinfo is None:
		return dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc)


def _live_worker_heartbeats(conn):
	"""
	Return (heartbeats, workers_known).

	heartbeats: map of worker name → last-heartbeat (UTC).
	workers_known: True only if the worker roster was read successfully. When
	False, callers MUST NOT use the zombie signal (otherwise a transient
	enumeration failure would flag every healthy job as an orphan); they fall
	back to the runtime cap alone.
	"""
	from rq.worker import Worker

	heartbeats = {}
	try:
		for w in Worker.all(connection=conn):
			heartbeats[w.name] = _as_utc(getattr(w, "last_heartbeat", None))
		return heartbeats, True
	except Exception:
		return heartbeats, False


def _derive_doctype(job):
	"""Best-effort: which Frappe DocType was this job working on (for gate clear)?"""
	try:
		kwargs = job.kwargs or {}
		if kwargs.get("doctype"):
			return kwargs["doctype"]
		wp_table_name = kwargs.get("wp_table_name")
		if wp_table_name:
			return frappe.db.get_value("WP Tables", wp_table_name, "frappe_doctype")

		args = job.args or ()
		func = job.func_name or ""
		# run_write_back_for_doc(wp_table_name, doctype, docname)
		if func.endswith("run_write_back_for_doc") and len(args) >= 2:
			return args[1]
		# run_sync_doctype_rows_job(doctype, names, ...) / linked(doctype, ...)
		if (
			func.endswith("run_sync_doctype_rows_job")
			or func.endswith("run_sync_linked_doctype_rows_job")
		) and len(args) >= 1:
			return args[0]
	except Exception:
		return None
	return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def find_stuck_jobs():
	"""
	Return a list of stuck jobs belonging to our apps.

	Each entry is a dict: job, job_id, func_name, queue_name, worker_name,
	runtime_sec, is_zombie, reason. Read-only — performs no remediation.
	"""
	from rq import Queue
	from rq.job import Job
	from rq.registry import StartedJobRegistry

	conn = _get_redis_conn()
	now_utc = datetime.now(timezone.utc)
	current_id = _current_job_id()
	heartbeats, workers_known = _live_worker_heartbeats(conn)

	stuck = []
	for q in Queue.all(connection=conn):
		try:
			registry = StartedJobRegistry(queue=q)
			job_ids = registry.get_job_ids()
		except Exception:
			continue

		for jid in job_ids:
			if jid == current_id:
				continue
			try:
				job = Job.fetch(jid, connection=conn)
			except Exception:
				continue

			func_name = job.func_name or ""
			if not func_name.startswith(APP_PREFIXES):
				continue

			started = _as_utc(job.started_at)
			if not started:
				continue
			runtime_sec = (now_utc - started).total_seconds()

			# Zombie detection only when the worker roster is trustworthy AND the
			# job names an owning worker. Otherwise rely on the runtime cap so a
			# transient enumeration hiccup never reaps a healthy job.
			worker_name = getattr(job, "worker_name", None)
			is_zombie = False
			if workers_known and worker_name:
				worker_hb = heartbeats.get(worker_name)
				is_zombie = (
					worker_hb is None
					or (now_utc - worker_hb).total_seconds() > WORKER_STALE_SEC
				)

			over_cap = (
				func_name not in TIME_CAP_EXEMPT
				and runtime_sec > MAX_SYNC_JOB_RUNTIME_SEC
			)

			if not (is_zombie or over_cap):
				continue

			reasons = []
			if is_zombie:
				reasons.append("orphaned/zombie worker")
			if over_cap:
				reasons.append(
					f"runtime {int(runtime_sec)}s > cap {MAX_SYNC_JOB_RUNTIME_SEC}s"
				)

			stuck.append(
				{
					"job": job,
					"job_id": jid,
					"func_name": func_name,
					"queue_name": q.name,
					"worker_name": worker_name,
					"runtime_sec": int(runtime_sec),
					"is_zombie": is_zombie,
					"reason": "; ".join(reasons),
				}
			)

	return stuck


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------


def _reap_one(conn, info):
	"""Reap a single stuck job. Returns a serialisable summary dict."""
	from rq.job import Job, JobStatus
	from rq.registry import StartedJobRegistry

	job = info["job"]
	jid = info["job_id"]
	actions = []

	# 1. If a live worker still holds it, ask RQ to stop the work-horse.
	if not info["is_zombie"]:
		try:
			from rq.command import send_stop_job_command

			send_stop_job_command(conn, jid)
			actions.append("sent stop-job command")
		except Exception as e:
			actions.append(f"stop-job failed: {e}")

	# 2. Evict from the Started registry and mark failed so it cannot linger.
	try:
		registry = StartedJobRegistry(name=info["queue_name"], connection=conn)
		registry.remove(jid)
		actions.append("removed from started registry")
	except Exception as e:
		actions.append(f"registry remove failed: {e}")

	try:
		# Re-fetch in case the worker already transitioned it.
		job = Job.fetch(jid, connection=conn)
		job.set_status(JobStatus.FAILED)
		actions.append("marked failed")
	except Exception:
		pass

	# 3. Clear any orphaned sync-busy gate for the DocType it was working on.
	doctype = _derive_doctype(job)
	if doctype:
		try:
			clear_doctype_syncing(doctype)
			actions.append(f"cleared busy gate for {doctype}")
		except Exception as e:
			actions.append(f"gate clear failed: {e}")

	summary = {
		"job_id": jid,
		"func_name": info["func_name"],
		"worker_name": info["worker_name"],
		"runtime_sec": info["runtime_sec"],
		"reason": info["reason"],
		"doctype": doctype,
		"actions": actions,
	}

	frappe.log_error(
		title=f"Stuck task reaped: {info['func_name']}",
		message=(
			f"job_id={jid}\nworker={info['worker_name']}\n"
			f"runtime={info['runtime_sec']}s\nreason={info['reason']}\n"
			f"doctype={doctype}\nactions={'; '.join(actions)}"
		),
	)
	return summary


def reap_stuck_jobs():
	"""Find and reap all stuck jobs. Returns {'reaped': n, 'jobs': [...]}"""
	stuck = find_stuck_jobs()
	if not stuck:
		return {"reaped": 0, "jobs": []}

	conn = _get_redis_conn()
	reaped = [_reap_one(conn, info) for info in stuck]
	return {"reaped": len(reaped), "jobs": reaped}


def guard_preflight():
	"""
	Safe entry point called as the first step of every sync job.

	Reaps stuck jobs but never raises into the caller — a guard failure must not
	prevent the sync from running.
	"""
	try:
		return reap_stuck_jobs()
	except Exception:
		frappe.log_error(
			title="stuck_task_guard: pre-flight failed",
			message=frappe.get_traceback(),
		)
		return {"reaped": 0, "jobs": [], "error": True}


@frappe.whitelist()
def scan_stuck_jobs():
	"""Read-only Desk/console helper: list currently stuck jobs without reaping."""
	frappe.only_for("System Manager")
	return [
		{
			"job_id": info["job_id"],
			"func_name": info["func_name"],
			"queue": info["queue_name"],
			"worker": info["worker_name"],
			"runtime_sec": info["runtime_sec"],
			"is_zombie": info["is_zombie"],
			"reason": info["reason"],
		}
		for info in find_stuck_jobs()
	]
