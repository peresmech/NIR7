import hashlib
import hmac
import io
import os
import secrets
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import folium
import matplotlib
import pandas as pd
from flask import Flask, redirect, render_template, request, send_file, session, url_for
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
    if getattr(sys, "frozen", False):
        seed = PROJECT_DIR / "data_seed" / "hydro.db"

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
        """
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
               COALESCE(sort_order,0) AS sort_order
        FROM stations
        ORDER BY river, sort_order, display_name
        """
    ).fetchall()
    con.close()
    return render_template(
        "index.html",
        stations=stations,
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
               COALESCE(object_type,'hub') AS object_type
        FROM stations
        """
    ).fetchall()
    con.close()

    if not rows:
        m = folium.Map(location=(55.7, 47.5), zoom_start=5)
    else:
        center = (
            sum(r["lat"] for r in rows) / len(rows),
            sum(r["lon"] for r in rows) / len(rows),
        )
        m = folium.Map(location=center, zoom_start=5)

    folium.TileLayer(
        "OpenStreetMap", name="OpenStreetMap", attr="OpenStreetMap contributors"
    ).add_to(m)

    for row in rows:
        if overview == "volga" and row["river"] != "Волга":
            continue
        if overview == "kama" and row["river"] != "Кама":
            continue
        kind = "Гидроузел" if row["object_type"] == "hub" else "Гидропост"
        popup = f"{row['display_name']}<br>{kind}<br>Река: {row['river']}"
        color = "#e67e22" if row["station_id"] == station_id else "#1976d2"
        folium.CircleMarker(
            location=(row["lat"], row["lon"]),
            radius=9 if row["station_id"] == station_id else 6,
            popup=popup,
            tooltip=row["display_name"],
            color=color,
            fill=True,
            fill_color=color,
        ).add_to(m)

    return m.get_root().render()


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
    query = (
        "SELECT date,value FROM observations "
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

    labels = {
        "discharge": "Расход воды, м³/с",
        "level_upper": "Уровень верхнего бьефа, м",
        "level_lower": "Уровень нижнего бьефа, м",
        "level": "Уровень воды, м",
    }
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=130)
    fig.patch.set_facecolor("#f5f7fa")
    ax.set_facecolor("white")
    ax.grid(True, color="#dde5ef", linewidth=0.8)

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        ax.plot(df["date"], df["value"], color="#1769aa", linewidth=1.5)
        span = max((df["date"].max() - df["date"].min()).days, 1)
        if span <= 120:
            ax.xaxis.set_major_formatter(DateFormatter("%d.%m"))
        elif span <= 365 * 3:
            ax.xaxis.set_major_locator(MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(DateFormatter("%m.%Y"))
        else:
            ax.xaxis.set_major_locator(YearLocator())
            ax.xaxis.set_major_formatter(DateFormatter("%Y"))
        ax.tick_params(axis="x", rotation=30)
    else:
        ax.text(0.5, 0.5, "Нет данных за выбранный период", ha="center", va="center")

    title = station["display_name"] if station else station_id
    ax.set_title(f"{title}: {labels.get(parameter, parameter)}")
    ax.set_ylabel(labels.get(parameter, parameter))
    ax.set_xlabel("Дата")
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
        file = request.files.get("csv_file")
        if not station_id or parameter not in ("discharge", "level_upper", "level_lower", "level"):
            error = "Выберите станцию и параметр"
        elif file is None or file.filename == "":
            error = "Выберите CSV-файл"
        else:
            filename = secure_filename(file.filename)
            path = UPLOAD_DIR / filename
            file.save(path)
            try:
                rows_added, rows_replaced = import_observations(path, station_id, parameter)
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
    return render_template(
        "admin.html",
        stations=stations,
        imports=imports,
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
    ):
        if column not in station_columns:
            con.execute(f"ALTER TABLE stations ADD COLUMN {column} {definition}")

    con.execute(
        """
        INSERT INTO stations(
            station_id,display_name,river,lat,lon,db_station,
            object_type,river_section,sort_order,
            source_name,source_type,source_note
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
            source_note=excluded.source_note
        """,
        (
            station_id, display_name, river, lat_value, lon_value, station_id,
            object_type, river_section, 0,
            source_name, source_type, source_note,
        ),
    )
    con.commit()
    con.close()
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
    return redirect(url_for("admin"))


def import_observations(path, station_id, parameter):
    frame = pd.read_csv(path)
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
    }
    metadata = {}
    for column, aliases in metadata_aliases.items():
        alias = next((name for name in aliases if name in frame.columns), None)
        if alias is not None:
            values = frame[alias].dropna().astype(str).str.strip()
            if not values.empty:
                metadata[column] = values.iloc[0]

    if metadata:
        updates = ", ".join(f"{column}=?" for column in metadata)
        con.execute(
            f"UPDATE stations SET {updates} WHERE station_id=?",
            [*metadata.values(), station_id],
        )

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
            INSERT INTO observations(station,parameter,date,value)
            VALUES(?,?,?,?)
            ON CONFLICT(station,parameter,date) DO UPDATE SET value=excluded.value
            """,
            (station_id, parameter, date_text, float(row.value)),
        )
    con.execute(
        "INSERT INTO import_log(station_id,parameter,filename,imported_at,rows_added,rows_replaced)"
        " VALUES(?,?,?,?,?,?)",
        (station_id, parameter, path.name, datetime.now().isoformat(timespec="seconds"),
         len(clean), replaced),
    )
    con.commit()
    con.close()
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
    window.setWindowTitle("HydroGIS")
    window.resize(1280, 820)
    window.load(QUrl("http://127.0.0.1:5055/login"))
    window.show()
    sys.exit(qt_app.exec_())
