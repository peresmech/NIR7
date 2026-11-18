import geopandas as gpd


def extract_volga_kama():

    print("Загружаем HydroRIVERS...")

    gdf = gpd.read_file("data/hydrorivers/HydroRIVERS_v10_eu.shp")

    print("Всего сегментов:", len(gdf))

    # ================================
    # 🔥 1. ОБРЕЗКА ПО КОРИДОРУ ВОЛГИ
    # ================================
    # (узкий bbox вдоль Волги и Камы)

    rivers = gdf.cx[45:55, 53:59]

    print("После bbox:", len(rivers))

    # ================================
    # 🔥 2. УБИРАЕМ МЕЛКИЙ МУСОР
    # ================================

    rivers = rivers[rivers["LENGTH_KM"] > 30]

    print("После фильтра длины:", len(rivers))

    # ================================
    # 🔥 3. ОСТАВЛЯЕМ ТОЛЬКО КРУПНЫЕ РЕКИ
    # ================================

    rivers = rivers[rivers["ORD_FLOW"] >= 4]

    print("После фильтра порядка:", len(rivers))

    # ================================
    # 🔥 4. СОХРАНЕНИЕ
    # ================================

    rivers[["geometry"]].to_file("data/rivers.geojson", driver="GeoJSON")

    print("Сохранено: data/rivers.geojson")


if __name__ == "__main__":
    extract_volga_kama()