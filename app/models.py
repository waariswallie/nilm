from pydantic import BaseModel
from typing import Optional, Tuple


class EventDTO(BaseModel):
    t_on: str
    t_off: Optional[str]
    dP_on_kW: float
    dP_phase_kW: Optional[Tuple[float, float, float]]
    duration_min: Optional[float]
    cluster: int | None = None
