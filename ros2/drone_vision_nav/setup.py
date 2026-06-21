from pathlib import Path

from setuptools import setup


PACKAGE_NAME = "drone_vision_nav"
PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
LAUNCH_FILES = [str(path) for path in (PACKAGE_DIR.parent / "launch").glob("*.launch.py")]


setup(
    name=PACKAGE_NAME,
    version="0.1.0",
    packages=[PACKAGE_NAME, "vision_nav"],
    package_dir={
        PACKAGE_NAME: str(PACKAGE_DIR / PACKAGE_NAME),
        "vision_nav": str(ROOT / "src" / "vision_nav"),
    },
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{PACKAGE_NAME}"]),
        (f"share/{PACKAGE_NAME}", ["package.xml"]),
        (f"share/{PACKAGE_NAME}/launch", LAUNCH_FILES),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Drone Vision Nav",
    maintainer_email="dev@example.com",
    description="ROS 2 package wrapper for Drone GNSS-denied terrain vision navigation.",
    license="Proprietary",
    entry_points={
        "console_scripts": [
            "terrain_nav_live = drone_vision_nav.terrain_nav_live:main",
            "terrain_nav_replay = drone_vision_nav.terrain_nav_replay:main",
        ],
    },
)
