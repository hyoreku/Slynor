"""Exercise Android navigation and a local queue on a fresh emulator."""

from pathlib import Path
import json
import re
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import wave
import xml.etree.ElementTree as ET
import zlib


PACKAGE = "io.github.hyoreku.slynor.debug"
ACTIVITY = f"{PACKAGE}/io.github.hyoreku.slynor.MainActivity"
TRACKS = [f"Slynor-{name}.wav" for name in ("Alpha", "Beta", "Gamma")]
FIXTURE_FOLDER = "0-slynor-ui"
COVER_FIXTURE = "Slynor-cover.png"
PLAYER = "Открыть экран текущего трека"


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


def bounds(node):
    return tuple(map(int, re.findall(r"\d+", node.attrib["bounds"])))


def centers(tree, label):
    for node in tree.iter("node"):
        if label_is(node, label):
            left, top, right, bottom = bounds(node)
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
    label = "Действия для трека"
    tree = wait_for(label)
    if not list(centers(tree, track)):
        scrollable = next(node for node in tree.iter("node") if node.get("scrollable") == "true")
        left, top, right, bottom = bounds(scrollable)
        x = str((left + right) // 2)
        start = str((top + 3 * bottom) // 4)
        end = str((3 * top + bottom) // 4)
        adb("shell", "input", "swipe", x, start, x, end, "400")
        tree = wait_for(track, label)
    tap(tree, label, near=track)
    tap(wait_for(action), action)


def pick_document(filename):
    roots = "Show roots"
    storage = adb("shell", "getprop", "ro.product.model").decode().strip()
    picker = wait_for(filename, roots, storage, any_label=True)
    if not list(centers(picker, filename)):
        if not list(centers(picker, storage)):
            tap(picker, roots)
            picker = wait_for(storage)
        tap(picker, storage)
        tap(wait_for(FIXTURE_FOLDER), FIXTURE_FOLDER)
    tap(wait_for(filename), filename)


def import_tracks():
    with TemporaryDirectory() as folder:
        remote_folder = f"/sdcard/{FIXTURE_FOLDER}"
        adb("shell", "mkdir", "-p", remote_folder)
        for name in TRACKS:
            audio = Path(folder) / name
            with wave.open(str(audio), "wb") as sample:
                sample.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                sample.writeframes(b"\x00\x00" * 8000 * 120)
            adb("push", str(audio), f"{remote_folder}/{name}")

        def chunk(kind, data):
            payload = kind + data
            return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

        cover = Path(folder) / COVER_FIXTURE
        cover.write_bytes(
            b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 24, 24, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes((105, 74, 164)) * 24) * 24))
            + chunk(b"IEND", b"")
        )
        adb("push", str(cover), f"{remote_folder}/{COVER_FIXTURE}")

    for index, track in enumerate(TRACKS):
        add_label = "Добавить музыку" if index == 0 else "Добавить"
        tap(wait_for(add_label), add_label)
        pick_document(track)
        wait_for("Недавно добавленные", track)


def open_player():
    tap(wait_for(PLAYER), PLAYER)
    return wait_for("Сейчас играет", "Свернуть плеер")


def open_queue(size):
    button = f"Очередь · {size}"
    tree = wait_for(PLAYER, button, any_label=True)
    if list(centers(tree, PLAYER)):
        tree = open_player()
    if not list(centers(tree, button)):
        tree = wait_for(button)
    tap(tree, button)
    wait_for("Очередь", "Закрыть очередь")


def close_queue():
    tap(wait_for("Закрыть очередь"), "Закрыть очередь")


def collapse_player():
    tap(wait_for("Свернуть плеер"), "Свернуть плеер")
    return wait_for("Моя музыка", "Плейлисты")


def saved_playlist(name):
    library = ET.fromstring(adb("exec-out", "run-as", PACKAGE, "cat", "shared_prefs/library.xml"))
    rows = json.loads(library.find("./string[@name='playlists']").text)
    return next(playlist for playlist in rows if playlist["name"] == name)


def assert_queue(*tracks):
    counter = f"1 из {len(tracks)}"
    tree = wait_for(counter, *tracks)
    positions = [next(centers(tree, track))[1] for track in tracks]
    assert positions == sorted(positions), (tracks, positions)
    return tree


def wait_for_media_state(state):
    end = time.monotonic() + 15
    pattern = re.compile(rf"PlaybackState \{{state=(?:{state}|[A-Z_]+\({state}\)),")
    states = []
    while time.monotonic() < end:
        sessions = adb("shell", "dumpsys", "media_session").decode()
        states = [line.strip() for line in sessions.splitlines() if "PlaybackState {" in line]
        if PACKAGE in sessions and any(pattern.search(line) for line in states):
            return
        time.sleep(.4)
    raise RuntimeError(f"Slynor media session did not reach state {state}. Reported states: {states}")


def pause_active_playback():
    # Keep the progress indicator still while uiautomator waits for an idle window.
    wait_for_media_state(3)
    adb("shell", "cmd", "media_session", "dispatch", "pause")
    wait_for_media_state(2)


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    adb("shell", "wm", "size", "480x920")
    adb("shell", "wm", "density", "160")
    adb("shell", "settings", "put", "global", "animator_duration_scale", "1")
    home = wait_for("Включите первую песню")
    assert not list(centers(home, "Очередь")), "Queue appears in the bottom navigation"
    (output / "home.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap(home, "Моя музыка")
    library = wait_for("Библиотека пуста")
    tap(library, "Главная")
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
    tap(favorites, alpha)
    pause_active_playback()
    open_queue(1)
    assert_queue(alpha)
    close_queue()
    collapse_player()
    tap(wait_for("Все треки"), "Все треки")
    track_action(beta, "В конец очереди")
    track_action(gamma, "Играть следующим")
    open_queue(3)
    assert_queue(alpha, gamma, beta)
    (output / "queue.png").write_bytes(adb("exec-out", "screencap", "-p"))

    track_action(gamma, "В конец очереди")
    assert_queue(alpha, beta, gamma)
    track_action(gamma, "Убрать из очереди")
    assert_queue(alpha, beta)
    close_queue()
    player = wait_for("Сейчас играет", "Следующий трек", "Воспроизвести")
    assert not list(centers(player, "Таймер сна"))
    (output / "playing.png").write_bytes(adb("exec-out", "screencap", "-p"))
    tap(player, "Следующий трек")
    wait_for(beta, "Воспроизвести")

    collapse_player()
    tap(wait_for("3 трека", "Перемешать и включить"), "Перемешать и включить")
    pause_active_playback()
    open_queue(3)
    shuffled = wait_for("1 из 3", *TRACKS)
    queue_order = sorted(TRACKS, key=lambda track: next(centers(shuffled, track))[1])
    close_queue()
    print("Verified queue opens over Now Playing and supports reordering and removal.", flush=True)

    tap(wait_for("1×"), "1×")
    tap(wait_for("1,5×"), "1,5×")
    tap(wait_for("Без повтора", "1,5×"), "Без повтора")
    tap(wait_for("Вся очередь"), "Вся очередь")
    tap(wait_for("Эквалайзер"), "Эквалайзер")
    tap(wait_for("Пресеты эквалайзера"), "Пресеты эквалайзера")
    tap(wait_for("Бас"), "Бас")
    tap(wait_for("-6 дБ", "+6 дБ", "Готово"), "Готово")

    tap(collapse_player(), "Плейлисты")
    tap(wait_for("Плейлистов пока нет", "Создать"), "Создать")
    tap(wait_for("Новый плейлист", "Выбрать обложку"), "Выбрать обложку")
    pick_document(COVER_FIXTURE)
    tap(wait_for("Сменить обложку", "Название"), "Название")
    adb("shell", "input", "text", "Roadtrip")
    tap(wait_for("Сохранить"), "Сохранить")
    tap(wait_for("Roadtrip", "Плейлист пуст", "Добавить треки"), "Добавить треки")
    tap(wait_for(alpha, beta, gamma), alpha)
    tap(wait_for(beta), beta)
    tap(wait_for("Добавить (2)"), "Добавить (2)")
    playlist = wait_for("Roadtrip", alpha, beta, "2 трека")
    assert next(centers(playlist, alpha))[1] < next(centers(playlist, beta))[1]
    track_action(beta, "Переместить выше")
    playlist = wait_for("Roadtrip", alpha, beta)
    assert next(centers(playlist, beta))[1] < next(centers(playlist, alpha))[1]
    (output / "playlist.png").write_bytes(adb("exec-out", "screencap", "-p"))

    tap(wait_for("Настройки"), "Настройки")
    settings = wait_for("Тема приложения", "Тёмная", "Визуализатор")
    for label in ("Эквалайзер", "Скорость", "Повтор", "Таймер сна", "1,5×", "Вся очередь"):
        assert not list(centers(settings, label)), f"Unexpected playback control in Settings: {label}"
    (output / "settings.png").write_bytes(adb("exec-out", "screencap", "-p"))
    tap(settings, "Тёмная")
    tap(wait_for("Светлая"), "Светлая")
    wait_for("Тема приложения", "Светлая")

    adb("shell", "am", "force-stop", PACKAGE)
    adb("shell", "am", "start", "-W", "-n", ACTIVITY)
    open_queue(3)
    assert_queue(*queue_order)
    close_queue()
    wait_for("1,5×", "Вся очередь")
    tap(wait_for("Эквалайзер"), "Эквалайзер")
    tap(wait_for("-6 дБ", "+6 дБ", "Готово"), "Готово")
    tap(collapse_player(), "Плейлисты")
    tap(wait_for("Roadtrip"), "Roadtrip")
    playlist = wait_for("Roadtrip", alpha, beta)
    assert next(centers(playlist, beta))[1] < next(centers(playlist, alpha))[1]
    cover = saved_playlist("Roadtrip")["cover"]
    image = adb("exec-out", "run-as", PACKAGE, "cat", f"files/playlist-covers/{cover}.jpg")
    assert image.startswith(b"\xff\xd8"), "Playlist cover was not saved locally as a JPEG"
    tap(wait_for("Редактировать плейлист"), "Редактировать плейлист")
    wait_for("Редактирование плейлиста", "Сменить обложку")
    (output / "playlist-editor.png").write_bytes(adb("exec-out", "screencap", "-p"))
    tap(wait_for("Убрать обложку"), "Убрать обложку")
    tap(wait_for("Выбрать обложку", "Сохранить"), "Сохранить")
    wait_for("Roadtrip", alpha, beta)
    assert "cover" not in saved_playlist("Roadtrip"), "The removed cover is still assigned"
    print("Verified persistent playlist artwork, track order, theme and playback controls.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
