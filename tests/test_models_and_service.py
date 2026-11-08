import hashlib
import xml.dom.minidom
from pathlib import Path

import httpx
import pytest

from bbeye import service
from bbeye.vision import models
from bbeye.vision.models import ModelError, ModelFile, resolve_model


def fake_model(monkeypatch, content):
    entry = ModelFile(
        "tiny.onnx", "https://models.test/tiny.onnx", hashlib.sha256(content).hexdigest(), "MIT"
    )
    monkeypatch.setitem(models.KNOWN_MODELS, "tiny.onnx", entry)


def client(content, calls):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=content)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_once_and_verify(tmp_path, monkeypatch):
    fake_model(monkeypatch, b"weights")
    calls = []
    path = resolve_model("tiny.onnx", tmp_path, client(b"weights", calls))
    assert path.read_bytes() == b"weights"
    resolve_model("tiny.onnx", tmp_path, client(b"weights", calls))
    assert len(calls) == 1


def test_bad_checksum_is_refused(tmp_path, monkeypatch):
    fake_model(monkeypatch, b"weights")
    with pytest.raises(ModelError, match="checksum"):
        resolve_model("tiny.onnx", tmp_path, client(b"tampered", []))
    assert list(tmp_path.iterdir()) == []


def test_local_paths_and_unknown_names(tmp_path):
    custom = tmp_path / "custom.pt"
    custom.write_bytes(b"x")
    assert resolve_model(str(custom)) == custom
    with pytest.raises(ModelError):
        resolve_model("not-a-model.pt")


def test_service_templates_are_valid(tmp_path):
    xml.dom.minidom.parseString(
        service.windows_task_xml(r"C:\venv\pythonw.exe", r"DESK\me").replace("UTF-16", "UTF-8")
    )
    plist = service.launchd_plist("/venv/bin/python", tmp_path)
    xml.dom.minidom.parseString(plist)
    assert "<string>run</string>" in plist
    assert "-m bbeye run" in service.systemd_unit("/venv/bin/python")
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in service.windows_task_xml("p", "u")


def test_idle_server_service_templates():
    plist = service.launchd_plist("/venv/bin/python", Path("/logs"), service.IDLE)
    xml.dom.minidom.parseString(plist)
    assert "<string>idle-serve</string>" in plist and service.IDLE.launchd_label in plist
    assert "-m bbeye idle-serve" in service.windows_task_xml("p", "u", service.IDLE)
    assert "-m bbeye idle-serve" in service.systemd_unit("/venv/bin/python", service.IDLE)
    assert service.EYE.launchd_label != service.IDLE.launchd_label
