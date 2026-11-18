import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from viz.map import create_map


def main():
    m = create_map(
        "data/network_edges.geojson",
        "data/network_nodes.geojson",
    )

    m.save("map.html")
    print("Карта сохранена как map.html")


if __name__ == "__main__":
    main()
