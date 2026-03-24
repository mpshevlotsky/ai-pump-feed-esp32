"""
ESP32 board management tool for AI Pump Bridge.

Usage:
    python esp.py --deploy                          Upload firmware files
    python esp.py --flash --deploy                  Flash MicroPython + deploy
    python esp.py --flash --libs --deploy --monitor Full setup
    python esp.py --monitor                         Open REPL
    python esp.py --deploy --port COM3              Deploy to specific port
"""

import argparse
import glob
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_DIR: str = os.path.dirname(os.path.abspath(__file__))

FIRMWARE_BIN_PATTERN: str = os.path.join(PROJECT_DIR, "tools", "*.bin")

# MicroPython libraries to install via mip
MIP_LIBS: list = ["aioble"]

# Files that go to ESP32 root (MicroPython requirement)
ROOT_FILES: list = [
    "firmware/boot.py",
    "firmware/main.py",
]

# Directories to deploy recursively
DEPLOY_DIRS: list = [
    "firmware",
    "core",
]

# Extensions to deploy
DEPLOY_EXTENSIONS: set = {".py", ".html", ".css", ".js", ".json", ".yaml"}

# Files already handled by ROOT_FILES — skip in directory scan
SKIP_FILES: set = {
    os.path.normpath("firmware/boot.py"),
    os.path.normpath("firmware/main.py"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(args: list, description: str) -> bool:
    """Run a subprocess command. Returns True on success."""
    print("  %s" % description)
    result = subprocess.run(args)
    if result.returncode != 0:
        print("  FAILED: %s" % " ".join(args))
        return False
    return True


def find_firmware_bin() -> str:
    """Find the MicroPython .bin file in tools/."""
    bins = sorted(glob.glob(FIRMWARE_BIN_PATTERN))
    if not bins:
        print("ERROR: No .bin file found in tools/")
        print("Download from https://micropython.org/download/ESP32_GENERIC_S3/")
        sys.exit(1)
    if len(bins) > 1:
        print("WARNING: Multiple .bin files found, using newest:")
        for b in bins:
            print("  %s" % os.path.basename(b))
    return bins[-1]


def collect_deploy_files() -> list:
    """Scan DEPLOY_DIRS and return list of (local_path, esp32_path) tuples."""
    files: list = []

    for deploy_dir in DEPLOY_DIRS:
        abs_dir = os.path.join(PROJECT_DIR, deploy_dir)
        if not os.path.isdir(abs_dir):
            continue

        for dirpath, _dirnames, filenames in os.walk(abs_dir):
            if "__pycache__" in dirpath:
                continue

            for fname in sorted(filenames):
                local_abs = os.path.join(dirpath, fname)
                local_rel = os.path.relpath(local_abs, PROJECT_DIR)
                local_rel_norm = os.path.normpath(local_rel)

                if local_rel_norm in SKIP_FILES:
                    continue

                _, ext = os.path.splitext(fname)
                if ext not in DEPLOY_EXTENSIONS:
                    continue

                esp32_path = local_rel.replace(os.sep, "/")
                files.append((local_rel, esp32_path))

    return files


def collect_esp32_dirs(files: list) -> list:
    """Extract unique directories that need to be created on ESP32."""
    dirs: set = set()
    for _, esp32_path in files:
        parts = esp32_path.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)


def confirm(actions: list, port: str, file_count: int) -> bool:
    """Show action plan and ask for confirmation. Returns True if confirmed."""
    print("\n--- Deploy plan ---")
    print("  Port:    %s" % (port or "auto-detect"))
    print("  Actions: %s" % " -> ".join(actions))
    if file_count > 0:
        print("  Files:   %d" % file_count)
    print("-------------------\n")

    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def esptool_cmd(port: str, *args: str) -> list:
    """Build esptool command. Port passed only if explicitly set."""
    cmd = [sys.executable, "-m", "esptool"]
    if port:
        cmd.extend(["--port", port])
    cmd.extend(args)
    return cmd


def mpremote_cmd(port: str, *args: str) -> list:
    """Build mpremote command. Port passed only if explicitly set."""
    cmd = [sys.executable, "-m", "mpremote"]
    if port:
        cmd.extend(["connect", port])
    cmd.extend(args)
    return cmd


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def action_flash(port: str) -> bool:
    """Erase flash and write MicroPython firmware."""
    firmware_bin = find_firmware_bin()
    print("\nFlashing: %s" % os.path.basename(firmware_bin))

    if not run(
        esptool_cmd(port, "erase-flash"),
        "Erasing flash...",
    ):
        return False

    if not run(
        esptool_cmd(port, "write-flash", "0", firmware_bin),
        "Writing firmware...",
    ):
        return False

    print("Flash complete. Waiting for board to boot...")
    time.sleep(8)
    return True


def action_install_libs(port: str) -> bool:
    """Install MicroPython libraries via mip."""
    print("\nInstalling libraries...")
    # Soft-reset the board first so mpremote can enter raw REPL cleanly
    subprocess.run(
        mpremote_cmd(port, "soft-reset"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    for lib in MIP_LIBS:
        if not run(mpremote_cmd(port, "mip", "install", lib), "mip install %s" % lib):
            return False
    return True


def action_deploy(port: str) -> bool:
    """Deploy application files to ESP32."""
    print("\nCollecting files...")
    deploy_files = collect_deploy_files()
    esp32_dirs = collect_esp32_dirs(deploy_files)

    total_files = len(deploy_files) + len(ROOT_FILES)
    print("  %d files, %d directories\n" % (total_files, len(esp32_dirs)))

    # Create directories
    print("Creating directories...")
    for d in esp32_dirs:
        subprocess.run(
            mpremote_cmd(port, "fs", "mkdir", ":%s" % d),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("  :%s/" % d)

    # Deploy root files (boot.py, main.py -> ESP32 root)
    print("\nUploading root files...")
    for local_rel in ROOT_FILES:
        fname = os.path.basename(local_rel)
        esp32_target = ":%s" % fname
        if not run(
            mpremote_cmd(port, "fs", "cp", local_rel, esp32_target),
            "%s -> %s" % (local_rel, esp32_target),
        ):
            return False

    # Deploy module files
    print("\nUploading modules...")
    for local_rel, esp32_path in deploy_files:
        esp32_target = ":%s" % esp32_path
        if not run(
            mpremote_cmd(port, "fs", "cp", local_rel, esp32_target),
            "%s -> %s" % (local_rel, esp32_target),
        ):
            return False

    # Reset board
    print("\nResetting board...")
    if not run(mpremote_cmd(port, "reset"), "Reset"):
        return False

    print("\nDeploy complete.")
    return True


def action_monitor(port: str) -> None:
    """Open REPL for log monitoring."""
    print("\nOpening REPL (Ctrl+] to exit)...")
    subprocess.run(mpremote_cmd(port, "repl"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESP32 board management tool for AI Pump Bridge",
    )
    actions_group = parser.add_argument_group("actions (combine as needed)")
    actions_group.add_argument(
        "--flash",
        action="store_true",
        help="Erase flash and write MicroPython firmware",
    )
    actions_group.add_argument(
        "--libs",
        action="store_true",
        help="Install MicroPython libraries (aioble) via mip",
    )
    actions_group.add_argument(
        "--deploy",
        action="store_true",
        help="Upload application files to ESP32",
    )
    actions_group.add_argument(
        "--monitor",
        action="store_true",
        help="Open REPL for log monitoring",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (auto-detected if omitted). Examples: /dev/ttyACM0, COM3",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    # No actions specified — show help
    if not any([args.flash, args.libs, args.deploy, args.monitor]):
        parser.print_help()
        sys.exit(0)

    os.chdir(PROJECT_DIR)

    # Build action plan
    actions: list = []
    if args.flash:
        actions.append("flash")
    if args.libs:
        actions.append("libs")
    if args.deploy:
        actions.append("deploy")
    if args.monitor:
        actions.append("monitor")

    # Count files for the plan summary
    file_count: int = 0
    if args.deploy:
        file_count = len(collect_deploy_files()) + len(ROOT_FILES)

    # Confirm
    if not args.yes:
        if not confirm(actions, args.port, file_count):
            print("Aborted.")
            sys.exit(0)

    # Execute
    if args.flash:
        if not action_flash(args.port):
            sys.exit(1)

    if args.libs:
        if not action_install_libs(args.port):
            sys.exit(1)

    if args.deploy:
        if not action_deploy(args.port):
            sys.exit(1)

    if args.monitor:
        action_monitor(args.port)


if __name__ == "__main__":
    main()