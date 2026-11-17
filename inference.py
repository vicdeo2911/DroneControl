"""Small synchronous HailoRT wrapper with explicitly nested resource lifetimes."""
from contextlib import ExitStack
import os
import subprocess

# Enable Hailo's utilization sampler for both service and manual launches.
os.environ.setdefault('HAILO_MONITOR', '1')

from hailo_platform import (ConfigureParams, FormatType, HEF, HailoStreamInterface,
                            InferVStreams, InputVStreamParams, OutputVStreamParams, VDevice)
import numpy as np


def hailo_architecture():
    result = subprocess.run(['hailortcli', 'fw-control', 'identify'],
                            capture_output=True, text=True, check=True, timeout=15)
    for line in result.stdout.splitlines():
        if 'Device Architecture:' in line:
            return line.split(':', 1)[1].strip()
    raise RuntimeError('Hailo did not report its architecture. Check hailortcli fw-control identify.')


class HailoModel:
    """Open streams after the device; close streams before releasing the device.

    Uses HailoRT's public VStreams API. The Picamera2 0.3.38 Hailo helper crashed
    during shutdown with this Pi's runtime, so this project owns that lifecycle.
    """
    def __init__(self, path):
        self.hef = HEF(path)
        self.stack = ExitStack()

    def __enter__(self):
        try:
            self.device = self.stack.enter_context(VDevice())
            params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
            self.group = self.device.configure(self.hef, params)[0]
            inputs = InputVStreamParams.make(self.group, quantized=True, format_type=FormatType.UINT8)
            outputs = OutputVStreamParams.make(self.group, quantized=False, format_type=FormatType.FLOAT32)
            self.pipeline = self.stack.enter_context(InferVStreams(self.group, inputs, outputs))
            self.stack.enter_context(self.group.activate(self.group.create_params()))
            self.input_info = self.hef.get_input_vstream_infos()[0]
            return self
        except Exception:
            self.stack.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.stack.close()

    def get_input_shape(self):
        return self.input_info.shape

    def describe(self):
        return ([(info.name, info.shape, info.format.type) for info in self.hef.get_input_vstream_infos()],
                [(info.name, info.shape, info.format.type) for info in self.hef.get_output_vstream_infos()])

    def run(self, rgb):
        batch = np.expand_dims(np.ascontiguousarray(rgb), axis=0)
        result = self.pipeline.infer({self.input_info.name: batch})
        return {name: data[0] for name, data in result.items()}
