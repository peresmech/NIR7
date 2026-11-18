import folium
import geopandas as gpd
from urllib.parse import quote


def create_map(edges_path, stations_path, overview="all", selected_station_id=None):

    # ===== БАЗОВАЯ КАРТА =====
    m = folium.Map(
        location=[56.0, 49.0],
        zoom_start=5,
        zoom_snap=0.5,
        zoom_delta=0.5,
        tiles=None,
        control_scale=True
    )

    # ===== ПОДЛОЖКИ =====
    folium.TileLayer("CartoDB positron", name="Светлая карта").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Уличная карта").add_to(m)

    # ===== ДАННЫЕ =====
    edges = gpd.read_file(edges_path) if edges_path else None
    stations = gpd.read_file(stations_path) if stations_path else None

    def filter_edges(data):
        if data is None or overview == "all":
            return data
        if overview == "volga":
            return data[(data["river"] == "Волга") | (data["parent"] == "Волга")]
        if overview == "kama":
            return data[(data["river"] == "Кама") | (data["parent"] == "Кама")]
        if overview == "volga_tributaries":
            return data[(data["parent"] == "Волга") & (data["river"] != "Волга")]
        if overview == "kama_tributaries":
            return data[(data["parent"] == "Кама") & (data["river"] != "Кама")]
        return data

    def filter_stations(data):
        if data is None:
            return data
        data = data[data["type"] == "station"].copy()
        if overview in {"volga", "volga_tributaries"}:
            return data[data["river"] == "Волга"]
        if overview in {"kama", "kama_tributaries"}:
            return data[data["river"] == "Кама"]
        return data

    edges = filter_edges(edges)
    stations = filter_stations(stations)

    # ===== СТИЛЬ РЕК =====
    def style_function(feature):
        river = feature["properties"].get("river", "")
        parent = feature["properties"].get("parent", "")

        if river == "Волга":
            return {"color": "#0D47A1", "weight": 6, "opacity": 0.95}

        if river == "Кама":
            return {"color": "#00838F", "weight": 6, "opacity": 0.95}

        if parent == "Волга":
            return {
                "color": "#7E57C2",
                "weight": 4,
                "opacity": 0.9,
            }

        if parent == "Кама":
            return {
                "color": "#43A047",
                "weight": 4,
                "opacity": 0.9,
            }

        return {"color": "gray", "weight": 2}

    # ===== РЕКИ =====
    if edges is not None:
        rivers_layer = folium.FeatureGroup(name="Реки")

        folium.GeoJson(
            edges,
            style_function=style_function,
            tooltip=folium.GeoJsonTooltip(
                fields=["river", "kind", "flows_to"],
                aliases=["Река:", "Тип:", "Впадает в:"],
            ),
        ).add_to(rivers_layer)

        rivers_layer.add_to(m)

    # ===== ГИДРОПОСТЫ =====
    if stations is not None:
        hubs_layer = folium.FeatureGroup(name="Гидроузлы")
        posts_layer = folium.FeatureGroup(name="Гидропосты")

        for _, row in stations.iterrows():
            if row.get("type") != "station":
                continue

            object_type = str(row.get("object_type", "hub") or "hub")
            is_hub = object_type == "hub"
            name = str(row.get("name", "Объект"))
            station_key = str(row.get("db_station") or name)
            station_href = quote(station_key)
            label_href = quote(name)
            popup_type = "Гидроузел" if is_hub else "Гидропост"
            popup_section = str(row.get("river_section", "") or "")

            popup_html = f"""
            <div class="station-card">
                <div class="station-name">{name}</div>
                <div class="station-meta">{popup_type}</div>
                {f'<div class="station-meta">Участок: {popup_section}</div>' if popup_section else ''}
                <a class="station-link" href="pyqt://station/{station_href}?label={label_href}">Открыть график</a>
            </div>
            """

            is_selected = selected_station_id and station_key == selected_station_id
            marker = folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=(10 if is_hub else 9) if is_selected else (7 if is_hub else 7),
                color="#0f1720" if is_selected else ("#16324f" if is_hub else "#ffffff"),
                fill=True,
                fill_color="#ffd166" if is_selected else ("#0A55C4" if is_hub else "#d62828"),
                fill_opacity=1.0 if is_selected else 0.92,
                weight=3 if is_selected else (2 if is_hub else 3),
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=(name if is_hub else f"г/п {name}"),
            )
            marker.add_to(hubs_layer if is_hub else posts_layer)

            if is_hub and is_selected:
                folium.Marker(
                    location=[row.geometry.y, row.geometry.x],
                    icon=folium.DivIcon(
                        html=f'<div class="hub-label">{name}</div>',
                        icon_size=(180, 24),
                        icon_anchor=(-12, 12),
                    ),
                ).add_to(hubs_layer)
            elif not is_hub:
                folium.Marker(
                    location=[row.geometry.y, row.geometry.x],
                    icon=folium.DivIcon(
                        html=f'<div class="post-label">г/п {name}</div>',
                        icon_size=(120, 24),
                        icon_anchor=(-8, 10),
                    ),
                ).add_to(posts_layer)

        hubs_layer.add_to(m)
        posts_layer.add_to(m)

    # ===== КОНТРОЛЬ СЛОЁВ =====
    folium.LayerControl(collapsed=True).add_to(m)

    visible_geometries = []
    if edges is not None and not edges.empty:
        visible_geometries.extend(list(edges.geometry))
    if stations is not None and not stations.empty:
        visible_geometries.extend(list(stations.geometry))

    if selected_station_id and stations is not None and not stations.empty:
        selected_rows = stations[stations["db_station"] == selected_station_id]
        if not selected_rows.empty:
            selected_point = selected_rows.iloc[0].geometry
            lat = selected_point.y
            lon = selected_point.x
            m.fit_bounds(
                [[lat - 0.45, lon - 0.65], [lat + 0.45, lon + 0.65]],
                padding=(24, 24),
            )
        elif visible_geometries:
            minx = min(geom.bounds[0] for geom in visible_geometries)
            miny = min(geom.bounds[1] for geom in visible_geometries)
            maxx = max(geom.bounds[2] for geom in visible_geometries)
            maxy = max(geom.bounds[3] for geom in visible_geometries)
            m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(24, 24))
    elif visible_geometries:
        minx = min(geom.bounds[0] for geom in visible_geometries)
        miny = min(geom.bounds[1] for geom in visible_geometries)
        maxx = max(geom.bounds[2] for geom in visible_geometries)
        maxy = max(geom.bounds[3] for geom in visible_geometries)
        m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(24, 24))

    m.get_root().html.add_child(folium.Element("""
    <style>
    .leaflet-control-layers-expanded {
        font-size: 15px;
        padding: 10px 12px;
        min-width: 180px;
        border-radius: 14px;
        border: 1px solid #d6e1ec;
        box-shadow: 0 12px 28px rgba(15, 23, 32, 0.12);
    }
    .leaflet-control-layers label {
        font-size: 15px;
        line-height: 1.5;
    }
    .leaflet-control-zoom a {
        width: 38px !important;
        height: 38px !important;
        line-height: 38px !important;
        font-size: 22px !important;
    }
    .leaflet-control-attribution {
        display: none !important;
    }
    .leaflet-tooltip {
        font-size: 15px !important;
        font-weight: 600;
        color: #1d2d3d;
        padding: 8px 10px;
        border-radius: 10px;
        border: 1px solid #d6e1ec;
        box-shadow: 0 8px 20px rgba(15, 23, 32, 0.12);
    }
    .station-link {
        display: inline-block;
        margin-top: 10px;
        padding: 10px 14px;
        font-size: 15px;
        font-weight: 600;
        color: #17324b;
        background: #f4c95d;
        border: 1px solid #e2b84a;
        border-radius: 10px;
        text-decoration: none;
        box-shadow: 0 6px 14px rgba(120, 93, 24, 0.18);
    }
    .station-card {
        min-width: 180px;
        padding: 8px 4px 2px 4px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .station-name {
        font-size: 15px;
        font-weight: 700;
        color: #1d2d3d;
        line-height: 1.35;
    }
    .station-meta {
        margin-top: 4px;
        font-size: 13px;
        color: #56697d;
        line-height: 1.3;
    }
    .post-label {
        font-size: 15px;
        font-weight: 700;
        color: #a61e1e;
        white-space: nowrap;
        text-shadow:
            -1px -1px 0 #ffffff,
             1px -1px 0 #ffffff,
            -1px  1px 0 #ffffff,
             1px  1px 0 #ffffff,
             0    0   6px rgba(255, 255, 255, 0.9);
    }
    .hub-label {
        font-size: 16px;
        font-weight: 700;
        color: #17324b;
        white-space: nowrap;
        text-shadow:
            -1px -1px 0 #ffffff,
             1px -1px 0 #ffffff,
            -1px  1px 0 #ffffff,
             1px  1px 0 #ffffff,
             0    0   6px rgba(255, 255, 255, 0.95);
    }
    </style>
    """))

    return m
