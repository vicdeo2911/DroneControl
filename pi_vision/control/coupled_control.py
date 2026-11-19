"""Dry-run prototype for comparing roll-based and vertical-image tracking control.

Run "python coupled_control.py --help" for the comparison options.
All commands are percentages of calibrated travel.
"""

import argparse
from dataclasses import dataclass, replace
import math
from typing import Optional


@dataclass(frozen=True)
class TrackingSettings:
    cruise_throttle: float = 25.0
    throttle_limit: float = 50.0
    turn_adjustment: float = 0.0  # Percentage points at full allowed roll.
    setpoint_x: float = 0.5
    deadband: float = 0.03
    kp: float = 35.0
    kd: float = 8.0
    roll_direction: int = 1
    max_roll: float = 15.0
    roll_limit: float = 50.0
    throttle_slew: float = 20.0
    roll_slew: float = 40.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name in ("cruise_throttle", "throttle_limit", "max_roll", "roll_limit"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if not -100 <= self.turn_adjustment <= 100:
            raise ValueError("turn_adjustment must be between -100 and 100")
        if not 0 <= self.setpoint_x <= 1 or not 0 <= self.deadband <= 1:
            raise ValueError("setpoint_x and deadband must be between 0 and 1")
        if self.roll_direction not in (-1, 1):
            raise ValueError("roll_direction must be -1 or 1")
        if min(self.kp, self.kd) < 0 or min(self.throttle_slew, self.roll_slew) <= 0:
            raise ValueError("gains must be nonnegative and slew rates must be positive")


@dataclass(frozen=True)
class TrackingCommand:
    throttle: float = 0.0
    roll: float = 0.0


@dataclass(frozen=True)
class TrackingStep:
    command: TrackingCommand
    throttle_target: float
    roll_target: float


def clamp(value, low, high):
    return max(low, min(high, value))


def slew(previous, target, rate, seconds):
    step = rate * seconds
    return previous + clamp(target - previous, -step, step)


def tracking_step(filtered_x, velocity, dt, previous, settings):
    """Compute one tracking update from filtered image x and its velocity.

    Use only with confirmed tracking data. Launch, loss, and stop handling
    belong to the surrounding controller. No hardware is accessed here.
    """
    return _tracking_step(filtered_x, velocity, dt, previous, settings)


def _tracking_step(filtered_x, velocity, dt, previous, settings, *,
                   roll_scale=1.0, throttle_boost=0.0):
    if not all(math.isfinite(value) for value in (
        filtered_x, velocity, dt, previous.throttle, previous.roll
    )):
        raise ValueError("tracking inputs and previous commands must be finite")
    if not 0 <= filtered_x <= 1:
        raise ValueError("filtered_x must be between 0 and 1")

    seconds = clamp(dt, 0, 0.2)  # Same tick interval cap as the current controller.
    roll_limit = min(settings.max_roll, settings.roll_limit)
    error = settings.setpoint_x - filtered_x
    error = math.copysign(max(0, abs(error) - settings.deadband), error)
    roll_target = settings.roll_direction * (settings.kp * error - settings.kd * velocity)
    roll_target = clamp(roll_target, -roll_limit, roll_limit) * roll_scale
    roll = slew(clamp(previous.roll, -roll_limit, roll_limit),
                roll_target, settings.roll_slew, seconds)

    roll_fraction = abs(roll) / roll_limit if roll_limit else 0.0
    throttle_target = (settings.cruise_throttle + settings.turn_adjustment * roll_fraction
                       + throttle_boost)
    throttle_target = clamp(throttle_target, 0, settings.throttle_limit)
    throttle = slew(clamp(previous.throttle, 0, settings.throttle_limit),
                    throttle_target, settings.throttle_slew, seconds)
    return TrackingStep(TrackingCommand(throttle, roll), throttle_target, roll_target)


@dataclass(frozen=True)
class VerticalSettings:
    """Image fractions increase downward. Gains yield throttle percentage points."""

    position_deadband: float = 0.02
    rate_deadband: float = 0.02  # Image heights per second.
    filter_seconds: float = 0.15
    position_gain: float = 80.0
    rate_gain: float = 20.0
    max_boost: float = 10.0
    roll_reduction_enabled: bool = False
    descent_seconds: float = 0.5
    max_roll_reduction: float = 0.35
    recovery_seconds: float = 0.4
    max_sample_gap: float = 0.3

    def __post_init__(self):
        for name in ("position_deadband", "rate_deadband", "position_gain",
                     "rate_gain", "max_boost", "max_roll_reduction"):
            if not 0 <= getattr(self, name) < math.inf:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("filter_seconds", "descent_seconds", "recovery_seconds",
                     "max_sample_gap"):
            if not 0 < getattr(self, name) < math.inf:
                raise ValueError(f"{name} must be finite and positive")
        if self.position_deadband > 1 or self.max_roll_reduction > 1:
            raise ValueError("position deadband and roll reduction must be image or roll fractions")


@dataclass(frozen=True)
class VerticalTrackingStep:
    tracking: TrackingStep
    filtered_y: Optional[float]
    vertical_rate: float
    throttle_boost: float
    roll_reduction: float


class VerticalTrackingController:
    """Experimental image-space feedback. Capture a reference during stable tracking."""

    def __init__(self, tracking, vertical=VerticalSettings()):
        self.tracking = replace(tracking, turn_adjustment=0.0)
        self.vertical = vertical
        self.reset()

    def reset(self):
        """Discard vertical history after loss of tracking or a stale sample."""
        self.reference_y = self.filtered_y = None
        self.descent_time = self.boost = self.reduction = 0.0

    def capture_reference(self, y):
        if not math.isfinite(y) or not 0 <= y <= 1:
            raise ValueError("reference y must be between 0 and 1")
        self.reset()
        self.reference_y = self.filtered_y = y

    def step(self, filtered_x, horizontal_rate, y, dt, previous):
        """Use None for unreliable y. Full tracking loss belongs to the caller."""
        if not all(math.isfinite(value) for value in (
            filtered_x, horizontal_rate, dt, previous.throttle, previous.roll
        )) or not 0 <= filtered_x <= 1:
            raise ValueError("invalid horizontal tracking inputs or previous commands")
        settings = self.vertical
        rate = 0.0
        if (y is None or not math.isfinite(y) or not 0 <= y <= 1
                or dt <= 0 or dt > settings.max_sample_gap):
            self.reset()
        elif self.reference_y is not None:
            alpha = -math.expm1(-dt / settings.filter_seconds)
            change = alpha * (y - self.filtered_y)
            self.filtered_y += change
            rate = change / dt
            error = self.filtered_y - self.reference_y - settings.position_deadband
            downward_rate = rate - settings.rate_deadband
            descending = error > 0 and downward_rate > 0
            if descending:
                self.descent_time += dt
                self.boost = min(settings.max_boost, settings.position_gain * error
                                 + settings.rate_gain * downward_rate)
            else:
                self.descent_time = 0.0
                self.boost = slew(self.boost, 0.0,
                                  settings.max_boost / settings.recovery_seconds, dt)

            reduction_target = 0.0
            if settings.roll_reduction_enabled and self.descent_time >= settings.descent_seconds:
                reduction_target = settings.max_roll_reduction
            self.reduction = slew(self.reduction, reduction_target,
                                  settings.max_roll_reduction / settings.recovery_seconds, dt)

        tracking = _tracking_step(
            filtered_x, horizontal_rate, dt, previous, self.tracking,
            roll_scale=1.0 - self.reduction, throttle_boost=self.boost,
        )
        return VerticalTrackingStep(tracking, self.filtered_y, rate, self.boost, self.reduction)


def dry_run_vertical(fixed):
    throttle_only = VerticalTrackingController(fixed)
    combined = VerticalTrackingController(fixed, VerticalSettings(roll_reduction_enabled=True))
    throttle_only.capture_reference(0.4)
    combined.capture_reference(0.4)
    initial = TrackingCommand(throttle=min(fixed.cruise_throttle, fixed.throttle_limit))
    throttle_command = combined_command = initial
    print("Method 2 dry run (synthetic camera observations)")
    print(" Time    Image y     Rate    Throttle (%)       Throttle + roll reduction")
    print("  (s)   filtered  (img/s)   output  target    output  target    roll  reduced")
    for tick in range(1, 41):
        if tick <= 10:
            y = 0.4
        elif tick <= 25:
            y = 0.4 + 0.02 * (tick - 10)
        elif tick <= 39:
            y = 0.7 - 0.02 * (tick - 25)
        else:
            y = None
        throttle = throttle_only.step(0.25, 0.0, y, 0.1, throttle_command)
        both = combined.step(0.25, 0.0, y, 0.1, combined_command)
        throttle_command = throttle.tracking.command
        combined_command = both.tracking.command
        filtered = "--" if both.filtered_y is None else f"{both.filtered_y:.3f}"
        print(f"{tick / 10:5.1f} {filtered:>10} {both.vertical_rate:8.3f} "
              f"{throttle_command.throttle:8.2f} {throttle.tracking.throttle_target:7.2f} "
              f"{combined_command.throttle:9.2f} {both.tracking.throttle_target:7.2f} "
              f"{combined_command.roll:7.2f} {both.roll_reduction:8.1%}")


def dry_run(coupled, fixed):
    cases = (
        ("Centered", 0.5, 0.0),
        ("Left turn", 0.5 + 0.03 + 7.5 / 35, 0.0),
        ("Right turn", 0.5 - 0.03 - 7.5 / 35, 0.0),
        ("Full left", 0.95, 0.5),
        ("Full right", 0.05, -0.5),
    )
    print("Autonomous tracking dry run (synthetic camera observations)")
    print(f"Turn adjustment at maximum roll: {coupled.turn_adjustment:+g} percentage points")
    print(f"Throttle cap: {coupled.throttle_limit:g}%")
    print(f"{'Scenario':<12} {'Time (s)':>8} {'Roll (%)':>9} {'Fixed target':>13} "
          f"{'Coupled target':>15} {'Coupled output':>15}")

    for name, x, velocity in cases:
        initial = TrackingCommand(throttle=min(coupled.cruise_throttle, coupled.throttle_limit))
        baseline = candidate = initial
        for tick in range(1, 61):
            fixed_step = tracking_step(x, velocity, 0.1, baseline, fixed)
            coupled_step = tracking_step(x, velocity, 0.1, candidate, coupled)
            baseline, candidate = fixed_step.command, coupled_step.command
            if tick in (1, 60):
                print(f"{name:<12} {tick / 10:8.1f} {candidate.roll:9.2f} "
                      f"{fixed_step.throttle_target:12.2f}% "
                      f"{coupled_step.throttle_target:14.2f}% {candidate.throttle:14.2f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("1", "2"), default="1",
                        help="dry-run method (default: 1)")
    parser.add_argument("--turn-adjustment", type=float, default=10,
                        help="dry-run change at maximum roll, in percentage points (default: +10)")
    parser.add_argument("--cruise-throttle", type=float, default=25,
                        help="baseline throttle percentage (default: 25)")
    parser.add_argument("--throttle-limit", type=float, default=50,
                        help="maximum throttle percentage (default: 50)")
    args = parser.parse_args()

    try:
        fixed = TrackingSettings(cruise_throttle=args.cruise_throttle,
                                 throttle_limit=args.throttle_limit)
        coupled = TrackingSettings(cruise_throttle=args.cruise_throttle,
                                   throttle_limit=args.throttle_limit,
                                   turn_adjustment=args.turn_adjustment)
    except ValueError as error:
        parser.error(str(error))
    if args.method == "2":
        dry_run_vertical(fixed)
    else:
        dry_run(coupled, fixed)


if __name__ == "__main__":
    main()
