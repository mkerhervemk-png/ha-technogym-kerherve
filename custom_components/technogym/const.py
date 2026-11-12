"""Constantes de l'intégration Technogym (Mywellness)."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "technogym"
MANUFACTURER = "Technogym"

CONF_FACILITY_ID = "facility_id"
CONF_FACILITY_URL = "facility_url"
CONF_FACILITY_NAME = "facility_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_HISTORY_DAYS = "history_days"
CONF_CLASS_DAYS = "class_days"

DEFAULT_SCAN_INTERVAL_MIN = 30
DEFAULT_HISTORY_DAYS = 35
DEFAULT_CLASS_DAYS = 7

MIN_SCAN_INTERVAL_MIN = 15
MAX_SCAN_INTERVAL_MIN = 240

DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MIN)

EVENT_NEW_ACTIVITY = "technogym_new_activity"

SERVICE_BOOK_CLASS = "book_class"
SERVICE_UNBOOK_CLASS = "unbook_class"
SERVICE_LIST_CLASSES = "list_classes"
SERVICE_REFRESH = "refresh"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_CLASS_ID = "class_id"
ATTR_PARTITION_DATE = "partition_date"
ATTR_DAYS = "days"
ATTR_ONLY_BOOKABLE = "only_bookable"
