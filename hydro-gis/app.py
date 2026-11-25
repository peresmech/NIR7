import hashlib
import hmac
import io
import logging
import math
import os
import csv
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
import pandas as pd
from flask import (
    Flask,
    has_request_context,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, MonthLocator, YearLocator


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    PROJECT_DIR = Path(sys._MEIPASS)
else:
    APP_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = APP_DIR

DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESOURCES_DIR = (PROJECT_DIR / "resources") if getattr(sys, "frozen", False) else (APP_DIR / "resources")
RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "hydro.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
ICON_PATH = RESOURCES_DIR / "hydrogis.ico"

SECRET_FILE = DATA_DIR / ".secret"
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "templates"),
    static_folder=str(PROJECT_DIR / "static"),
)
app.secret_key = SECRET_FILE.read_text(encoding="utf-8")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def migrate_observations(con):
    schema = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='observations'"
    ).fetchone()
    columns = {row["name"] for row in con.execute("PRAGMA table_info(observations)")}
    if schema and "series_type" in columns and "REFERENCES stations" in schema["sql"]:
        return

    for row in con.execute("SELECT DISTINCT parameter FROM observations"):
        con.execute(
            "INSERT OR IGNORE INTO parameters(code,label,unit) VALUES(?,?,?)",
            (row["parameter"], row["parameter"], ""),
        )

    unknown_station = con.execute(
        """
        SELECT DISTINCT o.station FROM observations o
        LEFT JOIN stations s ON s.station_id=o.station
        WHERE s.station_id IS NULL LIMIT 1
        """
    ).fetchone()
    if unknown_station:
        raise RuntimeError(
            f"В наблюдениях найден неизвестный объект: {unknown_station['station']}"
        )

    con.execute("DROP INDEX IF EXISTS observations_unique")
    con.execute("DROP TABLE IF EXISTS observations_new")
    con.execute(
        """
        CREATE TABLE observations_new (
            station TEXT NOT NULL REFERENCES stations(station_id),
            parameter TEXT NOT NULL REFERENCES parameters(code),
            date TEXT NOT NULL,
            value REAL NOT NULL,
            quality_flag TEXT NOT NULL DEFAULT 'unreviewed'
                CHECK(quality_flag IN ('unreviewed','checked','suspect')),
            quality_note TEXT,
            series_type TEXT NOT NULL DEFAULT 'observed'
                CHECK(series_type IN ('observed','forecast')),
            PRIMARY KEY(station,parameter,date,series_type)
        )
        """
    )
    series_expression = (
        "COALESCE(series_type,'observed')" if "series_type" in columns else "'observed'"
    )
    con.execute(
        f"""
        INSERT INTO observations_new(
            station,parameter,date,value,quality_flag,quality_note,series_type
        )
        SELECT o.station,o.parameter,substr(o.date,1,10),o.value,
               CASE WHEN o.quality_flag IN ('unreviewed','checked','suspect')
                    THEN o.quality_flag ELSE 'unreviewed' END,
               o.quality_note,{series_expression}
        FROM observations o
        JOIN stations s ON s.station_id=o.station
        JOIN parameters p ON p.code=o.parameter
        WHERE o.value IS NOT NULL AND o.date IS NOT NULL
        GROUP BY o.station,o.parameter,substr(o.date,1,10),{series_expression}
        """
    )
    con.execute("DROP TABLE observations")
    con.execute("ALTER TABLE observations_new RENAME TO observations")
    con.execute(
        "CREATE INDEX observations_parameter_date ON observations(parameter,date)"
    )


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return "pbkdf2_sha256$200000$" + salt.hex() + "$" + digest.hex()


def password_verify(password, encoded):
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def init_database():
    seed = RESOURCES_DIR / "hydro.db"
    if not seed.exists():
        seed = PROJECT_DIR.parent / "first" / "data" / "hydro.db"

    if not DB_PATH.exists():
        if seed.exists():
            shutil.copy2(seed, DB_PATH)
        else:
            raise RuntimeError(
                "Не найдена исходная база first/data/hydro.db. "
                "Поместите файл рядом с программой."
            )

    con = connect_db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('guest','admin'))
        );
        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY,
            station_id TEXT,
            parameter TEXT,
            filename TEXT,
            imported_at TEXT,
            rows_added INTEGER,
            rows_replaced INTEGER
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parameters (
            code TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            unit TEXT NOT NULL
        );
        """
    )
    station_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(stations)").fetchall()
    }
    for column, definition in (
        ("db_station", "TEXT"),
        ("object_type", "TEXT"),
        ("river_section", "TEXT"),
        ("region", "TEXT"),
        ("source_stok", "TEXT"),
        ("downstream_station_id", "TEXT"),
        ("sort_order", "INTEGER"),
        ("source_name", "TEXT"),
        ("source_type", "TEXT"),
        ("source_note", "TEXT"),
        ("source_istok", "TEXT"),
        ("tp", "TEXT"),
    ):
        if column not in station_columns:
            con.execute(f"ALTER TABLE stations ADD COLUMN {column} {definition}")
    observation_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(observations)").fetchall()
    }
    for column, definition in (
        ("quality_flag", "TEXT NOT NULL DEFAULT 'unreviewed'"),
        ("quality_note", "TEXT"),
    ):
        if column not in observation_columns:
            con.execute(f"ALTER TABLE observations ADD COLUMN {column} {definition}")
    con.executemany(
        "INSERT OR IGNORE INTO parameters(code,label,unit) VALUES(?,?,?)",
        [
            ("discharge", "Расход воды", "м³/с"),
            ("level_upper", "Уровень верхнего бьефа", "м"),
            ("level_lower", "Уровень нижнего бьефа", "м"),
            ("level", "Уровень воды на гидропосту", "м"),
        ],
    )
    stations_csv = RESOURCES_DIR / "stations.csv"
    if not stations_csv.exists():
        stations_csv = PROJECT_DIR.parent / "first" / "data" / "stations.csv"
    if not stations_csv.exists() and getattr(sys, "frozen", False):
        stations_csv = PROJECT_DIR / "stations.csv"
    if stations_csv.exists():
        station_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(stations)").fetchall()
        }
        for column, definition in (
            ("db_station", "TEXT"),
            ("object_type", "TEXT"),
            ("river_section", "TEXT"),
            ("region", "TEXT"),
            ("source_stok", "TEXT"),
            ("downstream_station_id", "TEXT"),
            ("sort_order", "INTEGER"),
            ("source_name", "TEXT"),
            ("source_type", "TEXT"),
            ("source_note", "TEXT"),
            ("source_istok", "TEXT"),
            ("tp", "TEXT"),
        ):
            if column not in station_columns:
                con.execute(f"ALTER TABLE stations ADD COLUMN {column} {definition}")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS stations_station_id_unique "
            "ON stations(station_id)"
        )
        stations = pd.read_csv(stations_csv)
        for row in stations.itertuples(index=False):
            station_id = getattr(row, "db_station")
            display_name = getattr(row, "name")
            con.execute(
                """
                INSERT INTO stations(
                    station_id,display_name,river,lat,lon,db_station,
                    object_type,river_section,region,sort_order
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(station_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    river=excluded.river,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    db_station=excluded.db_station,
                    object_type=excluded.object_type,
                    river_section=excluded.river_section,
                    region=excluded.region,
                    sort_order=excluded.sort_order
                """,
                (
                    station_id,
                    display_name,
                    getattr(row, "river"),
                    float(getattr(row, "lat")),
                    float(getattr(row, "lon")),
                    station_id,
                    getattr(row, "object_type"),
                    getattr(row, "river_section"),
                    getattr(row, "region", ""),
                    int(getattr(row, "sort_order")),
                ),
            )

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS stations_station_id_unique "
        "ON stations(station_id)"
    )
    con.executemany(
        "INSERT OR IGNORE INTO parameters(code,label,unit) VALUES(?,?,?)",
        [
            ("level_relative", "Относительный уровень воды", "см"),
            ("level_upper_relative", "Относительный уровень верхнего бьефа", "см"),
            ("level_lower_relative", "Относительный уровень нижнего бьефа", "см"),
        ],
    )
    migrate_observations(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY,
            imported_at TEXT NOT NULL,
            username TEXT NOT NULL REFERENCES users(username)
        );
        CREATE TABLE IF NOT EXISTS import_files (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL REFERENCES import_batches(id),
            filename TEXT NOT NULL,
            station_id TEXT REFERENCES stations(station_id),
            status TEXT NOT NULL CHECK(status IN ('imported','failed')),
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS import_series (
            file_id INTEGER NOT NULL REFERENCES import_files(id) ON DELETE CASCADE,
            parameter TEXT NOT NULL REFERENCES parameters(code),
            series_type TEXT NOT NULL CHECK(series_type IN ('observed','forecast')),
            rows_added INTEGER NOT NULL DEFAULT 0,
            rows_replaced INTEGER NOT NULL DEFAULT 0,
            rows_skipped INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(file_id,parameter,series_type)
        );
        """
    )

    admin = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if admin is None:
        con.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            ("admin", password_hash("admin"), "admin"),
        )

    guest = con.execute("SELECT id FROM users WHERE username='guest'").fetchone()
    if guest is None:
        con.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            ("guest", password_hash(secrets.token_hex(24)), "guest"),
        )
    con.commit()
    con.close()


def audit(action, details):
    con = connect_db()
    con.execute(
        "INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)",
        (
            (current_user() if has_request_context() else None) or "system",
            action,
            details,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    con.commit()
    con.close()


def current_user():
    return session.get("username")


def is_admin():
    return session.get("role") == "admin"


def login_required(view):
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        con = connect_db()
        user = con.execute(
            "SELECT username,password_hash,role FROM users WHERE username=?", (username,)
        ).fetchone()
        con.close()
        if user and password_verify(password, user["password_hash"]):
            session.clear()
            session["username"] = user["username"]
            session["role"] = user["role"]
            audit("Вход", f"Успешный вход пользователя {user['username']}")
            return redirect(url_for("index"))
        error = "Неверное имя пользователя или пароль"
    return render_template("login.html", error=error)


@app.route("/guest")
def guest_login():
    session.clear()
    session["username"] = "guest"
    session["role"] = "guest"
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    con = connect_db()
    stations = con.execute(
        """
        SELECT station_id, display_name, river, lat, lon,
               COALESCE(object_type,'hub') AS object_type,
               COALESCE(river_section,'') AS river_section,
               COALESCE(region,'') AS region,
               COALESCE(source_istok,'') AS source_istok,
               COALESCE(source_stok,'') AS source_stok,
               COALESCE(downstream_station_id,'') AS downstream_station_id,
               COALESCE(source_name,'') AS source_name,
               COALESCE(source_type,'') AS source_type,
               COALESCE(source_note,'') AS source_note,
               COALESCE(tp,'') AS tp,
               COALESCE(sort_order,0) AS sort_order
        FROM stations
        ORDER BY region, display_name
        """
    ).fetchall()
    date_bounds = con.execute(
        "SELECT MIN(substr(date,1,10)) AS first_date, "
        "MAX(substr(date,1,10)) AS last_date FROM observations"
    ).fetchone()
    con.close()
    first_date = date_bounds["first_date"] or "2010-01-01"
    last_date = date_bounds["last_date"] or first_date
    range_days = max(
        (datetime.fromisoformat(last_date) - datetime.fromisoformat(first_date)).days,
        0,
    )
    station_groups = {}
    for station in stations:
        group_name = station["region"] or "Область не указана"
        station_groups.setdefault(group_name, []).append(station)
    return render_template(
        "index.html",
        stations=stations,
        all_stations=stations,
        station_groups=station_groups,
        username=current_user(),
        is_admin=is_admin(),
        date_min=first_date,
        date_max=last_date,
        range_days=range_days,
    )


@app.route("/map")
@login_required
def map_page():
    station_id = request.args.get("station")
    pick_mode = request.args.get("pick") == "1"
    con = connect_db()
    rows = con.execute(
        """
        SELECT station_id, display_name, river, lat, lon,
               COALESCE(object_type,'hub') AS object_type,
               COALESCE(source_istok,'') AS source_istok,
               COALESCE(tp,'') AS tp
        FROM stations
        """
    ).fetchall()
    con.close()

    visible_rows = [
        {
            "station_id": row["station_id"],
            "display_name": row["display_name"],
            "river": row["river"] or "",
            "lat": row["lat"],
            "lon": row["lon"],
            "object_type": row["object_type"],
            "source_istok": row["source_istok"] or "",
            "tp": row["tp"] or "",
        }
        for row in rows
        if row["lat"] is not None and row["lon"] is not None
    ]
    if visible_rows:
        center = [
            sum(row["lat"] for row in visible_rows) / len(visible_rows),
            sum(row["lon"] for row in visible_rows) / len(visible_rows),
        ]
    else:
        center = [55.7, 47.5]
    return render_template(
        "map.html",
        stations=visible_rows,
        selected_station=station_id,
        center=center,
        pick_mode=pick_mode,
        can_edit=is_admin(),
    )


@app.route("/plot")
@login_required
def plot():
    station_id = request.args.get("station", "")
    parameter = request.args.get("parameter", "discharge")
    series_type = request.args.get("series_type", "observed")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    if series_type not in ("observed", "forecast"):
        return "Неизвестный тип ряда", 400

    con = connect_db()
    station = con.execute(
        "SELECT display_name FROM stations WHERE station_id=?", (station_id,)
    ).fetchone()
    parameter_meta = con.execute(
        "SELECT label,unit FROM parameters WHERE code=?", (parameter,)
    ).fetchone()
    query = (
        "SELECT date,value,quality_flag FROM observations "
        "WHERE station=? AND parameter=? AND series_type=? "
    )
    params = [station_id, parameter, series_type]
    if date_from:
        query += "AND date>=? "
        params.append(date_from)
    if date_to:
        query += "AND date<=? "
        params.append(date_to + " 23:59:59")
    query += "ORDER BY date"
    df = pd.read_sql_query(query, con, params=params)
    con.close()

    label = parameter_meta["label"] if parameter_meta else parameter
    unit = parameter_meta["unit"] if parameter_meta else ""
    compact = request.args.get("mini") == "1"
    fig, ax = plt.subplots(
        figsize=(2.7, 1.25) if compact else (8.8, 3.8),
        dpi=90 if compact else 130,
    )
    fig.patch.set_facecolor("#f5f7fa")
    ax.set_facecolor("white")
    ax.grid(True, color="#dde5ef", linewidth=0.8)

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        ax.plot(
            df["date"],
            df["value"],
            color="#1769aa",
            linewidth=1.25 if compact else 1.5,
        )
        suspect = df[df["quality_flag"] == "suspect"]
        if not suspect.empty:
            ax.scatter(
                pd.to_datetime(suspect["date"]),
                suspect["value"],
                color="#c23b32",
                s=10 if compact else 24,
                label="Подозрительные наблюдения",
                zorder=3,
            )
        span = max((df["date"].max() - df["date"].min()).days, 1)
        if span <= 120:
            ax.xaxis.set_major_formatter(DateFormatter("%d.%m"))
        elif span <= 365 * 3:
            ax.xaxis.set_major_locator(MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(DateFormatter("%m.%Y"))
        else:
            ax.xaxis.set_major_locator(YearLocator())
            ax.xaxis.set_major_formatter(DateFormatter("%Y"))
        ax.tick_params(axis="x", rotation=30, labelsize=7 if compact else 10)
    else:
        ax.text(0.5, 0.5, "Нет данных за выбранный период", ha="center", va="center")

    title = station["display_name"] if station else station_id
    ax.set_title(f"{title}: {label}", fontsize=8 if compact else 12)
    if not compact:
        ax.set_ylabel(f"{label} ({unit})" if unit else label)
        ax.set_xlabel("Дата")
    else:
        ax.tick_params(axis="y", labelsize=7)
        ax.set_xlabel("")
        ax.set_ylabel("")
    fig.tight_layout()
    output = io.BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    output.seek(0)
    filename = f"{station_id or 'hydro'}_{parameter}.png"
    if request.args.get("download") == "1":
        return send_file(
            output,
            mimetype="image/png",
            as_attachment=True,
            download_name=filename,
        )
    return send_file(output, mimetype="image/png")


def observation_filters(stations, parameter, date_from, date_to, series_type="observed"):
    if not stations:
        raise ValueError("Выберите хотя бы один объект")
    if len(stations) > 30:
        raise ValueError("Можно выбрать не более 30 объектов за один раз")
    con = connect_db()
    valid_ids = {
        row["station_id"]
        for row in con.execute(
            f"SELECT station_id FROM stations WHERE station_id IN ({','.join('?' for _ in stations)})",
            stations,
        )
    }
    if valid_ids != set(stations):
        con.close()
        raise ValueError("Список объектов содержит неизвестный идентификатор")
    if not con.execute("SELECT 1 FROM parameters WHERE code=?", (parameter,)).fetchone():
        con.close()
        raise ValueError("Неизвестный гидрологический параметр")
    if series_type not in ("observed", "forecast"):
        con.close()
        raise ValueError("Неизвестный тип ряда")
    return con


@app.route("/summary")
@login_required
def summary():
    station_id = request.args.get("station", "")
    parameter = request.args.get("parameter", "discharge")
    series_type = request.args.get("series_type", "observed")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    if series_type not in ("observed", "forecast"):
        return "Неизвестный тип ряда", 400
    con = connect_db()
    station = con.execute(
        "SELECT display_name,source_istok,tp,source_name,source_type,source_note "
        "FROM stations WHERE station_id=?",
        (station_id,),
    ).fetchone()
    meta = con.execute(
        "SELECT label,unit FROM parameters WHERE code=?", (parameter,)
    ).fetchone()
    if station is None or meta is None:
        con.close()
        return "Неизвестный объект или параметр", 404
    sql = (
        "SELECT date,value,quality_flag,quality_note FROM observations "
        "WHERE station=? AND parameter=? AND series_type=?"
    )
    args = [station_id, parameter, series_type]
    if date_from:
        sql += " AND date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND date<=?"
        args.append(date_to + " 23:59:59")
    frame = pd.read_sql_query(sql, con, params=args)
    con.close()

    stats = None
    annual_rows = []
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
        extreme_min = frame.loc[frame["value"].idxmin()]
        extreme_max = frame.loc[frame["value"].idxmax()]
        ordered = frame.sort_values("date").copy()
        elapsed_days = ordered["date"].diff().dt.total_seconds() / 86400
        rates = ordered["value"].diff() / elapsed_days
        max_rise = rates.max()
        max_fall = rates.min()
        yearly = frame.assign(year=frame["date"].dt.year).groupby("year")["value"].agg(
            ["mean", "min", "max", "count"]
        )
        annual_rows = [
            {
                "year": int(year),
                "mean": float(row["mean"]),
                "min": float(row["min"]),
                "max": float(row["max"]),
                "count": int(row["count"]),
            }
            for year, row in yearly.iterrows()
        ]
        stats = {
            "count": int(frame["value"].count()),
            "mean": float(frame["value"].mean()),
            "median": float(frame["value"].median()),
            "min": float(extreme_min["value"]),
            "min_date": extreme_min["date"].strftime("%Y-%m-%d"),
            "max": float(extreme_max["value"]),
            "max_date": extreme_max["date"].strftime("%Y-%m-%d"),
            "amplitude": float(frame["value"].max() - frame["value"].min()),
            "std": float(frame["value"].std(ddof=0)),
            "max_rise_per_day": float(max_rise) if pd.notna(max_rise) else None,
            "max_fall_per_day": float(max_fall) if pd.notna(max_fall) else None,
            "unreviewed": int((frame["quality_flag"] == "unreviewed").sum()),
            "suspect": int((frame["quality_flag"] == "suspect").sum()),
        }
    return render_template(
        "summary.html",
        station=station,
        meta=meta,
        series_type=series_type,
        stats=stats,
        annual_rows=annual_rows,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/export/table")
@login_required
def export_table():
    station_ids = request.args.getlist("station")
    if not station_ids and request.args.get("stations"):
        station_ids = request.args.get("stations", "").split(",")
    station_ids = list(dict.fromkeys(item for item in station_ids if item))
    parameter = request.args.get("parameter", "discharge")
    series_type = request.args.get("series_type", "observed")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    try:
        con = observation_filters(station_ids, parameter, date_from, date_to, series_type)
    except ValueError as exc:
        return str(exc), 400
    query = (
        "SELECT s.display_name,s.river,s.source_istok,s.tp,p.label AS parameter_label,"
        "p.unit,o.date,o.value,o.series_type,o.quality_flag,o.quality_note "
        "FROM observations o JOIN stations s ON s.station_id=o.station "
        "JOIN parameters p ON p.code=o.parameter "
        f"WHERE o.station IN ({','.join('?' for _ in station_ids)}) "
        "AND o.parameter=? AND o.series_type=?"
    )
    args = [*station_ids, parameter, series_type]
    if date_from:
        query += " AND o.date>=?"
        args.append(date_from)
    if date_to:
        query += " AND o.date<=?"
        args.append(date_to + " 23:59:59")
    query += " ORDER BY s.display_name,o.date"
    frame = pd.read_sql_query(query, con, params=args)
    con.close()
    output = io.StringIO()
    frame.to_csv(output, index=False)
    data = io.BytesIO(("\ufeff" + output.getvalue()).encode("utf-8"))
    suffix = station_ids[0] if len(station_ids) == 1 else "stations"
    return send_file(
        data,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"hydro_{suffix}_{parameter}_{series_type}.csv",
    )


@app.route("/compare")
@login_required
def compare():
    station_ids = request.args.get("stations", "").split(",")
    station_ids = list(dict.fromkeys(item for item in station_ids if item))
    if len(station_ids) < 2:
        return "Для сравнения выберите не менее двух объектов", 400
    parameter = request.args.get("parameter", "discharge")
    series_type = request.args.get("series_type", "observed")
    mode = request.args.get("mode", "timeseries")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    try:
        con = observation_filters(station_ids, parameter, date_from, date_to, series_type)
    except ValueError as exc:
        return str(exc), 400
    sql = (
        "SELECT o.station,s.display_name,o.date,o.value "
        "FROM observations o JOIN stations s ON s.station_id=o.station "
        f"WHERE o.station IN ({','.join('?' for _ in station_ids)}) "
        "AND o.parameter=? AND o.series_type=?"
    )
    args = [*station_ids, parameter, series_type]
    if date_from:
        sql += " AND o.date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND o.date<=?"
        args.append(date_to + " 23:59:59")
    sql += " ORDER BY o.date"
    frame = pd.read_sql_query(sql, con, params=args)
    meta = con.execute(
        "SELECT label,unit FROM parameters WHERE code=?", (parameter,)
    ).fetchone()
    station_names = {
        row["station_id"]: row["display_name"]
        for row in con.execute(
            f"SELECT station_id,display_name FROM stations "
            f"WHERE station_id IN ({','.join('?' for _ in station_ids)})",
            station_ids,
        )
    }
    con.close()
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
    fig, ax = plt.subplots(figsize=(8.8, 4), dpi=100)
    if frame.empty or frame["station"].nunique() < 2:
        present = set(frame["station"]) if not frame.empty else set()
        missing = [station_names[item] for item in station_ids if item not in present]
        message = "Нет данных для выбранного периода"
        if missing:
            message = "Нет данных у: " + ", ".join(missing)
        ax.text(0.5, 0.5, message, ha="center", va="center")
    elif mode == "annual":
        frame["year"] = frame["date"].dt.year
        annual = frame.groupby(["station", "display_name", "year"], as_index=False)["value"].mean()
        for _, group in annual.groupby("station"):
            name = group["display_name"].iloc[0]
            ax.plot(group["year"], group["value"], marker="o", linewidth=1.8, label=name)
        ax.set_xlabel("Год")
        title = "прогнозных рядов" if series_type == "forecast" else "наблюдений"
        ax.set_title(f"Межгодовая динамика {title}: {meta['label']}")
    else:
        for _, group in frame.groupby("station"):
            name = group["display_name"].iloc[0]
            ax.plot(group["date"], group["value"], linewidth=1.25, label=name)
        ax.xaxis.set_major_formatter(DateFormatter("%Y"))
        ax.tick_params(axis="x", rotation=30)
        ax.set_xlabel("Дата")
        title = "прогнозных рядов" if series_type == "forecast" else "наблюдений"
        ax.set_title(f"Сравнение {title}: {meta['label']}")
    ax.set_ylabel(f"{meta['label']} ({meta['unit']})")
    ax.grid(True, color="#dde5ef")
    if not frame.empty and frame["station"].nunique() > 1:
        ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    output = io.BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    output.seek(0)
    filename = "hydro_annual_comparison.png" if mode == "annual" else "hydro_comparison.png"
    if series_type == "forecast":
        filename = filename.replace(".png", "_forecast.png")
    if request.args.get("download") == "1":
        return send_file(output, mimetype="image/png", as_attachment=True, download_name=filename)
    return send_file(output, mimetype="image/png")


@app.route("/admin/backup", methods=["POST"])
@login_required
def create_backup():
    if not is_admin():
        return "Доступ запрещён", 403
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"hydro_{timestamp}.db"
    source = connect_db()
    destination = sqlite3.connect(backup_path)
    source.backup(destination)
    destination.close()
    source.close()
    audit("Резервная копия", backup_path.name)
    return redirect(url_for("admin"))


@app.route("/admin/backup/<filename>")
@login_required
def download_backup(filename):
    if not is_admin():
        return "Доступ запрещён", 403
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".db"):
        return "Некорректное имя файла", 400
    backup_path = BACKUP_DIR / safe_name
    if not backup_path.is_file():
        return "Резервная копия не найдена", 404
    return send_file(backup_path, as_attachment=True, download_name=safe_name)


@app.route("/admin/quality", methods=["POST"])
@login_required
def update_quality():
    if not is_admin():
        return "Доступ запрещён", 403
    station_id = request.form.get("station_id", "")
    parameter = request.form.get("parameter", "")
    date_from = request.form.get("date_from", "")
    date_to = request.form.get("date_to", "")
    quality_flag = request.form.get("quality_flag", "")
    note = request.form.get("quality_note", "").strip()
    if quality_flag not in ("unreviewed", "checked", "suspect"):
        return "Некорректная пометка качества", 400
    if not station_id or not parameter or not date_from or not date_to:
        return "Заполните объект, параметр и диапазон дат", 400
    con = connect_db()
    cursor = con.execute(
        """
        UPDATE observations
        SET quality_flag=?,quality_note=?
        WHERE station=? AND parameter=? AND series_type='observed'
          AND date>=? AND date<=?
        """,
        (quality_flag, note or None, station_id, parameter, date_from, date_to + " 23:59:59"),
    )
    updated = cursor.rowcount
    con.commit()
    con.close()
    audit(
        "Оценка качества",
        f"{station_id}, {parameter}, {date_from}—{date_to}: "
        f"{updated} записей → {quality_flag}",
    )
    return redirect(url_for("admin"))


@app.route("/admin/audit")
@login_required
def audit_page():
    if not is_admin():
        return "Доступ запрещён", 403
    con = connect_db()
    entries = con.execute(
        "SELECT username,action,details,created_at FROM audit_log ORDER BY id DESC LIMIT 500"
    ).fetchall()
    con.close()
    return render_template("audit.html", entries=entries)


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if not is_admin():
        return "Доступ запрещён", 403

    message = None
    error = None
    if request.method == "POST":
        files = [
            file for file in request.files.getlist("csv_files")
            if file and file.filename
        ]
        if not files:
            error = "Выберите один или несколько CSV-файлов"
        elif len(files) > 100:
            error = "За один раз можно загрузить не более 100 файлов"
        else:
            shared_name = request.form.get("shared_station_name", "").strip()
            shared_region = request.form.get("shared_region", "").strip() or None
            shared_object_type = request.form.get("shared_object_type", "").strip()
            latitude_text = request.form.get("shared_lat", "").strip()
            longitude_text = request.form.get("shared_lon", "").strip()
            if bool(latitude_text) != bool(longitude_text):
                error = "Для координат укажите и широту, и долготу."
            elif shared_object_type not in ("", "hub", "post"):
                error = "Неизвестный тип объекта."

            coordinates = None
            if not error and latitude_text and longitude_text:
                try:
                    coordinates = (float(latitude_text), float(longitude_text))
                    if not (
                        -90 <= coordinates[0] <= 90
                        and -180 <= coordinates[1] <= 180
                    ):
                        raise ValueError
                except ValueError:
                    error = "Укажите корректные координаты: широта от -90 до 90, долгота от -180 до 180."

            if not error:
                first_name = Path(files[0].filename.replace("\\", "/")).name
                first_station_name, first_object_type = station_from_filename(first_name)
                if coordinates and not shared_name:
                    shared_name = first_station_name
                if shared_name and not shared_object_type:
                    shared_object_type = first_object_type
                con = connect_db()
                cursor = con.execute(
                    "INSERT INTO import_batches(imported_at,username) VALUES(?,?)",
                    (datetime.now().isoformat(timespec="seconds"), current_user()),
                )
                batch_id = cursor.lastrowid
                con.commit()
                con.close()

                imported = 0
                failed = 0
                for file in files:
                    filename = Path(file.filename.replace("\\", "/")).name
                    path = UPLOAD_DIR / f"{secrets.token_hex(12)}.csv"
                    try:
                        file.save(path)
                        import_observations(
                            path,
                            filename,
                            batch_id,
                            station_name_override=shared_name or None,
                            object_type_override=shared_object_type or None,
                            coordinates_override=coordinates,
                            region_override=shared_region,
                        )
                        imported += 1
                    except (OSError, UnicodeError, csv.Error, ValueError, sqlite3.Error) as exc:
                        record_import_failure(batch_id, filename, str(exc))
                        app.logger.warning("CSV import failed for %s: %s", filename, exc)
                        failed += 1
                message = f"Обработано файлов: {len(files)}. Загружено: {imported}. С ошибками: {failed}."

    con = connect_db()
    stations = con.execute(
        "SELECT station_id,display_name FROM stations ORDER BY display_name"
    ).fetchall()
    all_stations = con.execute(
        "SELECT station_id,display_name FROM stations ORDER BY display_name"
    ).fetchall()
    imports = con.execute(
        """
        SELECT f.id,b.imported_at,COALESCE(s.display_name,'—') AS station,
               f.station_id,
               f.filename,f.status,f.message,
               COALESCE(GROUP_CONCAT(p.label || ' · ' || t.series_type, ', '),'') AS series
        FROM import_files f
        JOIN import_batches b ON b.id=f.batch_id
        LEFT JOIN stations s ON s.station_id=f.station_id
        LEFT JOIN import_series t ON t.file_id=f.id
        LEFT JOIN parameters p ON p.code=t.parameter
        GROUP BY f.id
        ORDER BY f.id DESC LIMIT 50
        """
    ).fetchall()
    con.close()
    backups = sorted(BACKUP_DIR.glob("hydro_*.db"), reverse=True)
    return render_template(
        "admin.html",
        stations=stations,
        all_stations=all_stations,
        imports=imports,
        backups=[path.name for path in backups[:20]],
        message=message,
        error=error,
        username=current_user(),
    )


@app.route("/admin/station", methods=["POST"])
@login_required
def create_station():
    if not is_admin():
        return "Доступ запрещён", 403

    station_id = request.form.get("station_id", "").strip()
    display_name = request.form.get("display_name", "").strip()
    river = request.form.get("river", "").strip()
    region = request.form.get("region", "").strip()
    lat = request.form.get("lat", "").strip()
    lon = request.form.get("lon", "").strip()
    object_type = request.form.get("object_type", "hub").strip()
    river_section = request.form.get("river_section", "").strip()
    source_name = request.form.get("source_name", "").strip()
    source_type = request.form.get("source_type", "").strip()
    source_note = request.form.get("source_note", "").strip()
    source_istok = request.form.get("source_istok", "").strip()
    source_stok = request.form.get("source_stok", "").strip()
    downstream_station_id = request.form.get("downstream_station_id", "").strip()
    tp = request.form.get("tp", "").strip()

    try:
        lat_value = float(lat)
        lon_value = float(lon)
        if not (-90 <= lat_value <= 90 and -180 <= lon_value <= 180):
            raise ValueError
    except ValueError:
        return "Укажите корректные широту и долготу", 400

    if not station_id or not display_name:
        return "Укажите идентификатор и название объекта", 400
    if downstream_station_id == station_id:
        return "Объект не может быть собственным следующим объектом.", 400
    if object_type not in ("hub", "post"):
        return "Выберите корректный тип объекта.", 400

    con = connect_db()
    station_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(stations)").fetchall()
    }
    for column, definition in (
        ("db_station", "TEXT"),
        ("object_type", "TEXT"),
        ("river_section", "TEXT"),
        ("region", "TEXT"),
        ("source_stok", "TEXT"),
        ("downstream_station_id", "TEXT"),
        ("sort_order", "INTEGER"),
        ("source_name", "TEXT"),
        ("source_type", "TEXT"),
        ("source_note", "TEXT"),
        ("source_istok", "TEXT"),
        ("tp", "TEXT"),
    ):
        if column not in station_columns:
            con.execute(f"ALTER TABLE stations ADD COLUMN {column} {definition}")

    if downstream_station_id and not con.execute(
        "SELECT 1 FROM stations WHERE station_id=?", (downstream_station_id,)
    ).fetchone():
        con.close()
        return "Следующий объект не найден.", 400
    next_id = downstream_station_id
    visited = set()
    while next_id and next_id not in visited:
        if next_id == station_id:
            con.close()
            return "Связь создаст замкнутую цепочку объектов.", 400
        visited.add(next_id)
        next_station = con.execute(
            "SELECT downstream_station_id FROM stations WHERE station_id=?",
            (next_id,),
        ).fetchone()
        next_id = next_station["downstream_station_id"] if next_station else None
    previous = con.execute(
        "SELECT downstream_station_id,river,source_stok FROM stations WHERE station_id=?",
        (station_id,),
    ).fetchone()
    previous_downstream = previous["downstream_station_id"] if previous else None
    with con:
        con.execute(
            """
            INSERT INTO stations(
                station_id,display_name,river,lat,lon,db_station,
                object_type,river_section,region,sort_order,
                source_name,source_type,source_note,source_istok,source_stok,
                downstream_station_id,tp
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(station_id) DO UPDATE SET
                display_name=excluded.display_name,
                river=excluded.river,
                lat=excluded.lat,
                lon=excluded.lon,
                db_station=excluded.db_station,
                object_type=excluded.object_type,
                river_section=excluded.river_section,
                region=excluded.region,
                source_name=excluded.source_name,
                source_type=excluded.source_type,
                source_note=excluded.source_note,
                source_istok=excluded.source_istok,
                source_stok=excluded.source_stok,
                downstream_station_id=excluded.downstream_station_id,
                tp=excluded.tp
            """,
            (
                station_id, display_name, river, lat_value, lon_value, station_id,
                object_type, river_section, region, 0,
                source_name, source_type, source_note, source_istok, source_stok,
                downstream_station_id or None, tp,
            ),
        )
        if previous_downstream and previous_downstream != downstream_station_id:
            previous_source = previous["source_stok"] or previous["river"] or ""
            con.execute(
                """
                UPDATE stations SET source_istok=NULL
                WHERE station_id=? AND source_istok=?
                """,
                (previous_downstream, previous_source),
            )
        if downstream_station_id:
            next_source = source_stok or river
            con.execute(
                "UPDATE stations SET source_istok=? WHERE station_id=?",
                (next_source or None, downstream_station_id),
            )
    con.commit()
    con.close()
    audit("Справочник объектов", f"Создан/изменён объект {station_id}: {display_name}")
    return redirect("/" if request.form.get("return_to") == "/" else url_for("admin"))


@app.route("/admin/password", methods=["POST"])
@login_required
def change_password():
    if not is_admin():
        return "Доступ запрещён", 403
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    if len(password) < 8:
        return render_template(
            "admin.html",
            stations=[],
            imports=[],
            message=None,
            error="Пароль должен содержать не менее 8 символов",
            username=current_user(),
        ), 400
    if password != password_confirm:
        return render_template(
            "admin.html",
            stations=[],
            imports=[],
            message=None,
            error="Пароли не совпадают",
            username=current_user(),
        ), 400
    con = connect_db()
    con.execute(
        "UPDATE users SET password_hash=? WHERE username='admin'",
        (password_hash(password),),
    )
    con.commit()
    con.close()
    audit("Смена пароля", "Изменён пароль администратора")
    return redirect(url_for("admin"))


def normalize_words(value):
    text = str(value).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def station_key(value):
    name = normalize_words(value)
    name = re.sub(r"^(с|пгт|село|поселок)\s+", "", name)
    name = re.sub(r"\s+(гидроузел|гидропост|гэс|пост)$", "", name)
    return name


def station_from_filename(filename):
    stem = Path(filename).stem
    stem = re.sub(r"\s*\([^)]*\)\s*$", "", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"^\s*\d+\s+", "", stem)
    label = stem.casefold().replace("ё", "е")
    is_post = "пост" in label or "гидропост" in label
    is_hub = not is_post and (
        "гидроузел" in label or "гэс" in label or "бьеф" in label
    )
    patterns = (
        r"^ход\s+уровней\s+поста\s+",
        r"^уровень\s+воды\s+(?:верхнего|нижнего)\s+бьефа\s+в\s+срок\s+наблюдений\s+поста\s+",
        r"^сброс\s+воды\s+",
        r"^расход\s+",
    )
    for pattern in patterns:
        shortened = re.sub(pattern, "", stem, flags=re.IGNORECASE)
        if shortened != stem:
            stem = shortened
            break
    stem = stem.strip()
    stem = re.sub(r"\s+(гидроузел|гидропост|гэс|пост)$", "", stem, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", stem).strip(" _-.")
    if not name or station_key(name) in ("гэс", "нижний бьеф", "верхний бьеф"):
        detail = "Нижний бьеф" if "нижний бьеф" in label else "Объект не указан"
        subject = "Расход ГЭС" if "расход гэс" in label else detail
        name = f"{subject} (объект не указан)"
    return name, "hub" if is_hub else "post"


def find_or_create_station(
    con,
    filename,
    coordinates=None,
    region=None,
    name_override=None,
    object_type_override=None,
):
    name, object_type = station_from_filename(filename)
    if name_override:
        name = name_override
    if object_type_override:
        object_type = object_type_override
    key = station_key(name)
    stations = [
        station
        for station in con.execute(
            "SELECT station_id,display_name,object_type FROM stations"
        )
        if station_key(station["display_name"]) == key
    ]
    same_type = [
        station
        for station in stations
        if (station["object_type"] or "hub") == object_type
    ]
    match = same_type[0] if len(same_type) == 1 else (
        stations[0] if len(stations) == 1 else None
    )
    if match:
        latitude, longitude = coordinates or (None, None)
        con.execute(
            """
            UPDATE stations
            SET lat=COALESCE(?,lat), lon=COALESCE(?,lon),
                region=COALESCE(?,NULLIF(region,''))
            WHERE station_id=?
            """,
            (latitude, longitude, region, match["station_id"]),
        )
        return match["station_id"]

    base_id = "csv_" + re.sub(r"\s+", "_", key)
    station_id = base_id or "csv_imported_station"
    suffix = 2
    while con.execute(
        "SELECT 1 FROM stations WHERE station_id=?", (station_id,)
    ).fetchone():
        station_id = f"{base_id}_{suffix}"
        suffix += 1
    con.execute(
        """
        INSERT INTO stations(
            station_id,display_name,river,lat,lon,db_station,object_type,
            river_section,region,source_name,source_type,source_note
        )
        VALUES(?,?,?,?,?,?,?,'',?,'CSV','CSV',
               'Географические метаданные не указаны в загруженном файле')
        """,
        (
            station_id,
            name,
            None,
            coordinates[0] if coordinates else None,
            coordinates[1] if coordinates else None,
            station_id,
            object_type,
            region,
        ),
    )
    return station_id


def read_hydrology_csv(path):
    raw = Path(path).read_bytes()
    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Не удалось определить кодировку CSV")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("В CSV не найдена строка с названиями столбцов")
    reader.fieldnames = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
    return reader.fieldnames, list(reader)


def detect_series(fieldnames, filename):
    columns = {normalize_words(name): name for name in fieldnames}
    date_column = next(
        (columns[key] for key in ("dates", "date", "дата") if key in columns),
        None,
    )
    if date_column is None:
        raise ValueError("В CSV нет столбца даты (ожидается Dates или Дата)")

    title = normalize_words(filename)
    if "discharge" in columns:
        level_parameter = "discharge"
    elif "value" in columns and ("расход" in title or "discharge" in title):
        level_parameter = "discharge"
    elif "верхнего бьефа" in title:
        level_parameter = "level_upper"
    elif "нижнего бьефа" in title:
        level_parameter = "level_lower"
    else:
        level_parameter = "level"

    relative_parameter = {
        "level": "level_relative",
        "level_upper": "level_upper_relative",
        "level_lower": "level_lower_relative",
    }.get(level_parameter, "level_relative")
    definitions = []
    if "value" in columns and not any(
        key in columns for key in ("discharge", "absolute levels")
    ):
        definitions.append(("value", level_parameter, "observed"))
    definitions.extend(
        (
            ("discharge", "discharge", "observed"),
            ("absolute levels", level_parameter, "observed"),
            ("relative levels", relative_parameter, "observed"),
            ("forecast discharge", "discharge", "forecast"),
            ("forecast absolute levels", level_parameter, "forecast"),
            ("forecast levels", relative_parameter, "forecast"),
        )
    )
    series = [
        (columns[column], parameter, series_type)
        for column, parameter, series_type in definitions
        if column in columns
    ]
    if not series:
        raise ValueError("Не найдены столбцы гидрологических значений")
    return date_column, series


def metadata_columns(fieldnames):
    columns = {normalize_words(name): name for name in fieldnames}
    latitude = next(
        (columns[key] for key in ("lat", "latitude", "широта") if key in columns),
        None,
    )
    longitude = next(
        (columns[key] for key in ("lon", "lng", "longitude", "долгота") if key in columns),
        None,
    )
    region = next(
        (
            columns[key]
            for key in ("region", "oblast", "область", "субъект", "регион")
            if key in columns
        ),
        None,
    )
    if bool(latitude) != bool(longitude):
        raise ValueError("Координаты должны содержать оба столбца: широту и долготу")
    return latitude, longitude, region


def parse_date(value):
    value = (value or "").strip()
    for date_format in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def parse_value(value):
    try:
        number = float((value or "").strip().replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_quality(value):
    label = normalize_words(value or "")
    return {
        "checked": "checked",
        "проверено": "checked",
        "проверен": "checked",
        "норма": "checked",
        "suspect": "suspect",
        "подозрительно": "suspect",
        "подозрительное": "suspect",
        "сомнительно": "suspect",
        "unreviewed": "unreviewed",
        "не проверено": "unreviewed",
        "": "unreviewed",
    }.get(label, "unreviewed")


def record_import_failure(batch_id, filename, message):
    con = connect_db()
    con.execute(
        "INSERT INTO import_files(batch_id,filename,status,message) VALUES(?,?,?,?)",
        (batch_id, filename, "failed", message[:500]),
    )
    con.commit()
    con.close()


def import_observations(
    path,
    filename,
    batch_id,
    station_name_override=None,
    object_type_override=None,
    coordinates_override=None,
    region_override=None,
):
    fieldnames, rows = read_hydrology_csv(path)
    date_column, series = detect_series(fieldnames, filename)
    station_name, object_type = station_from_filename(filename)
    station_name = station_name_override or station_name
    object_type = object_type_override or object_type
    columns = {normalize_words(name): name for name in fieldnames}
    latitude_column, longitude_column, region_column = metadata_columns(fieldnames)
    quality_column = next(
        (columns[key] for key in ("quality flag", "quality", "качество") if key in columns),
        None,
    )
    note_column = next(
        (columns[key] for key in ("quality note", "quality comment", "примечание качества") if key in columns),
        None,
    )
    normalized = {}
    skipped = {(parameter, kind): 0 for _, parameter, kind in series}
    coordinates_found = set()
    regions_found = set()

    for row in rows:
        if latitude_column and longitude_column:
            latitude_text = (row.get(latitude_column) or "").strip()
            longitude_text = (row.get(longitude_column) or "").strip()
            if latitude_text or longitude_text:
                try:
                    latitude = float(latitude_text.replace(",", "."))
                    longitude = float(longitude_text.replace(",", "."))
                except ValueError as exc:
                    raise ValueError("В CSV найдены некорректные координаты") from exc
                if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    raise ValueError("Координаты CSV выходят за допустимые пределы")
                coordinates_found.add((latitude, longitude))
        if region_column:
            region_name = (row.get(region_column) or "").strip()
            if region_name:
                regions_found.add(region_name)
        date_text = parse_date(row.get(date_column))
        for column, parameter, kind in series:
            value = parse_value(row.get(column))
            key = (parameter, kind)
            if date_text is None or value is None:
                skipped[key] += 1
                continue
            quality = parse_quality(row.get(quality_column)) if quality_column else "unreviewed"
            note = (row.get(note_column) or "").strip() if note_column else ""
            normalized.setdefault(key, {})[date_text] = (value, quality, note)
    if len(coordinates_found) > 1:
        raise ValueError("В одном CSV указаны разные координаты объекта")
    if len(regions_found) > 1:
        raise ValueError("В одном CSV указаны разные области для объекта")
    normalized = {key: values for key, values in normalized.items() if values}
    if not normalized:
        raise ValueError("В файле нет строк с корректными датами и значениями")

    con = connect_db()
    try:
        with con:
            coordinates = coordinates_override or next(iter(coordinates_found), None)
            region = region_override or next(iter(regions_found), None)
            station_id = find_or_create_station(
                con,
                filename,
                coordinates,
                region,
                station_name_override,
                object_type_override,
            )
            station_location = con.execute(
                "SELECT lat,lon FROM stations WHERE station_id=?", (station_id,)
            ).fetchone()
            location_note = (
                "Координаты указаны"
                if station_location["lat"] is not None
                and station_location["lon"] is not None
                else "Координат нет: точка не появится на карте, пока они не будут добавлены"
            )
            file_cursor = con.execute(
                """
                INSERT INTO import_files(batch_id,filename,station_id,status,message)
                VALUES(?,?,?,?,?)
                """,
                (
                    batch_id,
                    filename,
                    station_id,
                    "imported",
                    f"Распознано: {station_name} ({object_type}). {location_note}",
                ),
            )
            file_id = file_cursor.lastrowid
            for (parameter, kind), values in normalized.items():
                dates = sorted(values)
                existing = {
                    row["date"]
                    for row in con.execute(
                        """
                        SELECT date FROM observations
                        WHERE station=? AND parameter=? AND series_type=?
                          AND date BETWEEN ? AND ?
                        """,
                        (station_id, parameter, kind, dates[0], dates[-1]),
                    )
                } & values.keys()
                con.executemany(
                    """
                    INSERT INTO observations(
                        station,parameter,date,value,quality_flag,quality_note,series_type
                    )
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(station,parameter,date,series_type) DO UPDATE SET
                        value=excluded.value,
                        quality_flag=excluded.quality_flag,
                        quality_note=excluded.quality_note
                    """,
                    [
                        (station_id, parameter, day, value, quality, note or None, kind)
                        for day, (value, quality, note) in values.items()
                    ],
                )
                con.execute(
                    """
                    INSERT INTO import_series(
                        file_id,parameter,series_type,rows_added,rows_replaced,rows_skipped
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        file_id,
                        parameter,
                        kind,
                        len(values) - len(existing),
                        len(existing),
                        skipped[(parameter, kind)],
                    ),
                )
            total_rows = sum(len(values) for values in normalized.values())
            con.execute(
                "UPDATE import_files SET message=? WHERE id=?",
                (
                    f"Распознано: {station_name} ({object_type}); "
                    f"рядов данных: {total_rows}. {location_note}",
                    file_id,
                ),
            )
    finally:
        con.close()


if __name__ == "__main__":
    init_database()

    try:
        import threading
        from PyQt5.QtCore import QUrl
        from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox
        from PyQt5.QtWebEngineWidgets import (
            QWebEngineDownloadItem,
            QWebEnginePage,
            QWebEngineView,
        )
        from logging.handlers import RotatingFileHandler
        from werkzeug.serving import WSGIRequestHandler, make_server
    except ImportError as exc:
        raise RuntimeError(
            "Для клиентского окна требуется PyQt5. "
            "Установите зависимости из requirements.txt"
        ) from exc

    file_handler = RotatingFileHandler(
        DATA_DIR / "hydrogis.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False

    class LocalRequestHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            if len(args) > 1 and str(args[1]).startswith("5"):
                app.logger.error(format, *args)

    http_server = make_server(
        "127.0.0.1",
        0,
        app,
        threaded=True,
        request_handler=LocalRequestHandler,
    )
    server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    server_thread.start()

    qt_app = QApplication(sys.argv)
    window = QWebEngineView()
    window.setWindowTitle("HydroGIS — Hydrological Data GIS")
    window.resize(1280, 820)
    if ICON_PATH.is_file():
        from PyQt5.QtGui import QIcon
        window.setWindowIcon(QIcon(str(ICON_PATH)))

    class HydroPage(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line_number, source_id):
            app.logger.error(
                "WebEngine JavaScript (%s:%s): %s",
                source_id,
                line_number,
                message,
            )

    page = HydroPage(window)
    page.profile().setHttpUserAgent(
        page.profile().httpUserAgent() + " HydroGIS/1.0"
    )
    window.setPage(page)

    def save_download(download):
        filename = Path(download.downloadFileName()).name or "hydrogis-export"
        export_dir = DATA_DIR / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        default_path = export_dir / filename
        file_path, selected_filter = QFileDialog.getSaveFileName(
            window,
            "Сохранить файл",
            str(default_path),
            "Изображение PNG (*.png);;Таблица CSV (*.csv);;Все файлы (*)",
        )
        if not file_path:
            download.cancel()
            return

        target = Path(file_path)
        if not target.suffix:
            extension = ".csv" if "CSV" in selected_filter else ".png"
            target = target.with_suffix(extension)
        download.setPath(str(target))

        def report_download_finished():
            if download.state() == QWebEngineDownloadItem.DownloadCompleted:
                QMessageBox.information(
                    window,
                    "Файл сохранён",
                    f"Файл сохранён:\n{target}",
                )
            elif download.state() == QWebEngineDownloadItem.DownloadInterrupted:
                QMessageBox.warning(
                    window,
                    "Ошибка сохранения",
                    f"Не удалось сохранить файл:\n{target}",
                )

        download.finished.connect(report_download_finished)
        download.accept()

    page.profile().downloadRequested.connect(save_download)

    def show_load_error(ok):
        if not ok:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                window,
                "HydroGIS",
                "Не удалось открыть локальный интерфейс. "
                "Проверьте, что папка приложения скопирована целиком.",
            )

    window.loadFinished.connect(show_load_error)
    window.load(QUrl(f"http://127.0.0.1:{http_server.server_port}/login"))
    window.showMaximized()
    exit_code = qt_app.exec_()
    http_server.shutdown()
    sys.exit(exit_code)
