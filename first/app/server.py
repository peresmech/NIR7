from flask import Flask, request, Response
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import io

app = Flask(__name__)


@app.route("/")
def home():
    return "Hydro GIS server работает"


@app.route("/station/<station_name>")
def station_page(station_name):

    parameter = request.args.get("param", "discharge")

    html = f"""
    <h2>{station_name}</h2>

    <a href="/station/{station_name}?param=discharge">Расход</a> |
    <a href="/station/{station_name}?param=level_lower">Нижний уровень</a> |
    <a href="/station/{station_name}?param=level_upper">Верхний уровень</a>

    <br><br>

    <img src="/plot/{station_name}?param={parameter}">
    """

    return html


@app.route("/plot/<station_name>")
def plot(station_name):

    parameter = request.args.get("param", "discharge")

    conn = sqlite3.connect("data/hydro.db")

    df = pd.read_sql("""
        SELECT date, value FROM observations
        WHERE station = ?
        AND parameter = ?
        ORDER BY date
    """, conn, params=(station_name, parameter))

    conn.close()

    if df.empty:
        return Response("Нет данных для выбранной станции или параметра", status=404)

    plt.figure()
    plt.plot(pd.to_datetime(df["date"]), df["value"])
    plt.title(f"{station_name} - {parameter}")
    plt.xticks(rotation=45)

    img = io.BytesIO()
    plt.savefig(img, format="png", bbox_inches="tight")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")


if __name__ == "__main__":
    app.run(debug=True, port=5051)
