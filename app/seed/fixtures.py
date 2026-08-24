"""Static seed fixtures: login users, the cellSens application and default
settings. Testing layers are not defined here — every application gets the
global pyramid from `app.layer_defaults`. No test data is seeded either;
records arrive via Excel upload."""

USERS = [
    {"username": "admin", "password": "admin123", "name": "Aadrika Sharma", "role": "admin"},
    {"username": "qa", "password": "qa123", "name": "Rahul Verma", "role": "qa"},
    {"username": "manager", "password": "manager123", "name": "Priya Nair", "role": "manager"},
    {"username": "qa2", "password": None, "name": "Meera Iyer", "role": "qa"},
    {"username": "manager2", "password": None, "name": "Vikram Rao", "role": "manager"},
]

APPS = [
    {
        "_id": "cellsens",
        "name": "cellSens",
        "tag": "Imaging & Microscopy",
        "desc": "Life-science imaging software for microscope control, acquisition and analysis.",
        "icon": "scope",
        # folder under settings.excelRoot holding this app's layer workbooks
        "excelPath": "cellsens",
    },
]

DEFAULT_SETTINGS = {
    # parent folder for the Excel catalog, e.g. r"A:\office excel files" or a
    # UNC share. Empty by default: an admin sets it in Settings.
    "excelRoot": "",
    "repoUrl": "https://github.com/Aadrika8/test_suites.git",
    "branch": "main",
    "cacheDir": "~/TestRunner/cache",
    "timeoutSeconds": 60,
    "rootFolder": "test_suites",
    "levels": ["application", "suite", "release"],
    "extensions": [".py"],
}
