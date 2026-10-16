"""Runs the Python app's own code for the Rust compatibility tests.

Reads one JSON command from stdin and prints a JSON answer.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path


def settings_save(args):
    from ctrlcctrlv.settings import Settings
    Settings(**args["values"]).save(Path(args["directory"]))
    return {}


def settings_load(args):
    from ctrlcctrlv.settings import Settings
    return asdict(Settings.load(Path(args["directory"])))


def history_add(args):
    from ctrlcctrlv.history import History
    history = History(Path(args["directory"]))
    return [history.add(*item) for item in args["items"]]


def history_entries(args):
    from ctrlcctrlv.history import History
    history = History(Path(args["directory"]))
    return [asdict(entry) for entry in history.entries(args.get("search", ""))]


def history_delete(args):
    from ctrlcctrlv.history import History
    entry = History(Path(args["directory"])).delete(args["id"])
    return asdict(entry) if entry else None


def lighton(args):
    from ctrlcctrlv.ocr import clean_lighton_code
    return [clean_lighton_code(text) for text in args["texts"]]


def payload(args):
    import httpx
    from ctrlcctrlv.core import Operation
    from ctrlcctrlv.ocr import OCRProvider
    captured = []

    def receive(request):
        captured.append(json.loads(request.content))
        captured.append(dict(request.headers))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"text": "x"}'}}]})

    provider = OCRProvider(args["endpoint"], args["model"], args["key"], args["max_tokens"], args["local"], args["mode"])
    provider.recognize(args["png"].encode("latin-1"), Operation(), httpx.MockTransport(receive),
                       prompt=args.get("prompt"), context=args.get("context"))
    return {"payload": captured[0], "authorization": captured[1].get("authorization")}


def http_errors(args):
    from ctrlcctrlv.ocr import http_error
    return [http_error(status, body.encode()) for status, body in args["cases"]]


def hotkeys(args):
    from ctrlcctrlv.hotkeys import parse_hotkey
    results = []
    for value in args["values"]:
        try:
            modifiers, key = parse_hotkey(value)
            results.append({"modifiers": sorted(modifiers), "key": key})
        except ValueError as error:
            results.append({"error": str(error)})
    return results


def dumps(args):
    return [json.dumps(value, ensure_ascii=False) for value in args["values"]]


def float_repr(args):
    return [repr(value) for value in args["values"]]


def engine_binary(args):
    from ctrlcctrlv.runtime import LocalRuntime
    binary = LocalRuntime(Path(args["directory"])).binary
    return str(binary) if binary else None


def model_installed(args):
    from ctrlcctrlv.runtime import LocalRuntime
    return LocalRuntime(Path(args["directory"])).installed(args["model"])


def write_model_receipt(args):
    # The receipt code from LocalRuntime.install, after the downloads.
    from ctrlcctrlv.catalog import MODELS
    model = MODELS[args["model"]]
    directory = Path(args["directory"]) / "models" / args["model"]
    receipt = {f.filename: {"sha256": f.sha256, "size": f.size,
        "mtime_ns": (directory / f.filename).stat().st_mtime_ns} for f in model.files}
    (directory / "verified.json").write_text(json.dumps(receipt))
    return {}


def session_key_check(args):
    import base64
    key = args["key"]
    return len(key) == 43 and len(base64.urlsafe_b64decode(key + "=")) == 32


def available(args):
    import ctrlcctrlv.ocr  # noqa: F401  (needs httpx)
    import ctrlcctrlv.runtime  # noqa: F401
    return True


if __name__ == "__main__":
    command = json.load(sys.stdin)
    print(json.dumps(globals()[command.pop("command")](command), ensure_ascii=False))
