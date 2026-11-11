from pathlib import Path

from mutagen import File


# Запуск программы: подготовка ресурсов, работа, завершение.
def main():
    raw_path = input("Вставь полный путь к проблемному файлу: ")
    path = Path(raw_path.strip().strip('"')).expanduser()

    try:
        print(f"\nПуть: {path.resolve()}")
        print(f"Существует: {path.exists()}")
        print(f"Это файл: {path.is_file()}")

        if not path.is_file():
            print("Проверка остановлена: файл не найден.")
            return

        size = path.stat().st_size
        print(f"Размер: {size} байт")

        if size == 0:
            print("Файл пустой — воспроизводить в нём нечего.")
            return

        with path.open("rb") as file:
            header = file.read(128)

        print(f"Первые 32 байта: {header[:32].hex(' ')}")

        beginning = header.lstrip().lower()

        if beginning.startswith((b"<!doctype html", b"<html")):
            print("Внимание: содержимое похоже на HTML, а не на аудио.")

        try:
            audio = File(str(path), easy=True)

            if audio is None:
                print("Mutagen не распознал аудиоформат.")
                return

            print(f"Распознанный тип: {type(audio).__name__}")

            length = getattr(audio.info, "length", None)
            print(f"Длительность: {length} секунд")

        except Exception as error:
            print(
                "Ошибка Mutagen: "
                f"{type(error).__name__}: {error}"
            )

    except OSError as error:
        print(
            "Ошибка доступа к файлу: "
            f"{type(error).__name__}: {error}"
        )


if __name__ == "__main__":
    main()
