import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="airdesk", description="Control your computer with hand gestures from a webcam.")
    parser.add_argument("--camera", type=int, default=0, help="camera number (default 0)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from PyQt6.QtWidgets import QApplication

    from .app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AirDesk")
    window = MainWindow(args.camera)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
