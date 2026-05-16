from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple


@dataclass
class Sample:
    timestamp: str
    session_id: str
    router_position_id: str
    click_id: str
    x_px: float
    y_px: float
    room_id: str
    room_name: str
    height_ft: float
    ssid: str
    bssid: str
    frequency_mhz: Optional[float]
    channel: Optional[int]
    rssi_dbm: Optional[float]
    interface: str
    scan_backend: str
    sample_number: int
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClickPoint:
    click_id: str
    session_id: str
    router_position_id: str
    x_px: float
    y_px: float
    room_id: str
    room_name: str
    height_ft: float
    timestamp: str


@dataclass
class RoomLabel:
    room_id: str
    room_name: str
    polygon: List[Tuple[float, float]] = field(default_factory=list)
    label_x: Optional[float] = None
    label_y: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "room_name": self.room_name,
            "polygon": self.polygon,
            "label_x": self.label_x,
            "label_y": self.label_y,
        }


@dataclass
class RouterPosition:
    router_position_id: str
    name: str
    x_px: float
    y_px: float
    height_ft: float = 4.0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectConfig:
    project_name: str
    target_ssid: str
    target_bssid: str
    default_interface: str
    units: str
    collection_mode: str
    paths: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
