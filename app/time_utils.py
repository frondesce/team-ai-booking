import datetime
from typing import List, Tuple, Optional
import pytz

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")

# 9 fixed daily slots: (start_hour, start_minute, end_hour, end_minute)
SLOT_DEFINITIONS: List[Tuple[int, int, int, int]] = [
    (9, 30, 10, 30),   # Slot 0: 09:30-10:30
    (10, 30, 11, 30),  # Slot 1: 10:30-11:30
    (11, 30, 12, 30),  # Slot 2: 11:30-12:30
    (12, 30, 13, 30),  # Slot 3: 12:30-13:30
    (13, 30, 14, 30),  # Slot 4: 13:30-14:30
    (14, 30, 15, 30),  # Slot 5: 14:30-15:30
    (15, 30, 16, 30),  # Slot 6: 15:30-16:30
    (16, 30, 17, 30),  # Slot 7: 16:30-17:30
    (17, 30, 18, 30),  # Slot 8: 17:30-18:30
]

class TimeProvider:
    _mock_time: Optional[datetime.datetime] = None

    @classmethod
    def set_mock_time(cls, dt: Optional[datetime.datetime]) -> None:
        """Set mock current time for testing. Must be timezone-aware or will be set to Shanghai."""
        if dt is not None and dt.tzinfo is None:
            dt = SHANGHAI_TZ.localize(dt)
        cls._mock_time = dt

    @classmethod
    def reset(cls) -> None:
        cls._mock_time = None

    @classmethod
    def now(cls) -> datetime.datetime:
        """Return current datetime in Asia/Shanghai timezone."""
        if cls._mock_time is not None:
            return cls._mock_time
        return datetime.datetime.now(SHANGHAI_TZ)

    @classmethod
    def today_date_str(cls) -> str:
        return cls.now().strftime("%Y-%m-%d")


def get_available_dates(now: Optional[datetime.datetime] = None) -> List[str]:
    """Return [today, tomorrow, day_after_tomorrow] in YYYY-MM-DD format."""
    if now is None:
        now = TimeProvider.now()
    cur_date = now.date()
    return [
        (cur_date + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(3)
    ]


def get_slot_times(date_str: str, slot_index: int) -> Tuple[datetime.datetime, datetime.datetime]:
    """Given date string (YYYY-MM-DD) and slot index (0..8), return (start_at, end_at) with Asia/Shanghai tz."""
    if slot_index < 0 or slot_index >= len(SLOT_DEFINITIONS):
        raise ValueError(f"Invalid slot_index: {slot_index}. Must be between 0 and 8.")
    
    y, m, d = map(int, date_str.split("-"))
    sh, sm, eh, em = SLOT_DEFINITIONS[slot_index]
    
    start_dt = SHANGHAI_TZ.localize(datetime.datetime(y, m, d, sh, sm, 0))
    end_dt = SHANGHAI_TZ.localize(datetime.datetime(y, m, d, eh, em, 0))
    return start_dt, end_dt


def get_slot_label(slot_index: int) -> str:
    sh, sm, eh, em = SLOT_DEFINITIONS[slot_index]
    return f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"


def iso_format(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = SHANGHAI_TZ.localize(dt)
    return dt.isoformat()


def parse_iso(dt_str: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = SHANGHAI_TZ.localize(dt)
    return dt
