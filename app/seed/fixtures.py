"""Static seed fixtures: login users, the cellSens application, its releases
and default settings. Testing layers are not defined here — every release gets
the global pyramid from `app.layer_defaults`. No test data is seeded either;
records arrive via Excel upload or a folder sync."""

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
    },
]

# Releases roll roughly every six months and each keeps its own data forever,
# so the seed lays down a shipped one and the one under test. `order` is the
# list position: the last entry is the newest.
RELEASES = [
    {
        "appId": "cellsens",
        "releaseId": "v4-3",
        "name": "v4.3",
        "desc": "Shipped release — kept for historical comparison.",
        # blank: the folder follows the names, <excelRoot>/cellSens/v4.3
        "excelPath": "",
        "current": False,
    },
    {
        "appId": "cellsens",
        "releaseId": "v4-4",
        "name": "v4.4",
        "desc": "Release currently under test.",
        "excelPath": "",
        "current": True,
    },
]

DEFAULT_SETTINGS = {
    # parent folder for the Excel catalog, e.g. r"A:\office excel files" or a
    # UNC share. Empty by default: an admin sets it in Settings.
    "excelRoot": "",
}
