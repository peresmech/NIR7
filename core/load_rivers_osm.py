import geopandas as gpd
import ssl
from shapely.geometry import LineString
from shapely.ops import linemerge
from urllib import request

TARGET_RIVERS = [
    ("Волга", "Volga"),
    ("Кама", "Kama"),
    ("Ока", "Oka"),
    ("Сура", "Sura"),
    ("Ветлуга", "Vetluga"),
    ("Вятка", "Vyatka"),
    ("Белая", "Belaya"),
    ("Шексна", "Sheksna"),
    ("Молога", "Mologa"),
    ("Кострома", "Kostroma"),
    ("Унжа", "Unzha"),
    ("Свияга", "Sviyaga"),
    ("Самара", "Samara"),
    ("Чусовая", "Chusovaya"),
    ("Сылва", "Sylva"),
    ("Уфа", "Ufa"),
    ("Вишера", "Vishera"),
]


SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _overpass_query(query: str) -> dict:
    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter"
    ]

    for url in urls:
        try:
            print(f"Пробуем сервер: {url}")

            req = request.Request(
                url,
                data=query.encode("utf-8"),
                method="POST",
            )
            with request.urlopen(req, timeout=180, context=SSL_CONTEXT) as response:
                status_code = getattr(response, "status", 200)
                if status_code >= 400:
                    raise RuntimeError(f"HTTP {status_code}")
                payload = response.read().decode("utf-8")
            print("Успех!")

            import json
            return json.loads(payload)

        except Exception as e:
            print(f"Ошибка: {e}")
            continue

    raise RuntimeError("Все Overpass серверы не ответили")


def _build_relation_queries() -> str:
    blocks = []
    for ru_name, en_name in TARGET_RIVERS:
        blocks.extend([
            f'relation["type"="waterway"]["waterway"="river"]["name"~"^({ru_name}|{en_name})$"];',
            f'relation["type"="waterway"]["waterway"="river"]["name:ru"="{ru_name}"];',
        ])
    return "\n      ".join(blocks)


def download_rivers():

    print("Запрос основных рек бассейна Волги из OSM...")

    query = """
    [out:json][timeout:180];
    (
      {relation_queries}
    );
    out body;
    >;
    out body qt;
    """.format(relation_queries=_build_relation_queries())

    data = _overpass_query(query)
    elements = data["elements"]

    nodes = {}
    ways = {}
    relations = []

    for el in elements:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])
        elif el["type"] == "way":
            ways[el["id"]] = el.get("nodes", [])
        elif el["type"] == "relation":
            relations.append(el)

    features = []

    for rel in relations:
        tags = rel.get("tags", {})
        name = tags.get("name:ru") or tags.get("name", "")

        member_lines = []

        for member in rel.get("members", []):
            if member["type"] != "way":
                continue

            node_ids = ways.get(member["ref"], [])
            coords = [nodes[n] for n in node_ids if n in nodes]

            if len(coords) >= 2:
                member_lines.append(LineString(coords))

        if not member_lines:
            continue

        merged = linemerge(member_lines)

        features.append({"river": name, "geometry": merged})

    gdf = gpd.GeoDataFrame(features, geometry="geometry", crs="EPSG:4326")
    gdf = gdf.drop_duplicates(subset=["river"]).reset_index(drop=True)

    print(gdf["river"].value_counts())

    gdf.to_file("data/rivers.geojson", driver="GeoJSON")

    print("Сохранено: data/rivers.geojson")


if __name__ == "__main__":
    download_rivers()
