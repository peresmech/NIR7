import sys
from pathlib import Path

from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from viz.article_section_map import create_article_section_map


class ArticleMapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Article Map Preview")
        self.resize(1400, 900)

        output_path = create_article_section_map()

        browser = QWebEngineView()
        browser.setUrl(QUrl.fromLocalFile(str(output_path)))
        self.setCentralWidget(browser)


def main():
    app = QApplication(sys.argv)
    window = ArticleMapWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
