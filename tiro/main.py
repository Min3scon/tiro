"""Entry point: logging, single-instance guard, Qt application."""

from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import os
import sys

from tiro import APP_NAME, __version__
from tiro.paths import FROZEN, log_dir


def _setup_logging(verbose: bool) -> None:
    handler = logging.handlers.RotatingFileHandler(
        log_dir() / "tiro.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if FROZEN or sys.stderr is None:
        # a windowed exe has no console: send stray prints (onnxruntime, PortAudio) to a file
        stream = open(log_dir() / "stdout.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = stream
    else:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    crash = logging.getLogger("tiro.crash")

    def excepthook(exc_type, exc, tb):  # also receives exceptions raised inside Qt slots
        crash.error("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(hook_args):
        crash.error("uncaught exception in thread %s", getattr(hook_args.thread, "name", "?"),
                    exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback))

    import threading

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--autostart", action="store_true", help="started by Windows at login")
    parser.add_argument("--settings", action="store_true", help="open the settings window")
    parser.add_argument("--quit", action="store_true", help="close a running Tiro")
    parser.add_argument("--after-restart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--setup", action="store_true", help="run the setup wizard")
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)  # launched by the installer
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="OUT.json",
                        help="load the model, transcribe a test clip, report (used by the installer)")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], help="device for --selftest")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="change a setting and exit (e.g. --set device=cpu)")
    parser.add_argument("--health", metavar="OUT.json", help=argparse.SUPPRESS)  # updater: can this build start?
    parser.add_argument("--update-trial", metavar="TOKEN", help=argparse.SUPPRESS)  # launcher: first start
    parser.add_argument("--rolled-back-from", metavar="VERSION", help=argparse.SUPPRESS)
    parser.add_argument("--safe-mode", action="store_true", help="start with all optional features off")
    args, _unknown = parser.parse_known_args(argv)

    if args.health:
        from tiro.update.health import run as health

        return health(args.health)
    if args.update_trial:
        try:  # test builds only (tools/build.ps1 -TestCrash): a version that fails to start, to test roll back
            from tiro._build import CRASH_ON_TRIAL
        except ImportError:
            CRASH_ON_TRIAL = False
        if CRASH_ON_TRIAL:
            os._exit(3)
    _setup_logging(args.verbose)
    log = logging.getLogger("tiro")
    if args.set:
        from tiro.selftest import apply_settings

        log.info("applying settings from the command line: %s", args.set)
        return apply_settings(args.set)
    if args.selftest is not None:
        from tiro.selftest import run

        log.info("running self-test (device=%s)", args.device)
        return run(args.selftest or None, args.device)
    log.info("%s %s starting (frozen=%s, pid=%d)", APP_NAME, __version__, FROZEN, os.getpid())

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_NAME)
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)  # closing Settings or Setup never quits the app
    if sys.platform == "darwin":
        from tiro.platform.mac.permissions import hide_dock_icon

        hide_dock_icon()  # a menu-bar app: no Dock icon

    from tiro.paths import instance_key

    server_name = f"{APP_NAME}-{getpass.getuser()}{instance_key()}"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if args.after_restart:
        # relaunched as administrator: give the old instance a moment to exit instead of deferring to it
        import time

        deadline = time.monotonic() + 8
        while probe.waitForConnected(300) and time.monotonic() < deadline:
            probe.disconnectFromServer()
            time.sleep(0.25)
            probe = QLocalSocket()
            probe.connectToServer(server_name)
    if probe.waitForConnected(300):
        # already running: ask it to show its settings (or quit) and leave
        probe.write(b"quit" if args.quit else b"show-settings")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        log.info("another instance is running; sent it %s", "quit" if args.quit else "show-settings")
        return 0
    if args.quit:
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    from tiro.ui import theme

    theme.load_fonts()
    from tiro.ui.icons import make_icon

    qapp.setWindowIcon(make_icon("app"))

    from tiro.app import TiroApp
    from tiro.config import Settings
    from tiro.models import MODELS, find_model

    first = Settings.load()
    needs_setup = args.setup or (not first.setup_done and not args.autostart) or find_model(MODELS[first.model]) is None
    if args.update_trial and needs_setup:
        needs_setup = False  # an update never sends anyone back through setup
    app = TiroApp(qapp, autostarted=args.autostart, setup=needs_setup, safe_mode=args.safe_mode,
                  update_trial=args.update_trial, rolled_back_from=args.rolled_back_from)
    if needs_setup:
        app.show_setup(installed=args.installed)

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(300)
        msg = bytes(sock.readAll().data())
        sock.disconnectFromServer()
        if msg.startswith(b"show-settings"):
            app.show_settings()
        elif msg.startswith(b"quit"):
            app.quit()

    server.newConnection.connect(on_connection)
    if args.settings:
        app.show_settings()

    code = qapp.exec()
    log.info("exited event loop (%s)", code)
    logging.shutdown()
    # Skip interpreter teardown: CUDA/ONNX Runtime threads can make a normal exit hang for seconds.
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
