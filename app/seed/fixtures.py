"""Static seed fixtures: login users, the cellSens application and its four
testing layers. No test data is seeded — records arrive via Excel upload."""

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

LAYERS = [
    {"layerId": "regression", "name": "Regression testing", "short": "REG", "order": 0,
     "desc": "Full regression sweep across cameras, microscopes and core workflows."},
    {"layerId": "system", "name": "System testing", "short": "SYS", "order": 1,
     "desc": "End-to-end verification of the integrated system against requirements."},
    {"layerId": "feature", "name": "Feature testing", "short": "FEAT", "order": 2,
     "desc": "Focused verification of new and changed features per release."},
    {"layerId": "acceptance", "name": "Acceptance testing", "short": "UAT", "order": 3,
     "desc": "Customer-facing acceptance criteria and sign-off scenarios."},
]

DEFAULT_SETTINGS = {
    "repoUrl": "https://github.com/Aadrika8/test_suites.git",
    "branch": "main",
    "cacheDir": "~/TestRunner/cache",
    "timeoutSeconds": 60,
    "rootFolder": "test_suites",
    "levels": ["application", "suite", "release"],
    "extensions": [".py"],
}
