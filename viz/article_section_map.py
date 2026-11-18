from pathlib import Path

import folium
import geopandas as gpd
from shapely.geometry import box


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_HTML = PROJECT_ROOT / "figures" / "article_saratov_section_map.html"


def _load_section_data(station_id="volgograd_hub"):
    nodes = gpd.read_file(DATA_DIR / "network_nodes.geojson")

    nodes = nodes[
        (nodes["type"] == "station")
        & (nodes["db_station"] == station_id)
    ].copy()
    nodes = nodes.sort_values(["sort_order", "name"]).reset_index(drop=True)

    section_bounds = nodes.total_bounds
    minx, miny, maxx, maxy = section_bounds
    # Use a generous extent first; final framing is easier to tune afterwards.
    bbox = box(minx - 0.6, miny - 0.45, maxx + 0.6, maxy + 0.45)

    return nodes, bbox


def create_article_section_map(output_path=OUTPUT_HTML, station_id="volgograd_hub"):
    nodes, bbox = _load_section_data(station_id)
    station = nodes.iloc[0]

    centroid = nodes.unary_union.centroid
    m = folium.Map(
        location=[centroid.y, centroid.x],
        zoom_start=11,
        zoom_snap=0.25,
        zoom_delta=0.25,
        tiles=None,
        control_scale=True,
    )

    folium.TileLayer(
        tiles="CartoDB Voyager",
        name="Base",
        overlay=False,
        control=False,
    ).add_to(m)

    for _, row in nodes.iterrows():
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=11,
            color="#17324b",
            fill=True,
            fill_color="#ffd166",
            fill_opacity=1.0,
            weight=3,
        ).add_to(m)
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            icon=folium.DivIcon(
                html=f'<div class="hub-label">{row["name"]}</div>',
                icon_size=(260, 28),
                icon_anchor=(-10, 16),
            ),
        ).add_to(m)

    minx, miny, maxx, maxy = bbox.bounds
    m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(18, 18))

    # English labels placed manually for a clean article figure.
    city_labels = [
        ("Volgograd", 48.7080, 44.5133, "city-label city-left"),
        ("Volzhsky", 48.7850, 44.7780, "city-label city-right"),
    ]
    for text, lat, lon, cls in city_labels:
        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f'<div class="{cls}">{text}</div>',
                icon_size=(120, 24),
                icon_anchor=(0, 0),
            ),
        ).add_to(m)

    m.get_root().html.add_child(
        folium.Element(
            """
    <style>
    .leaflet-control-attribution {
        display: none !important;
    }
    .leaflet-control-zoom a {
        width: 34px !important;
        height: 34px !important;
        line-height: 34px !important;
        font-size: 20px !important;
    }
    .hub-label {
        font-size: 18px;
        font-weight: 700;
        color: #17324b;
        white-space: nowrap;
        text-shadow:
            -1px -1px 0 #ffffff,
             1px -1px 0 #ffffff,
            -1px  1px 0 #ffffff,
             1px  1px 0 #ffffff,
             0 0 6px rgba(255, 255, 255, 0.95);
    }
    .city-label {
        font-size: 17px;
        color: #5d6670;
        white-space: nowrap;
        text-shadow:
            -1px -1px 0 rgba(255,255,255,0.95),
             1px -1px 0 rgba(255,255,255,0.95),
            -1px  1px 0 rgba(255,255,255,0.95),
             1px  1px 0 rgba(255,255,255,0.95);
    }
    .city-left {
        transform: translate(-10px, -2px);
    }
    .city-right {
        transform: translate(8px, -16px);
    }
    </style>
    """
        )
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(output_path))
    return output_path


if __name__ == "__main__":
    path = create_article_section_map()
    print(path)
