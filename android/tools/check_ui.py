"""Exercise Android navigation and a local queue on a fresh emulator."""

from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import wave
import xml.etree.ElementTree as ET


PACKAGE = "io.github.hyoreku.slynor.debug"
ACTIVITY = f"{PACKAGE}/io.github.hyoreku.slynor.MainActivity"
TRACKS = [f"Slynor-{name}.wav" for name in ("Alpha", "Beta", "Gamma")]


def adb(*args, timeout=24):
    return subprocess.check_output(["adb", *args], timeout=timeout)


def label_is(node, label):
    return label in (node.get("text"), node.get("content-desc"))


def wait_for(*labels, any_label=False):
    end = time.monotonic() + 35
    tree = None
    while time.monotonic() < end:
        try:
            adb("shell", "uiautomator", "dump", "/sdcard/slynor-ui.xml")
            tree = ET.fromstring(adb("exec-out", "cat", "/sdcard/slynor-ui.xml"))
            matches = (any(label_is(node, label) for node in tree.iter("node")) for label in labels)
            if (any if any_label else all)(matches):
                return tree
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ET.ParseError):
            pass
        time.sleep(.5)
    visible = [] if tree is None else [node.get("text") or node.get("content-desc") for node in tree.iter("node")]
    raise RuntimeError(f"Missing {labels}. Visible labels: {[label for label in visible if label]}")


def centers(tree, label):
    for node in tree.iter("node"):
        if label_is(node, label):
            left, top, right, bottom = map(int, re.findall(r"\d+", node.attrib["bounds"]))
            if right > left and bottom > top:
                yield ((left + right) // 2, (top + bottom) // 2)


def tap(tree, *labels, near=None):
    points = [point for label in labels for point in centers(tree, label)]
    if near is not None:
        y = next(centers(tree, near))[1]
        point = min(points, key=lambda point: abs(point[1] - y))
    else:
        point = max(points, key=lambda point: point[1])
    x, y = map(str, point)
    adb("shell", "input", "tap", x, y)


def track_action(track, action):
    tap(wait_for(track, "Действия для трека"), "Действия для трека", near=track)
    tap(wait_for(action), action)


def import_tracks():
    with TemporaryDirectory() as folder:
        adb("shell", "mkdir", "-p", "/sdcard/Download")
        for name in TRACKS:
            audio = Path(folder) / name
            with wave.open(str(audio), "wb") as sample:
                sample.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                sample.writeframes(b"\x00\x00" * 8000 * 120)
            remote = f"/sdcard/Download/{name}"
            adb("push", str(audio), remote)
            adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote}")

    roots = ("Show roots", "Show roots menu", "Показать корневые папки", "Открыть меню")
    downloads = ("Downloads", "Загрузки")
    for index, track in enumerate(TRACKS):
        add_label = "Добавить музыку" if index == 0 else "Добавить"
        tap(wait_for(add_label), add_label)
        picker = wait_for(track, *roots, any_label=True)
        if not list(centers(picker, track)):
            tap(picker, *roots)
            tap(wait_for(*downloads, any_label=True), *downloads)
        tap(wait_for(track), track)
        wait_for("Недавно добавленные", track)


def assert_queue(*tracks):
    counter = f"1 из {len(tracks)}"
    tree = wait_for(counter, *tracks)
    positions = [next(centers(tree, track))[1] for track in tracks]
    assert positions == sorted(positions), (tracks, positions)
    return tree


def wait_for_media_state(state):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        sessions = adb("shell", "dumpsys", "media_session").decode()
        if PACKAGE in sessions and re.search(rf"PlaybackState \{{state={state},", sessions):
            return
        time.sleep(.4)
    raise RuntimeError(f"Slynor media session did not reach state {state}")


def pause_active_playback():
    # Keep the progress indicator still while uiautomator waits for an idle window.
    wait_for_media_state(3)
    adb("shell", "cmd", "media_session", "dispatch", "pause")
    wait_for_media_state(2)


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    home = wait_for("Включите первую песню")
    (output / "home.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap(home, "Моя музыка")
    library = wait_for("Библиотека пуста")
    tap(library, "Очередь")
    queue = wait_for("Очередь пуста")
    tap(queue, "Главная")
    wait_for("Включите первую песню")

    alpha, beta, gamma = TRACKS
    import_tracks()
    tap(wait_for("Недавно добавленные", "Моя музыка"), "Моя музыка")
    wait_for("3 трека", *TRACKS)
    (output / "library.png").write_bytes(adb("exec-out", "screencap", "-p"))
    track_action(alpha, "В избранное")
    wait_for("В избранном")

    adb("shell", "am", "force-stop", PACKAGE)
    adb("shell", "am", "start", "-W", "-n", ACTIVITY)
    tap(wait_for("Недавно добавленные", "Моя музыка"), "Моя музыка")
    tap(wait_for("Избранное", "В избранном"), "Избранное")
    favorites = wait_for("1 трек", alpha, "В избранном")
    assert not list(centers(favorites, beta)), "A non-favorite track appears in Favorites"
    (output / "favorites.png").write_bytes(adb("exec-out", "screencap", "-p"))
    tap(favorites, alpha)
    pause_active_playback()
    tap(wait_for("Очередь"), "Очередь")
    assert_queue(alpha)
    tap(wait_for("Моя музыка"), "Моя музыка")
    tap(wait_for("Все треки"), "Все треки")
    track_action(beta, "В конец очереди")
    track_action(gamma, "Играть следующим")
    tap(wait_for("Очередь", "3 трека"), "Очередь")
    assert_queue(alpha, gamma, beta)
    (output / "queue.png").write_bytes(adb("exec-out", "screencap", "-p"))

    track_action(gamma, "В конец очереди")
    assert_queue(alpha, beta, gamma)
    track_action(gamma, "Убрать из очереди")
    assert_queue(alpha, beta)
    tap(wait_for(alpha), alpha)
    player = wait_for("Сейчас играет", "Следующий трек", "Воспроизвести")
    (output / "playing.png").write_bytes(adb("exec-out", "screencap", "-p"))
    tap(player, "Следующий трек")
    wait_for(beta, "Воспроизвести")

    tap(wait_for("Вернуться"), "Вернуться")
    tap(wait_for("Моя музыка"), "Моя музыка")
    tap(wait_for("3 трека", "Перемешать и включить"), "Перемешать и включить")
    pause_active_playback()
    tap(wait_for("Очередь"), "Очередь")
    wait_for("1 из 3", *TRACKS)
    print("Imported three tracks through the file picker, verified favorites persist and exercised queue playback and editing.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
