import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("DJANGO_SECRET_KEY", "build-only-not-used-at-runtime")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "QnVpbGRPbmx5Tm90VXNlZEF0UnVudGltZTAwMDAwMDA=")

hidden = []
for package in ("apps", "config", "django", "rest_framework", "rest_framework_simplejwt", "django_filters", "drf_spectacular", "corsheaders", "waitress", "reportlab", "openpyxl", "arabic_reshaper", "bidi", "pyotp", "dj_database_url", "dotenv"):
    hidden += collect_submodules(package)

datas = [("apps/reports/fonts", "apps/reports/fonts")]
for package in ("django", "rest_framework", "rest_framework_simplejwt", "django_filters", "drf_spectacular", "reportlab", "arabic_reshaper", "openpyxl"):
    datas += collect_data_files(package)

analysis = Analysis(
    ["vizora_server.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tests", "psycopg", "psycopg_binary", "tkinter", "PyInstaller", "IPython", "pytest"],
    noarchive=False,
)
PRIVATE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".env", "keys.json")
analysis.datas = [entry for entry in analysis.datas if not entry[0].lower().endswith(PRIVATE_SUFFIXES)]
archive = PYZ(analysis.pure)
program = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="vizora-server",
    console=True,
    upx=False,
)
COLLECT(program, analysis.binaries, analysis.datas, name="vizora-server", upx=False)
