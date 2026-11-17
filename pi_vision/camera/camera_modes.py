"""Capture geometry validation and sensor selection, independent of camera APIs."""
import math

RESOLUTIONS = ((960, 540), (1280, 720), (1920, 1080))


def validate_capture(width, height, fps):
    if (type(width) is not int or type(height) is not int
            or width < 64 or height < 64 or width % 2 or height % 2
            or width > 1920 or height > 1080):
        raise ValueError('Capture dimensions must be even, from 64 pixels up to 1920 x 1080.')
    if type(fps) not in (int, float) or not math.isfinite(fps) or int(fps) != fps or not 1 <= fps <= 60:
        raise ValueError('Camera FPS must be a whole number from 1 to 60.')


def choose_sensor_mode(modes, width, height, fps):
    validate_capture(width, height, fps)
    eligible = [m for m in modes if m['size'][0] >= width and m['size'][1] >= height and m['fps'] >= fps]
    if not eligible:
        raise ValueError(f'No sensor mode supports {width} x {height} at {fps} fps.')
    # Preserve the largest field of view, then avoid unnecessarily large readout.
    return min(eligible, key=lambda m: (-(m['crop_limits'][2] * m['crop_limits'][3]),
                                       m['size'][0] * m['size'][1]))
