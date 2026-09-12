"""Mechanical engineering design calculations.

Shigley's Mechanical Engineering Design, Budynas and Nisbett.

Gears were never special. Every mechanical design needs the same core
calculations underneath it, and this package holds them separately from any one
component so that a shaft, a bolted joint or a bracket draws on the same stress
analysis and the same failure theories.

Chapters implemented so far:

- 3, load and stress analysis: `stress`
- 5, failure theories for static loading: `static_failure`
- 6, fatigue failure from variable loading: `fatigue`
- 8, screws, fasteners and nonpermanent joints: `bolted_joints`

Chapters 3, 5 and 6 come first because everything else in the book stands on
them: a shaft, a spring, a weld and a bearing all reduce to a stress state rated
against a static or a fatigue criterion.

Scope, stated the same way the gear engine states it: this is preliminary sizing
evidence with declared assumptions, never certification. A result carries the
factor of safety it achieved and the assumptions behind it. Two quantities are
curve fits to published figures rather than tabulated constants, and say so at
the point of use: the fatigue-strength fraction of figure 6-18 and the Neuber
constant of equation 6-35. Both accept an explicit override, which is the
recommended path for any design that will be manufactured.

Units are millimetres, newtons, megapascals and newton-millimetres throughout.
"""

from __future__ import annotations

from .errors import MechanicsError
from .stress import StressState

__all__ = ["MechanicsError", "StressState"]
