import geopandas as gpd
from shapely.geometry import LineString
from shapely.ops import nearest_points

REAL_TRIBUTARIES = [
    ("Ока", "oka", "Волга", "Волга", "Приток Волги"),
    ("Сура", "sura", "Волга", "Волга", "Приток Волги"),
    ("Ветлуга", "vetluga", "Волга", "Волга", "Приток Волги"),
    ("Шексна", "sheksna", "Волга", "Волга", "Приток Волги"),
    ("Молога", "mologa", "Волга", "Волга", "Приток Волги"),
    ("Кострома", "kostroma", "Волга", "Волга", "Приток Волги"),
    ("Унжа", "unzha", "Волга", "Волга", "Приток Волги"),
    ("Свияга", "sviyaga", "Волга", "Волга", "Приток Волги"),
    ("Самара", "samara", "Волга", "Волга", "Приток Волги"),
    ("Вятка", "vyatka", "Кама", "Кама", "Приток Камы"),
    ("Белая", "belaya", "Кама", "Кама", "Приток Камы"),
    ("Чусовая", "chusovaya", "Кама", "Кама", "Приток Камы"),
    ("Сылва", "sylva", "Кама", "Кама", "Приток Камы"),
    ("Уфа", "ufa", "Кама", "Кама", "Приток Камы"),
    ("Вишера", "vishera", "Кама", "Кама", "Приток Камы"),
]


SCHEMATIC_TRIBUTARIES = [
    {
        "id": "oka",
        "river": "Ока",
        "parent": "Волга",
        "flows_to": "Волга",
        "kind": "Приток Волги",
        "geometry": LineString([
            (36.10, 52.95),
            (38.30, 53.70),
            (40.60, 54.80),
            (43.95, 56.33),
        ]),
    },
    {
        "id": "sura",
        "river": "Сура",
        "parent": "Волга",
        "flows_to": "Волга",
        "kind": "Приток Волги",
        "geometry": LineString([
            (45.00, 53.20),
            (45.70, 54.10),
            (46.50, 55.00),
            (47.20, 55.95),
        ]),
    },
    {
        "id": "vetluga",
        "river": "Ветлуга",
        "parent": "Волга",
        "flows_to": "Волга",
        "kind": "Приток Волги",
        "geometry": LineString([
            (47.00, 58.50),
            (47.30, 57.60),
            (46.90, 56.90),
            (46.40, 56.35),
        ]),
    },
    {
        "id": "vyatka",
        "river": "Вятка",
        "parent": "Кама",
        "flows_to": "Кама",
        "kind": "Приток Камы",
        "geometry": LineString([
            (50.30, 58.60),
            (50.80, 57.70),
            (51.20, 56.80),
            (51.85, 55.70),
        ]),
    },
    {
        "id": "belaya",
        "river": "Белая",
        "parent": "Кама",
        "flows_to": "Кама",
        "kind": "Приток Камы",
        "geometry": LineString([
            (56.10, 54.70),
            (55.20, 55.10),
            (54.50, 55.45),
            (53.70, 55.80),
        ]),
    },
]


def build_network():

    print("Загружаем данные...")

    rivers = gpd.read_file("data/rivers.geojson")
    stations = gpd.read_file("data/stations.geojson")

    if "river" not in rivers.columns:
        raise ValueError("Нет поля 'river' в rivers.geojson")

    available_rivers = set(rivers["river"].astype(str))

    # ============================
    # 🔥 1. ВЫДЕЛЯЕМ РЕКИ
    # ============================

    volga = rivers[
        rivers["river"].astype(str).str.contains("Волга|Volga", case=False, na=False)
    ].geometry.union_all()

    kama = rivers[
        rivers["river"].astype(str).str.contains("Кама|Kama", case=False, na=False)
    ].geometry.union_all()

    print("Ищем точку слияния...")

    inter = volga.intersection(kama)

    if inter.is_empty:
        print("⚠️ Нет пересечения → ищем ближайшую точку")
        confluence = nearest_points(kama, volga)[1]
    else:
        if inter.geom_type == "Point":
            confluence = inter
        elif inter.geom_type == "MultiPoint":
            confluence = list(inter.geoms)[0]
        else:
            confluence = nearest_points(kama, volga)[1]

    # прижимаем к Волге
    confluence = nearest_points(confluence, volga)[1]

    # ============================
    # 🔥 2. НЕ РЕЖЕМ ВОЛГУ
    # ============================

    print("Используем Волгу как одну линию")

    # ============================
    # 🔥 3. УЗЛЫ
    # ============================

    print("Создаём узлы...")

    nodes_list = []

    for idx, row in stations.iterrows():
        geometry = row.geometry
        if str(row.get("object_type", "hub")) == "post":
            river_name = str(row.get("river", "") or "")
            river_geometry = rivers[
                rivers["river"].astype(str).str.contains(river_name, case=False, na=False)
            ].geometry
            if not river_geometry.empty:
                geometry = nearest_points(geometry, river_geometry.union_all())[1]

        nodes_list.append({
            "id": f"station_{idx}",
            "type": "station",
            "name": row.get("name", f"station_{idx}"),
            "river": row.get("river", ""),
            "db_station": row.get("db_station", ""),
            "object_type": row.get("object_type", "hub"),
            "river_section": row.get("river_section", ""),
            "sort_order": row.get("sort_order", 0),
            "geometry": geometry
        })

    nodes_list.append({
        "id": "confluence_1",
        "type": "confluence",
        "name": "Слияние Камы и Волги",
        "river": "Волга/Кама",
        "geometry": confluence
    })

    nodes = gpd.GeoDataFrame(nodes_list, geometry="geometry", crs="EPSG:4326")

    # ============================
    # 🔥 4. РЁБРА
    # ============================

    print("Создаём рёбра...")

    edges_list = [
        {
            "id": "volga",
            "river": "Волга",
            "parent": "Волга",
            "flows_to": "Каспийское море",
            "kind": "Главное русло",
            "geometry": volga
        },
        {
            "id": "kama",
            "river": "Кама",
            "parent": "Кама",
            "flows_to": "Волга",
            "kind": "Главное русло Камы",
            "geometry": kama
        }
    ]
    for river_name, river_id, parent_river, flows_to, kind in REAL_TRIBUTARIES:
        real_geometry = rivers[
            rivers["river"].astype(str).str.contains(river_name, case=False, na=False)
        ].geometry
        if not real_geometry.empty:
            edges_list.append({
                "id": river_id,
                "river": river_name,
                "parent": parent_river,
                "flows_to": flows_to,
                "kind": kind,
                "geometry": real_geometry.union_all(),
            })

    for tributary in SCHEMATIC_TRIBUTARIES:
        if tributary["river"] not in available_rivers:
            edges_list.append(tributary)

    edges = gpd.GeoDataFrame(edges_list, geometry="geometry", crs="EPSG:4326")

    # ============================
    # 🔥 5. СОХРАНЕНИЕ
    # ============================

    nodes.to_file("data/network_nodes.geojson", driver="GeoJSON")
    edges.to_file("data/network_edges.geojson", driver="GeoJSON")

    print("Готово:")
    print("data/network_nodes.geojson")
    print("data/network_edges.geojson")


if __name__ == "__main__":
    build_network()
