import pandas as pd
import sqlite3
import os
import json

# ===== базовая папка проекта =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "raw")
HUB_PARAMETER_FILES = [
    ("discharge", "discharge", "Discharge"),
    ("level_lower", "level_lower", "Absolute_Levels"),
    ("level_upper", "level_upper", "Absolute_Levels"),
]
POST_PARAMETER_FILES = [
    ("level", "level", "Absolute_Levels"),
]
VALIDATION_REPORT_PATH = os.path.join(BASE_DIR, "validation_issues.csv")


def register_issue(issues, station_id, parameter_name, issue_type, row_count, details):
    issues.append({
        "station": station_id,
        "parameter": parameter_name,
        "issue_type": issue_type,
        "row_count": row_count,
        "details": details,
    })


def validate_frame(frame, station_id, parameter_name):
    issues = []
    frame = frame.copy()

    frame["date"] = pd.to_datetime(frame["date"], dayfirst=True, errors="coerce")
    invalid_dates = frame["date"].isna().sum()
    if invalid_dates:
        register_issue(issues, station_id, parameter_name, "invalid_date", int(invalid_dates), "Строки удалены")
        frame = frame.dropna(subset=["date"])

    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    invalid_values = frame["value"].isna().sum()
    if invalid_values:
        register_issue(issues, station_id, parameter_name, "invalid_value", int(invalid_values), "Строки удалены")
        frame = frame.dropna(subset=["value"])

    duplicate_count = int(frame.duplicated(subset=["date"]).sum())
    if duplicate_count:
        register_issue(issues, station_id, parameter_name, "duplicate_date", duplicate_count, "Оставлена последняя запись")
        frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")

    if parameter_name == "discharge":
        negative_count = int((frame["value"] < 0).sum())
        if negative_count:
            register_issue(issues, station_id, parameter_name, "negative_discharge", negative_count, "Строки удалены")
            frame = frame[frame["value"] >= 0]

    frame = frame.sort_values("date").reset_index(drop=True)

    if len(frame) > 2:
        diffs = frame["value"].diff().abs()
        typical_diff = diffs.median(skipna=True)
        if pd.notna(typical_diff):
            min_threshold = 1500.0 if parameter_name == "discharge" else 1.5
            jump_threshold = max(min_threshold, typical_diff * 8)
            suspicious = diffs > jump_threshold
            suspicious_count = int(suspicious.sum())
            if suspicious_count:
                register_issue(
                    issues,
                    station_id,
                    parameter_name,
                    "suspicious_jump",
                    suspicious_count,
                    f"Порог скачка {jump_threshold:.2f}; строки не удалялись",
                )

    return frame, issues


def get_station_type(station_id):
    stations_path = os.path.join(BASE_DIR, "stations.csv")
    stations_df = pd.read_csv(stations_path)
    match = stations_df[stations_df["db_station"] == station_id]
    if match.empty:
        return "hub"
    return str(match.iloc[0].get("object_type", "hub") or "hub")


def load_station_observations(station_id):
    station_dir = os.path.join(RAW_DIR, station_id)
    if not os.path.isdir(station_dir):
        return None

    print(f"\nСтанция: {station_id}")
    frames = []
    issues = []
    parameter_files = POST_PARAMETER_FILES if get_station_type(station_id) == "post" else HUB_PARAMETER_FILES

    for file_prefix, parameter_name, value_column in parameter_files:
        csv_files = [
            name for name in os.listdir(station_dir)
            if name.endswith(".csv") and (name == f"{file_prefix}.csv" or name.startswith(f"{file_prefix}_"))
        ]
        if not csv_files:
            print(f"  - нет файла для параметра {parameter_name}")
            continue

        csv_path = os.path.join(station_dir, sorted(csv_files)[0])
        print(f"  - читаем: {csv_path}")

        frame = pd.read_csv(csv_path)
        frame = frame.rename(columns={"Dates": "date", value_column: "value"})
        frame["parameter"] = parameter_name
        frame["station"] = station_id
        frame = frame[["date", "value", "parameter", "station"]]
        frame, frame_issues = validate_frame(frame, station_id, parameter_name)
        issues.extend(frame_issues)
        frames.append(frame)

    if not frames:
        return None

    return pd.concat(frames, ignore_index=True), issues


def load_all_observations():
    station_ids = sorted(
        name for name in os.listdir(RAW_DIR)
        if os.path.isdir(os.path.join(RAW_DIR, name)) and not name.startswith("__")
    )

    all_frames = []
    all_issues = []
    for station_id in station_ids:
        station_payload = load_station_observations(station_id)
        if station_payload is not None:
            station_df, station_issues = station_payload
            all_frames.append(station_df)
            all_issues.extend(station_issues)

    if not all_frames:
        raise RuntimeError("Не найдено ни одной папки станции с CSV-файлами")

    observations = pd.concat(all_frames, ignore_index=True)
    observations = observations.sort_values(["station", "date", "parameter"]).reset_index(drop=True)
    return observations, pd.DataFrame(all_issues)


df, validation_issues = load_all_observations()

print("\nПервые строки:")
print(df.head())


def load_rivers_reference():
    rivers_path = os.path.join(BASE_DIR, "network_edges.geojson")
    with open(rivers_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    rows = []
    for feature in geojson["features"]:
        props = feature.get("properties", {})
        rows.append({
            "river_id": props.get("id"),
            "river": props.get("river"),
            "parent": props.get("parent"),
            "flows_to": props.get("flows_to"),
            "kind": props.get("kind"),
        })

    rivers_df = pd.DataFrame(rows)
    rivers_df = rivers_df.drop_duplicates(subset=["river_id"]).reset_index(drop=True)
    return rivers_df


def load_stations_reference():
    stations_path = os.path.join(BASE_DIR, "stations.csv")
    stations_df = pd.read_csv(stations_path)
    stations_df = stations_df.rename(columns={"name": "display_name"})
    stations_df["station_id"] = stations_df["db_station"]
    for column, default in (
        ("object_type", "hub"),
        ("river_section", ""),
        ("sort_order", 0),
    ):
        if column not in stations_df.columns:
            stations_df[column] = default
    stations_df = stations_df[
        [
            "station_id",
            "display_name",
            "river",
            "lat",
            "lon",
            "db_station",
            "object_type",
            "river_section",
            "sort_order",
        ]
    ]
    return stations_df


# ===== запись в базу =====
db_path = os.path.join(BASE_DIR, "hydro.db")

conn = sqlite3.connect(db_path)

df.to_sql("observations", conn, if_exists="replace", index=False)
load_rivers_reference().to_sql("rivers", conn, if_exists="replace", index=False)
load_stations_reference().to_sql("stations", conn, if_exists="replace", index=False)

conn.close()

if validation_issues.empty:
    if os.path.exists(VALIDATION_REPORT_PATH):
        os.remove(VALIDATION_REPORT_PATH)
    print("\nПроверка данных: явных проблем не найдено")
else:
    validation_issues.to_csv(VALIDATION_REPORT_PATH, index=False)
    print("\nПроверка данных: найдено замечаний", len(validation_issues))
    print(validation_issues.to_string(index=False))
    print(f"\nОтчёт сохранён: {VALIDATION_REPORT_PATH}")

print(f"\nБаза создана: {db_path}")
