# GIS Development Handoff



Что внутри:

- `app/`
  - `gui_app.py` — основное настольное приложение на PyQt
  - `main.py` — генерация карты в `map.html`
  - `server.py` — простой web-режим
  - `article_map_window.py` — отдельное окно для карт под статьи

- `core/`
  - загрузка рек и станций
  - сборка речной сети
  - расчёт метрик и гистерезиса
  - служебные скрипты обработки

- `viz/`
  - `map.py` — основная карта Folium
  - `plots.py` — графики
  - `article_section_map.py` — отдельная публикационная карта

- `data/`
  - `hydro.db` — рабочая база наблюдений
  - `stations.csv`, `stations.geojson` — справочник объектов
  - `network_edges.geojson`, `network_nodes.geojson` — речная сеть и узлы
  - `rivers.geojson` — реки
  - `validation_issues.csv` — отчёт по качеству данных
  - `volga*.geojson` — эксперименты с площадной водой
  - `raw/` — исходные CSV по гидроузлам и гидропостам



Как запускать:

```bash
pip install -r requirements.txt
python3 app/gui_app.py
```

Что важно знать:

- в `stations.csv` уже есть и гидроузлы, и гидропосты;
- гидропосты `Вольск` и `Маркс` заведены как отдельные объекты;
- часть логики под статьи находится в `article_map_window.py` и `article_section_map.py`;
- если нужно пересобрать пространственные слои, использовать:

```bash
python3 core/load_stations.py
python3 core/build_network.py
```

Если нужно пересобрать БД из `data/raw/`:

```bash
python3 data/raw/import_csv.py
```
