import hashlib
import hmac
import io
import os
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
from werkzeug.utils import secure_filename

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, MonthLocator, YearLocator


APP_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys._MEIPASS)
else:
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
ICON_PATH = RESOURCES_DIR / "hydro-regime.ico"

SECRET_FILE = DATA_DIR / ".secret"
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")

app = Flask(__name__)
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
    con.execute("UPDATE observations SET date=substr(date,1,10)")
    con.execute(
        """
        DELETE FROM observations
        WHERE rowid NOT IN (
            SELECT MAX(rowid)
            FROM observations
            GROUP BY station, parameter, date
        )
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS observations_unique "
        "ON observations(station, parameter, date)"
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
                    object_type,river_section,sort_order
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(station_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    river=excluded.river,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    db_station=excluded.db_station,
                    object_type=excluded.object_type,
                    river_section=excluded.river_section,
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
                    int(getattr(row, "sort_order")),
                ),
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
               COALESCE(source_istok,'') AS source_istok,
               COALESCE(tp,'') AS tp,
               COALESCE(sort_order,0) AS sort_order
        FROM stations
        ORDER BY
            CASE river WHEN 'Волга' THEN 0 WHEN 'Кама' THEN 1 ELSE 2 END,
            CASE river_section
                WHEN 'upper_volga' THEN 0
                WHEN 'middle_volga' THEN 1
                WHEN 'saratov_volgograd' THEN 2
                WHEN 'kama' THEN 0
                ELSE 3
            END,
            sort_order,
            display_name
        """
    ).fetchall()
    con.close()
    station_groups = {}
    section_labels = {
        "upper_volga": "Верхний каскад Волги",
        "middle_volga": "Средний каскад Волги",
        "saratov_volgograd": "Саратовско-Волгоградский участок",
        "kama": "Камский каскад",
        "": "Участок не указан",
    }
    for station in stations:
        section = section_labels.get(station["river_section"], station["river_section"])
        group_name = f"{station['river']} — {section}" if station["river"] else section
        station_groups.setdefault(group_name, []).append(station)
    return render_template(
        "index.html",
        stations=stations,
        station_groups=station_groups,
        username=current_user(),
        is_admin=is_admin(),
    )


@app.route("/map")
@login_required
def map_page():
    station_id = request.args.get("station")
    overview = request.args.get("overview", "all")
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
        if (overview == "all")
        or (overview == "volga" and row["river"] == "Волга")
        or (overview == "kama" and row["river"] == "Кама")
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
    )


@app.route("/plot")
@login_required
def plot():
    station_id = request.args.get("station", "")
    parameter = request.args.get("parameter", "discharge")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    con = connect_db()
    station = con.execute(
        "SELECT display_name FROM stations WHERE station_id=?", (station_id,)
    ).fetchone()
    parameter_meta = con.execute(
        "SELECT label,unit FROM parameters WHERE code=?", (parameter,)
    ).fetchone()
    query = (
        "SELECT date,value,quality_flag FROM observations "
        "WHERE station=? AND parameter=? "
    )
    params = [station_id, parameter]
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
        figsize=(2.7, 1.25) if compact else (10, 4.8),
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


def observation_filters(stations, parameter, date_from, date_to):
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
    return con


@app.route("/summary")
@login_required
def summary():
    station_id = request.args.get("station", "")
    parameter = request.args.get("parameter", "discharge")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
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
        "WHERE station=? AND parameter=?"
    )
    args = [station_id, parameter]
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
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    try:
        con = observation_filters(station_ids, parameter, date_from, date_to)
    except ValueError as exc:
        return str(exc), 400
    query = (
        "SELECT s.display_name,s.river,s.source_istok,s.tp,p.label AS parameter_label,"
        "p.unit,o.date,o.value,o.quality_flag,o.quality_note "
        "FROM observations o JOIN stations s ON s.station_id=o.station "
        "JOIN parameters p ON p.code=o.parameter "
        f"WHERE o.station IN ({','.join('?' for _ in station_ids)}) AND o.parameter=?"
    )
    args = [*station_ids, parameter]
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
        download_name=f"hydro_{suffix}_{parameter}.csv",
    )


@app.route("/compare")
@login_required
def compare():
    station_ids = request.args.get("stations", "").split(",")
    station_ids = list(dict.fromkeys(item for item in station_ids if item))
    parameter = request.args.get("parameter", "discharge")
    mode = request.args.get("mode", "timeseries")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    try:
        con = observation_filters(station_ids, parameter, date_from, date_to)
    except ValueError as exc:
        return str(exc), 400
    sql = (
        "SELECT o.station,s.display_name,o.date,o.value "
        "FROM observations o JOIN stations s ON s.station_id=o.station "
        f"WHERE o.station IN ({','.join('?' for _ in station_ids)}) AND o.parameter=?"
    )
    args = [*station_ids, parameter]
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
    con.close()
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
    fig, ax = plt.subplots(figsize=(11, 6), dpi=130)
    if frame.empty:
        ax.text(0.5, 0.5, "Нет данных для сравнения", ha="center", va="center")
    elif mode == "annual":
        frame["year"] = frame["date"].dt.year
        annual = frame.groupby(["station", "display_name", "year"], as_index=False)["value"].mean()
        for _, group in annual.groupby("station"):
            name = group["display_name"].iloc[0]
            ax.plot(group["year"], group["value"], marker="o", linewidth=1.8, label=name)
        ax.set_xlabel("Год")
        ax.set_title(f"Межгодовая динамика: {meta['label']}")
    else:
        for _, group in frame.groupby("station"):
            name = group["display_name"].iloc[0]
            ax.plot(group["date"], group["value"], linewidth=1.25, label=name)
        ax.xaxis.set_major_formatter(DateFormatter("%Y"))
        ax.tick_params(axis="x", rotation=30)
        ax.set_xlabel("Дата")
        ax.set_title(f"Сравнение гидрологических рядов: {meta['label']}")
    ax.set_ylabel(f"{meta['label']} ({meta['unit']})")
    ax.grid(True, color="#dde5ef")
    if not frame.empty:
        ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    output = io.BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    output.seek(0)
    filename = "hydro_annual_comparison.png" if mode == "annual" else "hydro_comparison.png"
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
        WHERE station=? AND parameter=? AND date>=? AND date<=?
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
        station_id = request.form.get("station_id", "").strip()
        parameter = request.form.get("parameter", "").strip()
        quality_flag = request.form.get("quality_flag", "unreviewed").strip()
        unit = request.form.get("unit", "").strip()
        file = request.files.get("csv_file")
        if (
            not station_id
            or parameter not in ("discharge", "level_upper", "level_lower", "level")
            or quality_flag not in ("unreviewed", "checked", "suspect")
        ):
            error = "Выберите станцию и параметр"
        elif file is None or file.filename == "":
            error = "Выберите CSV-файл"
        else:
            filename = secure_filename(file.filename)
            path = UPLOAD_DIR / filename
            file.save(path)
            try:
                rows_added, rows_replaced = import_observations(
                    path, station_id, parameter, quality_flag, unit or None
                )
                message = f"Импорт завершён: добавлено {rows_added}, заменено {rows_replaced}"
            except Exception as exc:
                error = f"Ошибка импорта: {exc}"

    con = connect_db()
    stations = con.execute(
        "SELECT station_id,display_name FROM stations ORDER BY display_name"
    ).fetchall()
    imports = con.execute(
        "SELECT * FROM import_log ORDER BY id DESC LIMIT 20"
    ).fetchall()
    con.close()
    backups = sorted(BACKUP_DIR.glob("hydro_*.db"), reverse=True)
    return render_template(
        "admin.html",
        stations=stations,
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
    lat = request.form.get("lat", "").strip()
    lon = request.form.get("lon", "").strip()
    object_type = request.form.get("object_type", "hub").strip()
    river_section = request.form.get("river_section", "").strip()
    source_name = request.form.get("source_name", "").strip()
    source_type = request.form.get("source_type", "").strip()
    source_note = request.form.get("source_note", "").strip()
    source_istok = request.form.get("source_istok", "").strip()
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

    con = connect_db()
    station_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(stations)").fetchall()
    }
    for column, definition in (
        ("db_station", "TEXT"),
        ("object_type", "TEXT"),
        ("river_section", "TEXT"),
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
        """
        INSERT INTO stations(
            station_id,display_name,river,lat,lon,db_station,
            object_type,river_section,sort_order,
            source_name,source_type,source_note,source_istok,tp
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(station_id) DO UPDATE SET
            display_name=excluded.display_name,
            river=excluded.river,
            lat=excluded.lat,
            lon=excluded.lon,
            db_station=excluded.db_station,
            object_type=excluded.object_type,
            river_section=excluded.river_section,
            source_name=excluded.source_name,
            source_type=excluded.source_type,
            source_note=excluded.source_note,
            source_istok=excluded.source_istok,
            tp=excluded.tp
        """,
        (
            station_id, display_name, river, lat_value, lon_value, station_id,
            object_type, river_section, 0,
            source_name, source_type, source_note, source_istok, tp,
        ),
    )
    con.commit()
    con.close()
    audit("Справочник объектов", f"Создан/изменён объект {station_id}: {display_name}")
    return redirect(url_for("admin"))


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


def import_observations(path, station_id, parameter, quality_flag="unreviewed", unit=None):
    try:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    except UnicodeDecodeError:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="cp1251")
    date_candidates = ("Dates", "date", "Date", "дата", "Дата")
    date_col = next((name for name in date_candidates if name in frame.columns), None)
    possible_values = {
        "discharge": ["Discharge"],
        "level_upper": ["Absolute_Levels"],
        "level_lower": ["Absolute_Levels"],
        "level": ["Absolute_Levels"],
    }
    value_col = "value" if "value" in frame.columns else None
    if value_col is None:
        for name in possible_values[parameter]:
            if name in frame.columns:
                value_col = name
                break
    if date_col is None or value_col is None:
        raise ValueError(
            "Нужны столбцы даты (Dates/date/дата) и значения "
            "(Discharge/Absolute_Levels/value)"
        )

    clean = pd.DataFrame({
        "date": pd.to_datetime(frame[date_col], dayfirst=True, errors="coerce"),
        "value": pd.to_numeric(frame[value_col], errors="coerce"),
    }).dropna()
    quality_col = next(
        (name for name in ("quality_flag", "quality", "качество", "проверка") if name in frame.columns),
        None,
    )
    note_col = next(
        (name for name in ("quality_note", "quality_comment", "примечание качества") if name in frame.columns),
        None,
    )
    clean["quality_flag"] = (
        frame.loc[clean.index, quality_col].fillna(quality_flag).astype(str)
        if quality_col else quality_flag
    )
    if quality_col:
        clean["quality_flag"] = clean["quality_flag"].str.strip().str.lower().replace(
            {
                "проверено": "checked",
                "проверен": "checked",
                "норма": "checked",
                "подозрительно": "suspect",
                "подозрительное": "suspect",
                "сомнительно": "suspect",
                "не проверено": "unreviewed",
            }
        )
        clean.loc[
            ~clean["quality_flag"].isin(("unreviewed", "checked", "suspect")),
            "quality_flag",
        ] = quality_flag
    clean["quality_note"] = (
        frame.loc[clean.index, note_col].fillna("").astype(str)
        if note_col else ""
    )
    clean = clean.sort_values("date").drop_duplicates("date", keep="last")

    con = connect_db()
    station = con.execute(
        "SELECT station_id FROM stations WHERE station_id=?", (station_id,)
    ).fetchone()
    if station is None:
        con.close()
        raise ValueError("Неизвестная станция")

    metadata_aliases = {
        "river": ("river", "River", "река"),
        "display_name": ("display_name", "name", "Название", "station_name"),
        "lat": ("lat", "latitude", "широта"),
        "lon": ("lon", "longitude", "долгота"),
        "object_type": ("object_type", "type", "тип"),
        "river_section": ("river_section", "section", "участок"),
        "source_name": ("source_name", "istok", "исток"),
        "source_type": ("source_type", "source_type", "источник"),
        "source_note": ("source_note", "note", "ТП", "описание"),
        "source_istok": ("source_istok", "ИСТОК", "исток", "ISTOK"),
        "tp": ("tp", "ТП", "TП"),
        "unit": ("unit", "units", "единица измерения", "ед. изм."),
    }
    metadata = {}
    for column, aliases in metadata_aliases.items():
        alias = next((name for name in aliases if name in frame.columns), None)
        if alias is not None:
            values = frame[alias].dropna().astype(str).str.strip()
            if not values.empty:
                metadata[column] = values.iloc[0]

    unit_from_file = metadata.pop("unit", None)
    if "lat" in metadata or "lon" in metadata:
        try:
            lat_value = float(metadata["lat"])
            lon_value = float(metadata["lon"])
        except (KeyError, ValueError):
            raise ValueError("Если CSV задаёт координаты, нужны и широта, и долгота")
        if not (-90 <= lat_value <= 90 and -180 <= lon_value <= 180):
            raise ValueError("Координаты объекта в CSV находятся вне допустимого диапазона")
        metadata["lat"] = lat_value
        metadata["lon"] = lon_value
    if "object_type" in metadata:
        object_type = str(metadata["object_type"]).lower()
        object_type = {"пост": "post", "гидропост": "post", "гидроузел": "hub"}.get(
            object_type, object_type
        )
        if object_type not in ("hub", "post"):
            raise ValueError("Неизвестный тип объекта в CSV: используйте hub или post")
        metadata["object_type"] = object_type
    if metadata:
        updates = ", ".join(f"{column}=?" for column in metadata)
        con.execute(
            f"UPDATE stations SET {updates} WHERE station_id=?",
            [*metadata.values(), station_id],
        )
    parameter_unit = unit or unit_from_file
    if parameter_unit:
        con.execute("UPDATE parameters SET unit=? WHERE code=?", (parameter_unit, parameter))

    replaced = 0
    for row in clean.itertuples(index=False):
        date_text = row.date.strftime("%Y-%m-%d")
        existed = con.execute(
            "SELECT 1 FROM observations WHERE station=? AND parameter=? AND date=?",
            (station_id, parameter, date_text),
        ).fetchone()
        if existed:
            replaced += 1
        con.execute(
            """
            INSERT INTO observations(
                station,parameter,date,value,quality_flag,quality_note
            )
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(station,parameter,date) DO UPDATE SET
                value=excluded.value,
                quality_flag=excluded.quality_flag,
                quality_note=excluded.quality_note
            """,
            (
                station_id, parameter, date_text, float(row.value),
                row.quality_flag, row.quality_note or None,
            ),
        )
    con.execute(
        "INSERT INTO import_log(station_id,parameter,filename,imported_at,rows_added,rows_replaced)"
        " VALUES(?,?,?,?,?,?)",
        (station_id, parameter, path.name, datetime.now().isoformat(timespec="seconds"),
         len(clean), replaced),
    )
    con.commit()
    con.close()
    audit(
        "Импорт данных",
        f"{path.name}: {station_id}, {parameter}; {len(clean)} строк, "
        f"пометка качества: {quality_flag}",
    )
    return len(clean) - replaced, replaced


def start_server():
    app.run(
        host="127.0.0.1",
        port=5055,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    init_database()

    try:
        import threading
        from PyQt5.QtCore import QUrl
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtWebEngineWidgets import QWebEngineView
    except ImportError as exc:
        raise RuntimeError(
            "Для клиентского окна требуется PyQt5. "
            "Установите зависимости из requirements.txt"
        ) from exc

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    qt_app = QApplication(sys.argv)
    window = QWebEngineView()
    window.setWindowTitle("ГидроРежим — ГИС гидрологических данных")
    window.resize(1280, 820)
    if ICON_PATH.is_file():
        from PyQt5.QtGui import QIcon
        window.setWindowIcon(QIcon(str(ICON_PATH)))
    window.load(QUrl("http://127.0.0.1:5055/login"))
    window.showMaximized()
    sys.exit(qt_app.exec_())
