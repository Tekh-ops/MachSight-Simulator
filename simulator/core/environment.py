"""
simulator/core/environment.py
==============================
1-D environment model: a straight track with configurable obstacles.

The RC car drives along a positive x-axis:

    0 m ─────────────────────────────────────────────────→ x
         🚗 (position_m)            █ (obstacle at 50 m)

Obstacle distance is computed by the ultrasonic sensor model, not directly
exposed to MachSight.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional


@dataclasses.dataclass
class Obstacle:
    """A solid obstacle on the 1-D track."""

    position_m: float       # Distance from track origin (x=0)
    width_m: float = 0.3    # Physical width (depth) of obstacle


@dataclasses.dataclass
class Environment:
    """1-D simulation environment.

    Maintains a list of obstacles.  The front of the vehicle is at
    vehicle.position_m; distance to nearest forward obstacle is derived here.
    """

    track_length_m: float = 200.0
    obstacles: List[Obstacle] = dataclasses.field(default_factory=list)

    def add_obstacle(self, position_m: float, width_m: float = 0.3) -> Obstacle:
        obs = Obstacle(position_m=position_m, width_m=width_m)
        self.obstacles.append(obs)
        return obs

    def clear_obstacles(self) -> None:
        self.obstacles.clear()

    def nearest_forward_obstacle_distance(
        self, vehicle_position_m: float
    ) -> Optional[float]:
        """Return distance in metres to the nearest obstacle ahead.

        Returns None if no obstacle is within the track ahead of the vehicle.
        """
        min_distance: Optional[float] = None
        for obs in self.obstacles:
            # Only consider obstacles *ahead* of the vehicle
            gap = obs.position_m - vehicle_position_m
            if gap > 0:
                if min_distance is None or gap < min_distance:
                    min_distance = gap
        return min_distance

    @classmethod
    def from_config(cls, obstacles_config: List[dict]) -> "Environment":
        """Build an Environment from a list of obstacle config dicts."""
        env = cls()
        for obs_cfg in obstacles_config:
            env.add_obstacle(
                position_m=float(obs_cfg["position_m"]),
                width_m=float(obs_cfg.get("width_m", 0.3)),
            )
        return env
