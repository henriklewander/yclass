"""Constants for yclass."""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

DOMAIN = "yclass"

# Vklass reports wall-clock times in Swedish local time, whatever HA's zone is.
VKLASS_TZ = ZoneInfo("Europe/Stockholm")

UPDATE_INTERVAL = timedelta(hours=1)

# From this time of day the "school day" shown is the next one, not today's.
DAY_SWITCH = time(17, 0)

# The dashboard card, served by the integration itself.
CARD_URL = "/yclass/yclass-card.js"

# The window refreshed on every poll; calendar views outside it are fetched on demand.
WINDOW_PAST = timedelta(days=30)
WINDOW_AHEAD = timedelta(days=90)
