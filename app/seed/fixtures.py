"""Static seed fixtures: login users, the cellSens application, its releases
and default settings. Testing layers are not defined here — every release gets
the global pyramid from `app.layer_defaults`. No test data is seeded either;
records arrive via Excel upload or a folder sync."""

# `passwordSetting` names the field of `app.config.Settings` (and so the
# SEED_*_PASSWORD variable in .env) the account's initial password is read
# from. No password is written here. Accounts without one cannot sign in.
USERS = [
    {"username": "admin", "passwordSetting": "seed_admin_password",
     "name": "Aadrika Sharma", "role": "admin"},
    {"username": "qa", "passwordSetting": "seed_qa_password",
     "name": "Rahul Verma", "role": "qa"},
    {"username": "manager", "passwordSetting": "seed_manager_password",
     "name": "Priya Nair", "role": "manager"},
    {"username": "qa2", "passwordSetting": None, "name": "Meera Iyer", "role": "qa"},
    {"username": "manager2", "passwordSetting": None, "name": "Vikram Rao", "role": "manager"},
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
