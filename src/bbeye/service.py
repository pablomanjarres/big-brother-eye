"""Start the Eye automatically at login: Windows Task Scheduler, macOS launchd, Linux systemd --user."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Big Brother Eye"
LAUNCHD_LABEL = "io.github.big-brother-eye"
SYSTEMD_UNIT = "big-brother-eye.service"


def _python(windowless: bool) -> str:
    executable = Path(sys.executable)
    if windowless and sys.platform == "win32":
        candidate = executable.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(executable)


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def windows_task_xml(python: str, user: str) -> str:
    # XML (not schtasks flags) is needed to remove the default 72-hour run limit and to restart on failure.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Big Brother Eye desk monitor</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(python)}</Command><Arguments>-m bbeye run</Arguments></Exec>
  </Actions>
</Task>
"""


def launchd_plist(python: str, log_dir: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{escape(python)}</string><string>-m</string><string>bbeye</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{escape(str(log_dir / "launchd.out.log"))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log_dir / "launchd.err.log"))}</string>
</dict>
</plist>
"""


def systemd_unit(python: str) -> str:
    return f"""[Unit]
Description=Big Brother Eye desk monitor

[Service]
ExecStart="{python}" -m bbeye run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def install(log_dir: Path) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as handle:
            handle.write(windows_task_xml(_python(windowless=True), user))
        try:
            _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", handle.name, "/F"])
            _run(["schtasks", "/Run", "/TN", TASK_NAME])
        finally:
            os.unlink(handle.name)
        return f"Scheduled task '{TASK_NAME}' installed and started. It starts at every login."
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"], capture_output=True)
        path.write_text(launchd_plist(_python(windowless=False), log_dir))
        _run(["launchctl", "bootstrap", domain, str(path)])
        return f"LaunchAgent installed at {path}. It starts at every login."
    path = Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(systemd_unit(_python(windowless=False)))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])
    return f"systemd user service installed at {path}."


def uninstall() -> str:
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], capture_output=True)
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        return f"Scheduled task '{TASK_NAME}' removed."
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture_output=True)
        path.unlink(missing_ok=True)
        return "LaunchAgent removed."
    subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], capture_output=True)
    (Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT).unlink(missing_ok=True)
    return "systemd user service removed."
