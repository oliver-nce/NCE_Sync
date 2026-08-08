# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Centralised constants for NCE Sync.

All magic numbers, cache keys, singleton references, and configuration
defaults live here so every module draws from a single source of truth.
"""

# ---------------------------------------------------------------------------
# Batch sizes
# ---------------------------------------------------------------------------

#: Number of records per INSERT / UPDATE batch during WP→Frappe sync.
UPSERT_BATCH_SIZE = 3000

#: Number of orphan Frappe records deleted per commit during TS Compare sync.
DELETE_BATCH_SIZE = 3000

#: Maximum number of values in a single WHERE … IN (…) clause.
WHERE_IN_BATCH_SIZE = 1000

# ---------------------------------------------------------------------------
# Sync logging
# ---------------------------------------------------------------------------

#: Maximum individual row-error messages saved in a single sync run.
MAX_ROW_ERROR_MESSAGES = 10

#: Number of Sync Log records kept after each scheduled run (oldest pruned).
KEEP_SYNC_LOG_COUNT = 20

#: Allowed difference between the Frappe row count and the fresh WordPress
#: recount before a full-mirror sync is downgraded from Success to Warning.
#: 0 = exact match required. Raise via site config if a table legitimately
#: changes rows mid-sync often enough to cause noise.
SYNC_COUNT_DRIFT_TOLERANCE = 0

# ---------------------------------------------------------------------------
# Stuck-task guard + hard runtime cap
# ---------------------------------------------------------------------------

#: Hard cap (seconds) for a single WP-exchange background job. Used both as the
#: RQ ``timeout`` on enqueue and as the runtime the stuck-task guard reaps past.
#: 300 = 5 minutes. Orchestrators (scheduled batch) and exports are exempt from
#: the guard's runtime reap (they are only reaped when their worker is dead).
MAX_SYNC_JOB_RUNTIME_SEC = 300

#: A started job whose owning RQ worker has not sent a heartbeat within this many
#: seconds is treated as an orphan/zombie (worker hung or died) and reaped.
WORKER_STALE_SEC = 120

#: PyMySQL socket timeouts (seconds) for the WordPress connection. connect is
#: short (fail fast if RDS is unreachable); read/write match the job cap so a
#: hung socket dies at ~5 min instead of blocking a worker indefinitely.
WP_CONNECT_TIMEOUT_SEC = 10
WP_READ_TIMEOUT_SEC = 300
WP_WRITE_TIMEOUT_SEC = 300

# ---------------------------------------------------------------------------
# Scheduled sync frequency map
# ---------------------------------------------------------------------------

#: Human-readable frequency labels → minutes.
SYNC_FREQUENCY_MAP = {
	"Every 5 Minutes": 5,
	"Every 15 Minutes": 15,
	"Every 30 Minutes": 30,
	"Hourly": 60,
	"Every 6 Hours": 360,
	"Daily": 1440,
	"Weekly": 10080,
}

# ---------------------------------------------------------------------------
# Temp-name counter (reverse sync)
# ---------------------------------------------------------------------------

#: Singleton DocType that stores the auto-decrementing counter.
TEMP_NAME_COUNTER_DOCTYPE = "Sync Manager"

#: Field on the singleton that holds the current counter value.
TEMP_NAME_COUNTER_FIELD = "temp_name_counter"

# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------

#: Redis key for the listen-for-changes table map (live_sync).
CACHE_KEY_LISTEN_TABLES = "nce_sync:listen_for_changes_tables"

# ---------------------------------------------------------------------------
# Pick-list limits
# ---------------------------------------------------------------------------

#: Maximum DISTINCT values fetched when building a Select (pick-list) field.
PICK_LIST_DISTINCT_LIMIT = 500

# ---------------------------------------------------------------------------
# Frappe system fields — never written back to WordPress
# ---------------------------------------------------------------------------

FRAPPE_SYSTEM_FIELDS = frozenset(
	{
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"doctype",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
	}
)
