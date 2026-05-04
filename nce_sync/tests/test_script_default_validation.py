# Copyright (c) 2026, Oliver Reid and contributors

import pytest

from nce_sync.utils.script_default_validation import validate_and_normalize_script_default


def test_date_sql():
	assert validate_and_normalize_script_default("Date", "2026-05-03", "c1") == "2026-05-03"
	assert validate_and_normalize_script_default("Date", "'2026-05-03'", "c1") == "2026-05-03"


def test_date_invalid():
	with pytest.raises(Exception):
		validate_and_normalize_script_default("Date", "05/03/2026", "c1")


def test_time_24h():
	assert validate_and_normalize_script_default("Time", "14:30:00", "c1") == "14:30:00"
	assert validate_and_normalize_script_default("Time", "'23:00:00'", "c1") == "23:00:00"


def test_time_invalid():
	with pytest.raises(Exception):
		validate_and_normalize_script_default("Time", "2:30 PM", "c1")
	with pytest.raises(Exception):
		validate_and_normalize_script_default("Time", "25:00:00", "c1")


def test_datetime_sql():
	assert (
		validate_and_normalize_script_default("Datetime", "2026-05-03 14:30:00", "c1")
		== "2026-05-03 14:30:00"
	)


def test_json_valid():
	assert validate_and_normalize_script_default("JSON", '{"a": 1}', "c1") == '{"a": 1}'


def test_json_invalid():
	with pytest.raises(Exception):
		validate_and_normalize_script_default("JSON", "{no}", "c1")


def test_int():
	assert validate_and_normalize_script_default("Int", "-42", "c1") == "-42"


def test_check_normalized():
	assert validate_and_normalize_script_default("Check", "true", "c1") == "1"
	assert validate_and_normalize_script_default("Check", "0", "c1") == "0"


def test_rating():
	assert validate_and_normalize_script_default("Rating", "5", "c1") == "5"
	with pytest.raises(Exception):
		validate_and_normalize_script_default("Rating", "6", "c1")


def test_data_passthrough():
	assert validate_and_normalize_script_default("Data", "  hello ", "c1") == "  hello "
	assert validate_and_normalize_script_default("Small Text", "x", "c1") == "x"
