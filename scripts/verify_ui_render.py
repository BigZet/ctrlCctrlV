"""Validate saved render metadata and PNG dimensions without starting Qt."""
import argparse
import json
import struct
from pathlib import Path


def verify_manifest(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['unmatched_filters']:
        raise ValueError(f'{path}: unmatched filters')
    names = set()
    for frame in data['frames']:
        name = frame['screenshot']
        if name in names:
            raise ValueError(f'{path}: duplicate frame {name}')
        names.add(name)
        image = path.parent / name
        saved_metadata = json.loads(image.with_suffix('.json').read_text(encoding='utf-8'))
        if saved_metadata != frame:
            raise ValueError(f'{name}: frame metadata differs from manifest')
        png = image.read_bytes()
        if png[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError(f'{name}: invalid PNG')
        width, height = struct.unpack('>II', png[16:24])
        if dict(width=width, height=height) != frame['image_size']:
            raise ValueError(f'{name}: incorrect pixel dimensions')
        if frame['fit_applied'] and not frame['frame_inside_available']:
            raise ValueError(f'{name}: frame outside explicit test screen')
        if frame['device_pixel_ratio'] != data['scale']:
            raise ValueError(f'{name}: unexpected Qt scale')
        baseline = frame['baseline_image']
        if baseline is not None and not Path(baseline).is_file():
            raise ValueError(f'{name}: baseline reference does not exist')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifests', type=Path, nargs='+')
    parser.add_argument('--expect-state', action='append', default=[])
    args = parser.parse_args()
    states, frames = set(), 0
    for path in args.manifests:
        result = verify_manifest(path)
        states.update(result['rendered_states'])
        frames += len(result['frames'])
        print(f'{path}: {len(result["frames"])} frames verified')
    missing = set(args.expect_state) - states
    if missing:
        raise ValueError('Missing expected states: ' + ', '.join(sorted(missing)))
    print(f'Verified {frames} frames across {len(states)} states')


if __name__ == '__main__':
    main()
