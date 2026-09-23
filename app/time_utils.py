import datetime
from typing import List, Tuple, Optional
import pytz

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")

DEFAULT_SLOT_START_TIME = "09:30"
DEFAULT_SLOT_END_TIME = "18:30"


def parse_time_str(time_str: str) -> Tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute). Allows '24:00' -> (24, 0)."""
    time_str = time_str.strip()
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式错误: '{time_str}'，必须为 HH:MM 格式")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"时间格式错误: '{time_str}'，必须为有效数字")
    if m < 0 or m > 59:
        raise ValueError(f"时间分钟无效: '{time_str}'，分钟必须在 0 到 59 之间")
    if h == 24 and m == 0:
        return 24, 0
    if h < 0 or h > 23:
        raise ValueError(f"时间小时无效: '{time_str}'，小时必须在 0 到 23 之间（或 24:00）")
    return h, m


def validate_time_slot_range(start_time_str: str, end_time_str: str) -> Tuple[str, str]:
    """Validate start_time and end_time. Each slot must be 1 hour."""
    sh, sm = parse_time_str(start_time_str)
    eh, em = parse_time_str(end_time_str)

    start_total = sh * 60 + sm
    end_total = eh * 60 + em

    if start_total >= 24 * 60:
        raise ValueError("开始时间必须在 00:00 至 23:59 之间")
    if end_total <= start_total:
        raise ValueError("结束时间必须晚于开始时间")
    if (end_total - start_total) < 60:
        raise ValueError("结束时间与开始时间之间至少须间隔 1 小时")
    if (end_total - start_total) % 60 != 0:
        raise ValueError("结束时间与开始时间的间隔必须为整小时（例如 09:30 至 18:30 或 09:00 至 18:00）")

    formatted_start = f"{sh:02d}:{sm:02d}"
    formatted_end = f"{eh:02d}:{em:02d}"
    return formatted_start, formatted_end


def generate_slot_definitions(start_time_str: str, end_time_str: str) -> List[Tuple[int, int, int, int]]:
    """Given start_time and end_time (e.g. '09:30', '18:30'), generate list of (sh, sm, eh, em) 1-hour slots."""
    formatted_start, formatted_end = validate_time_slot_range(start_time_str, end_time_str)
    sh, sm = parse_time_str(formatted_start)
    eh, em = parse_time_str(formatted_end)

    start_total = sh * 60 + sm
    end_total = eh * 60 + em
    num_slots = (end_total - start_total) // 60

    slots = []
    for i in range(num_slots):
        s_min = start_total + i * 60
        e_min = s_min + 60
        slot_sh = s_min // 60
        slot_sm = s_min % 60
        slot_eh = e_min // 60
        slot_em = e_min % 60
        slots.append((slot_sh, slot_sm, slot_eh, slot_em))
    return slots


DEFAULT_SLOT_DEFINITIONS: List[Tuple[int, int, int, int]] = generate_slot_definitions(
    DEFAULT_SLOT_START_TIME, DEFAULT_SLOT_END_TIME
)
SLOT_DEFINITIONS: List[Tuple[int, int, int, int]] = DEFAULT_SLOT_DEFINITIONS


class TimeSlotManager:
    _slot_definitions: List[Tuple[int, int, int, int]] = DEFAULT_SLOT_DEFINITIONS
    _start_time: str = DEFAULT_SLOT_START_TIME
    _end_time: str = DEFAULT_SLOT_END_TIME

    @classmethod
    def set_config(cls, start_time: str, end_time: str) -> None:
        start_time, end_time = validate_time_slot_range(start_time, end_time)
        cls._start_time = start_time
        cls._end_time = end_time
        cls._slot_definitions = generate_slot_definitions(start_time, end_time)

    @classmethod
    def get_slot_definitions(cls) -> List[Tuple[int, int, int, int]]:
        return cls._slot_definitions

    @classmethod
    def get_time_range(cls) -> Tuple[str, str]:
        return cls._start_time, cls._end_time

    @classmethod
    def reset(cls) -> None:
        cls.set_config(DEFAULT_SLOT_START_TIME, DEFAULT_SLOT_END_TIME)


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


def get_slot_times(
    date_str: str,
    slot_index: int,
    slot_definitions: Optional[List[Tuple[int, int, int, int]]] = None
) -> Tuple[datetime.datetime, datetime.datetime]:
    """Given date string (YYYY-MM-DD) and slot index, return (start_at, end_at) with Asia/Shanghai tz."""
    if slot_definitions is None:
        slot_definitions = TimeSlotManager.get_slot_definitions()
    if slot_index < 0 or slot_index >= len(slot_definitions):
        raise ValueError(f"Invalid slot_index: {slot_index}. Must be between 0 and {len(slot_definitions) - 1}.")

    y, m, d = map(int, date_str.split("-"))
    sh, sm, eh, em = slot_definitions[slot_index]

    start_dt = SHANGHAI_TZ.localize(datetime.datetime(y, m, d, sh, sm, 0))
    end_dt = start_dt + datetime.timedelta(hours=1)
    return start_dt, end_dt


def get_slot_label(
    slot_index: int,
    slot_definitions: Optional[List[Tuple[int, int, int, int]]] = None
) -> str:
    if slot_definitions is None:
        slot_definitions = TimeSlotManager.get_slot_definitions()
    sh, sm, eh, em = slot_definitions[slot_index]
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
