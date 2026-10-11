# Third-party components

This is a local development/pilot application. It is not notarized for public distribution.

- Python: PSF license — https://docs.python.org/3/license.html
- PySide6 / Qt: LGPLv3 / GPLv3 / commercial terms, depending on the component. This application uses the dynamically linked Qt Core, Gui and Widgets modules. Upstream license texts and source: https://doc.qt.io/qtforpython-6/licenses.html and https://code.qt.io/cgit/pyside/pyside-setup.git/
- PyObjC: MIT — https://github.com/ronaldoussoren/pyobjc
- HTTPX: BSD-3-Clause — https://github.com/encode/httpx
- keyring: MIT — https://github.com/jaraco/keyring
- llama.cpp: MIT — https://github.com/ggml-org/llama.cpp
- Qwen3-VL weights: Apache-2.0 — https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- LightOnOCR-3 weights: Apache-2.0 — https://huggingface.co/lightonai/LightOnOCR-3-0.8B and https://huggingface.co/lightonai/LightOnOCR-3-4B

The catalog uses pinned community GGUF conversions from Unsloth and prithivMLmods; these are distinct from the original model publishers. Their exact revisions, filenames, sizes and SHA256 values are recorded in `src/ctrlcctrlv/catalog.py`.

Models and llama.cpp are downloaded separately through the application and are not embedded in the app bundle. Review the redistribution obligations, include the full required license texts, and complete signing/notarization before a public release.

