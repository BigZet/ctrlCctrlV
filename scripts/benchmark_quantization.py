"""Test pinned lighter LightOnOCR variants in an isolated, disposable model catalog.

Does not change application settings, the installed model catalog or user files.
Run with QT_QPA_PLATFORM=offscreen to avoid the system clipboard.
"""
import argparse
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import threading

import httpx

from ctrlcctrlv.catalog import Artifact, MODELS
from ctrlcctrlv.runtime import LocalRuntime, download
from ctrlcctrlv.settings import data_directory

REVISION = '414757bdb78fd07c947a1e83c2b8d7e9a8b73583'
BASE = 'https://huggingface.co/prithivMLmods/LightOnOCR-3-0.8B-GGUF/resolve/' + REVISION + '/'
WEIGHTS = Artifact('LightOnOCR-3-0.8B.Q4_K_M.gguf', BASE + 'LightOnOCR-3-0.8B.Q4_K_M.gguf',
                   '290ba9b31716a00dc1cbe1555a2776109f21ae3f4f5c177a2b543c9bc3eab12a', 529297504)
PROJECTOR_Q8 = Artifact('LightOnOCR-3-0.8B.mmproj-q8_0.gguf', BASE + 'LightOnOCR-3-0.8B.mmproj-q8_0.gguf',
                        'a8b9b3b01b6c780747b48e0807ef5b753c54f27aa4c23962c3c7d5ace2ed0233', 115923776)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--projector', choices=('bf16', 'q8'), default='bf16')
    parser.add_argument('--data-dir', type=Path, default=Path('artifacts/quantization-data'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/quantization.json'))
    args = parser.parse_args()
    primary = data_directory()
    model = MODELS['lightonocr-3-0.8b']
    identifier = 'lightonocr-3-0.8b-q4-' + args.projector + '-experiment'
    projector = model.files[1] if args.projector == 'bf16' else PROJECTOR_Q8
    MODELS[identifier] = replace(model, id=identifier, label='LightOnOCR-3 0.8B · Q4_K_M · experiment',
                                files=(WEIGHTS, projector), source=BASE)
    source = LocalRuntime(primary)
    runtime = LocalRuntime(args.data_dir)
    if not runtime.binary:
        runtime.engine_directory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source.engine_directory, runtime.engine_directory, symlinks=True, dirs_exist_ok=True)
    directory = args.data_dir / 'models' / identifier
    directory.mkdir(parents=True, exist_ok=True)
    if args.projector == 'bf16' and not (directory / projector.filename).exists():
        shutil.copy2(primary / 'models' / model.id / projector.filename, directory / projector.filename)
    last = {}
    def progress(label, done, total):
        bucket = int(done / total * 10) if total else 0
        if last.get(label) != bucket:
            print(f'{label}: {done}/{total}', flush=True)
            last[label] = bucket
    cancel = threading.Event()
    # Direct HTTPS transport; TLS verification stays enabled. All files must
    # match the pinned SHA256 before LocalRuntime writes its verified receipt.
    for artifact in (WEIGHTS, projector):
        download(artifact, directory / artifact.filename, cancel, progress,
                 transport=httpx.HTTPTransport(retries=1))
    runtime.install(identifier, cancel, progress)
    from scripts.benchmark import main as benchmark
    sys.argv = [sys.argv[0], '--model', identifier, '--data-dir', str(args.data_dir),
                '--fixtures', 'artifacts/fixtures-expanded', '--limit', '32', '--output', str(args.output)]
    benchmark()


if __name__ == '__main__':
    main()
