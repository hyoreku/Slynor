"""Check navigation on a fresh Android emulator and capture the visible screens."""

from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def adb(*args, timeout=24):
    return subprocess.check_output(["adb", *args], timeout=timeout)


def wait_for(label):
    end = time.monotonic() + 35
    while time.monotonic() < end:
        try:
            adb("shell", "uiautomator", "dump", "/sdcard/slynor-ui.xml")
            tree = ET.fromstring(adb("exec-out", "cat", "/sdcard/slynor-ui.xml"))
            if any(element.get("text") == label for element in tree.iter("node")):
                return tree
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ET.ParseError):
            pass
        time.sleep(.5)
    raise RuntimeError(f"Element did not appear: {label}")


def tap_tab(tree, label):
    bounds = [
        [int(value) for value in re.findall(r"\d+", element.get("bounds", ""))]
        for element in tree.iter("node")
        if element.get("text") == label
    ]
    left, top, right, bottom = max((rect for rect in bounds if len(rect) == 4), key=lambda rect: rect[1])
    adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    home = wait_for("Включите первую песню")
    (output / "home.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap_tab(home, "Моя музыка")
    library = wait_for("Библиотека пуста")
    (output / "library.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap_tab(library, "Очередь")
    queue = wait_for("Очередь пуста")
    (output / "queue.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap_tab(queue, "Главная")
    wait_for("Включите первую песню")
    print("Opened home, library and queue on the Android emulator.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
