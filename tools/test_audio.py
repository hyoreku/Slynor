import time
from pathlib import Path

import vlc


# Запуск программы: подготовка ресурсов, работа, завершение.
def main():
    raw_path = input("Вставь полный путь к музыкальному файлу: ")
    file_path = Path(raw_path.strip().strip('"'))

    if not file_path.is_file():
        print("Файл не найден. Проверь путь и запусти программу снова.")
        return

    instance = vlc.Instance("--no-video")
    if instance is None:
        print("Не удалось создать движок VLC.")
        return

    player = None
    media = None

    try:
        player = instance.media_player_new()
        media = instance.media_new_path(str(file_path.resolve()))

        player.set_media(media)
        player.audio_set_volume(50)

        if player.play() == -1:
            print("VLC не смог запустить воспроизведение.")
            return

        # Воспроизведение запускается не мгновенно.
        # Даём движку до пяти секунд на открытие файла.
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            state = player.get_state()

            if state == vlc.State.Playing:
                break

            if state in (vlc.State.Error, vlc.State.Ended):
                break

            time.sleep(0.1)

        state = player.get_state()

        if state == vlc.State.Error:
            print("Ошибка воспроизведения. Попробуй другой MP3-файл.")
            return

        if state == vlc.State.Ended:
            print("Воспроизведение уже завершилось. Попробуй более длинный трек.")
            return

        if state != vlc.State.Playing:
            print(
                f"Воспроизведение не началось за пять секунд. Состояние: {state}")
            return

        print(f"\nВоспроизводится: {file_path.name}")
        print("Громкость плеера: 50%.")
        input("Нажми Enter, чтобы остановить воспроизведение.\n")

    finally:
        if player is not None:
            player.stop()
            player.release()

        if media is not None:
            media.release()

        instance.release()
        print("Аудиодвижок закрыт.")


if __name__ == "__main__":
    main()
