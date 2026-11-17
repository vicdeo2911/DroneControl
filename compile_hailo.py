"""Compile this project's two-class YOLOv8 ONNX for Hailo-8 on Linux x86_64.

Requires Hailo Dataflow Compiler 3.x. Run inside the compiler container.
Intermediate HAR files allow the expensive stages to be resumed explicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path


def onnx_image_size(path):
    """Read the static RGB NCHW export rather than guessing its input size."""
    import onnx
    model = onnx.load(str(path), load_external_data=False)
    initializers = {value.name for value in model.graph.initializer}
    inputs = [value for value in model.graph.input if value.name not in initializers]
    if len(inputs) != 1:
        raise ValueError('Expected one image input in the ONNX model.')
    shape = [dim.dim_value for dim in inputs[0].type.tensor_type.shape.dim]
    if (len(shape) != 4 or shape[:2] != [1, 3] or shape[2] != shape[3]
            or not 320 <= shape[2] <= 1920 or shape[2] % 32):
        raise ValueError(f'Expected static [1, 3, S, S] input, S=320–1920 divisible by 32; got {shape}.')
    return shape[2]


def check_stage_input(output, stage, metadata):
    manifest = output / (stage + '-input.json')
    if manifest.exists():
        if json.loads(manifest.read_text()) != metadata:
            raise ValueError(f'{stage}.har belongs to a different ONNX model/input size. Rebuild that stage or use a new output directory.')
    elif metadata['input_size'] != 640:
        raise ValueError(f'Missing {stage} input manifest. Rebuild that stage for the selected resolution.')
    else:
        print(f'Legacy 640 {stage}.har has no input manifest; verify its source model.', flush=True)


def nms_config(outputs, size):
    if len(outputs) != 6:
        raise ValueError(f'Expected six detection heads, got {outputs}')
    return dict(nms_scores_th=0.2, nms_iou_th=0.7, image_dims=[size, size],
                max_proposals_per_class=100, classes=2, regression_length=16,
                background_removal=False, background_removal_index=0,
                bbox_decoders=[dict(name=f'bbox_decoder_{stride}', stride=stride,
                                   reg_layer=outputs[i * 2], cls_layer=outputs[i * 2 + 1])
                               for i, stride in enumerate((8, 16, 32))])


def calibration_samples(paths, size):
    import cv2
    import numpy as np
    # Match app.py letterboxing at the actual model input size.
    samples = np.empty((len(paths), size, size, 3), dtype=np.uint8)
    for index, path in enumerate(paths):
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f'Cannot decode {path}')
        h, w = image.shape[:2]
        scale = min(size / w, size / h)
        rw, rh = round(w * scale), round(h * scale)
        left, top = (size - rw) // 2, (size - rh) // 2
        samples[index].fill(114)
        samples[index, top:top + rh, left:left + rw] = cv2.cvtColor(
            cv2.resize(image, (rw, rh)), cv2.COLOR_BGR2RGB)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--onnx', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=['all', 'parse', 'optimize', 'compile'], default='all')
    args = parser.parse_args()
    size = onnx_image_size(args.onnx)
    metadata = dict(input_size=size, onnx_sha256=hashlib.sha256(args.onnx.read_bytes()).hexdigest())
    print(f'Detector input: {size} x {size}', flush=True)
    from hailo_sdk_client import ClientRunner

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    parsed, quantized = output / 'parsed.har', output / 'quantized.har'
    if args.stage in ('all', 'parse'):
        runner = ClientRunner(hw_arch='hailo8')
        ends = [f'/model.22/cv{branch}.{i}/cv{branch}.{i}.2/Conv'
                for i in range(3) for branch in (2, 3)]
        runner.translate_onnx_model(str(args.onnx.resolve()), 'plane_target', end_node_names=ends)
        runner.save_har(str(parsed))
        (output / 'parsed-input.json').write_text(json.dumps(metadata, indent=2))
        print('Parsed model saved:', parsed, flush=True)
    if args.stage in ('all', 'optimize'):
        check_stage_input(output, 'parsed', metadata)
        runner = ClientRunner(har=str(parsed))
        outputs = [layer.inputs[0].rsplit('/', 1)[-1]
                   for layer in runner.get_hn_model().get_output_layers()]
        config = nms_config(outputs, size)
        paths = sorted(p for p in args.calibration.iterdir()
                       if p.suffix.lower() in ('.jpg', '.jpeg', '.png'))[:256]
        if not paths:
            raise ValueError('Calibration directory contains no images.')
        config_path = output / 'nms_config.json'
        config_path.write_text(json.dumps(config, indent=2))
        batch = 8
        while batch > 1 and batch * size * size > 8 * 640 * 640:
            batch //= 2
        if len(paths) % batch:
            batch = 1
        script = ['normalization1 = normalization([0, 0, 0], [255, 255, 255])',
                  'model_optimization_flavor(optimization_level=1)',
                  f'model_optimization_config(calibration, batch_size={batch}, calibset_size={len(paths)})',
                  # IBC is optional and very slow in WSL; validate the INT8 model
                  # against saved validation frames before accepting deployment.
                  'post_quantization_optimization(bias_correction, policy=disabled)']
        script += [f'change_output_activation({outputs[i]}, sigmoid)' for i in (1, 3, 5)]
        script += [f'nms_postprocess("{config_path}", meta_arch=yolov8, engine=cpu)',
                   'allocator_param(width_splitter_defuse=disabled)']
        script_text = '\n'.join(script)
        (output / 'model.alls').write_text(script_text)
        runner.load_model_script(script_text)
        samples = calibration_samples(paths, size)
        print(f'Calibrating with {len(paths)} training images', flush=True)
        runner.optimize(samples)
        runner.save_har(str(quantized))
        (output / 'quantized-input.json').write_text(json.dumps(metadata, indent=2))
        print('Quantized model saved:', quantized, flush=True)
    if args.stage in ('all', 'compile'):
        check_stage_input(output, 'quantized', metadata)
        runner = ClientRunner(har=str(quantized))
        hef = output / 'plane-target-h8.hef'
        hef.write_bytes(runner.compile())
        (output / 'model-input.json').write_text(json.dumps(metadata, indent=2))
        print('Compiled HEF:', hef, flush=True)


if __name__ == '__main__':
    main()
