import pandas as pd
import geopandas as gpd


def load_stations():
    df = pd.read_csv("data/stations.csv")

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df.lon, df.lat),
        crs="EPSG:4326"
    )

    gdf.to_file("data/stations.geojson", driver="GeoJSON")

    print("Готово: data/stations.geojson")


if __name__ == "__main__":
    load_stations()