from __future__ import annotations

from starship_engine.apps.data_persist.settings import DataPersistSettings


class DataPersistApp:
    name = "data_persist"
    settings = DataPersistSettings()


app = DataPersistApp()
name = app.name
