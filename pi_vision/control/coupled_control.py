"""Dry-run prototype for coupling autonomous tracking throttle to roll.

Run "python coupled_control.py --help" for the comparison options.
All commands are percentages of calibrated travel.
"""

import argparse
from dataclasses import dataclass
import math


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
    roll_target = clamp(roll_target, -roll_limit, roll_limit)
    roll = slew(clamp(previous.roll, -roll_limit, roll_limit),
                roll_target, settings.roll_slew, seconds)

    roll_fraction = abs(roll) / roll_limit if roll_limit else 0.0
    throttle_target = settings.cruise_throttle + settings.turn_adjustment * roll_fraction
    throttle_target = clamp(throttle_target, 0, settings.throttle_limit)
    throttle = slew(clamp(previous.throttle, 0, settings.throttle_limit),
                    throttle_target, settings.throttle_slew, seconds)
    return TrackingStep(TrackingCommand(throttle, roll), throttle_target, roll_target)


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
    dry_run(coupled, fixed)


if __name__ == "__main__":
    main()
