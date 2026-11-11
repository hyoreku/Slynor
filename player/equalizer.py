"""Конфигурация эквалайзера: строгая проверка и атомарное сохранение отдельно от библиотеки."""
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4
import re

SPEED_RATES = {"standard": 1.0, "speedup": 1.25, "slowed": 0.8}
DEFAULT_EQ = {"enabled": False, "preamp": 0.0, "bands": [0.0] * 10, "speed_mode": "standard"}
# Собственные умеренные стартовые пресеты; это не автоматическая защита от клиппинга.
PRESETS = [
    {"id": "flat", "name": "Ровный", "preamp": 0, "bands": [0]*10},
    {"id": "bass", "name": "Бас", "preamp": -6, "bands": [6,4,2,0,0,0,0,0,0,0]},
    {"id": "voice", "name": "Вокал", "preamp": -4, "bands": [-3,-2,0,2,3,4,2,0,-1,-2]},
    {"id": "rock", "name": "Рок", "preamp": -5, "bands": [4,3,1,-1,-2,1,3,4,3,2]},
    {"id": "soft", "name": "Мягкий", "preamp": -2, "bands": [2,1,0,0,0,-1,-2,-3,-3,-4]},
]


def validate_equalizer(data):
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError("Некорректное включение эквалайзера.")
    def gain(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not -20 <= value <= 20:
            raise ValueError("Усиление должно быть числом от −20 до +20 дБ.")
        return float(value)
    bands = data.get("bands")
    if not isinstance(bands, list) or len(bands) != 10:
        raise ValueError("Эквалайзер требует ровно 10 полос.")
    mode = data.get("speed_mode", "standard")
    if not isinstance(mode, str) or mode not in SPEED_RATES:
        raise ValueError("Неизвестный режим скорости.")
    return {"enabled": data["enabled"], "preamp": gain(data.get("preamp")), "bands": [gain(v) for v in bands], "speed_mode": mode}


class EqualizerController:
    """Один lock сериализует предпросмотр/сохранение, защищая от устаревшей записи."""
    def __init__(self, engine, data_dir):
        self.engine = engine
        self.path = Path(data_dir) / "equalizer.json"
        self.lock = RLock()
        self.saved = deepcopy(DEFAULT_EQ)
        self.custom_presets = []
        self.warning = ""
        self.writable = True
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
                    raise ValueError("Неизвестная версия эквалайзера.")
                self.saved = validate_equalizer(data)
                items = data.get("custom_presets", [])
                if not isinstance(items, list) or len(items) > 100:raise ValueError("Некорректные собственные пресеты.")
                ids = set()
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", item["id"]) or item["id"] in ids:
                        raise ValueError("Некорректный ID пресета.")
                    name = item.get("name")
                    if not isinstance(name, str) or not name.strip() or len(name) > 80:raise ValueError("Некорректное имя пресета.")
                    config = validate_equalizer({**item, "enabled": True})
                    ids.add(item["id"])
                    self.custom_presets.append({"id": item["id"], "name": name.strip(), "preamp": config["preamp"], "bands": config["bands"], "speed_mode": config["speed_mode"], "custom": True})
        except (ValueError, OSError, UnicodeError) as error:
            self.writable = False
            self.warning = "Не удалось прочитать equalizer.json. Файл не изменён. " + str(error)
        try:
            self.frequencies = self.engine.get_equalizer_frequencies()
            self.available = True
            # Не пытаемся активировать повреждённые настройки.
            self.engine.set_equalizer(self.saved if self.writable else DEFAULT_EQ)
        except (RuntimeError, AttributeError, OSError) as error:
            self.available = False
            self.frequencies = []
            self.warning = "Эквалайзер недоступен в этой сборке VLC. " + str(error)

    def snapshot(self):
        with self.lock:
            return {"available": self.available, "writable": self.writable,
                    "current": self.engine.get_equalizer(), "saved": deepcopy(self.saved),
                    "frequencies": self.frequencies, "presets": deepcopy(PRESETS + self.custom_presets), "warning": self.warning}

    def apply(self, data):
        config = validate_equalizer(data)
        with self.lock:
            if not self.available or not self.writable:
                raise RuntimeError(self.warning or "Эквалайзер недоступен.")
            self.engine.set_equalizer(config)
            return self.snapshot()

    def save(self, expected):
        config = validate_equalizer(expected)
        with self.lock:
            if not self.available or not self.writable:
                raise RuntimeError(self.warning or "Эквалайзер недоступен.")
            # Записываем только реально подтверждённый текущий звук, не старую UI-копию.
            if config != self.engine.get_equalizer():
                raise ValueError("Настройки изменились. Сначала примените текущие значения.")
            self._write(config, self.custom_presets)
            self.saved = deepcopy(config)
            return self.snapshot()

    def _write(self, config, presets):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name("equalizer." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"version": 1, **config, "custom_presets": presets}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    # Именованный пресет хранит усиления, но не перезаписывает несохранённый current.
    def create_preset(self, name, config):
        config = validate_equalizer(config)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("Название пресета: от 1 до 80 символов.")
        with self.lock:
            if not self.available or not self.writable:raise RuntimeError(self.warning)
            if config != self.engine.get_equalizer():raise ValueError("Дождитесь применения эквалайзера.")
            if len(self.custom_presets) >= 100:raise ValueError("Достигнут предел 100 собственных пресетов.")
            if any(p["name"].casefold() == name.strip().casefold() for p in self.custom_presets):
                raise ValueError("Собственный пресет с таким именем уже есть.")
            item = {"id": uuid4().hex, "name": name.strip(), "preamp": config["preamp"], "bands": config["bands"], "speed_mode": config["speed_mode"], "custom": True}
            candidate = [*self.custom_presets, item]
            self._write(self.saved, candidate)
            self.custom_presets = candidate
            return {**self.snapshot(), "created_id": item["id"]}

    def delete_preset(self, preset_id):
        with self.lock:
            if not self.available or not self.writable:raise RuntimeError(self.warning)
            candidate = [p for p in self.custom_presets if p["id"] != preset_id]
            if len(candidate) == len(self.custom_presets):raise ValueError("Собственный пресет не найден.")
            self._write(self.saved, candidate)
            self.custom_presets = candidate
            return self.snapshot()
