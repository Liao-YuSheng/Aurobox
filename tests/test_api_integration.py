import pytest

flask = pytest.importorskip("flask", reason="Flask is required for API integration tests")
pytest.importorskip("flask_sqlalchemy", reason="Flask-SQLAlchemy is required for API integration tests")
Flask = flask.Flask

from aurobox.api import api_bp
from aurobox.models import Door, DoorStatus, db


class DummyClient:
    def __init__(self):
        self.completed_tasks = []

    def custom_complete(self, payload):
        self.completed_tasks.append(payload)
        return {"message": "SUCCESS"}


class DummyController:
    def __init__(self):
        self.client = DummyClient()
        self.custom_calls = []
        self.door_calls = []

    def custom_call2(self, payload):
        self.custom_calls.append(payload)
        return {"message": "SUCCESS", "data": {"task_id": "TASK-001"}}

    def control_doors(self, sn, door_number, operation):
        self.door_calls.append({"sn": sn, "door_number": door_number, "operation": operation})
        return {"message": "SUCCESS"}

    def get_status_summary(self, sn):
        return {"move_state": "IDLE", "sn": sn}


class DummyThread:
    created = []

    def __init__(self, target=None, args=None, daemon=None, kwargs=None):
        self.target = target
        self.args = args or ()
        self.daemon = daemon
        self.kwargs = kwargs or {}
        self.started = False
        DummyThread.created.append(self)

    def start(self):
        # Keep integration tests deterministic; avoid real background loops.
        self.started = True


@pytest.fixture
def api_ctx(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'test_api.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["ROBOT_SN"] = "TEST_SN_001"
    app.config["HOME_POINT_NAME"] = "管理室"
    app.config["CENTRAL_API_BASE_URL"] = "https://central.example.com"

    db.init_app(app)
    app.register_blueprint(api_bp, url_prefix="/api")

    with app.app_context():
        db.create_all()
        for door_number in ("H_01", "H_02", "H_03", "H_04"):
            db.session.add(
                Door(
                    sn=app.config["ROBOT_SN"],
                    door_number=door_number,
                    status=DoorStatus.EMPTY.value,
                )
            )
        db.session.commit()

    controller = DummyController()
    DummyThread.created = []

    monkeypatch.setattr("aurobox.api.get_controller", lambda: controller)
    monkeypatch.setattr("aurobox.api.threading.Thread", DummyThread)
    monkeypatch.setattr("aurobox.api.set_robot_target_point", lambda sn, point: None)
    monkeypatch.setattr("aurobox.api.check_and_return_home_if_empty", lambda: False)

    return {
        "app": app,
        "client": app.test_client(),
        "controller": controller,
    }


def _get_door(app, package_id):
    with app.app_context():
        return Door.query.filter_by(sn=app.config["ROBOT_SN"], package_id=package_id).first()


def test_assign_and_load_flow(api_ctx):
    client = api_ctx["client"]
    app = api_ctx["app"]

    assign_res = client.post("/api/doors/assign", json={"id": "PKG-A"})
    assert assign_res.status_code == 200
    assert assign_res.json["status"] == "success"

    door = _get_door(app, "PKG-A")
    assert door is not None
    assert door.status == DoorStatus.ASSIGNED.value
    assert any(thread.started for thread in DummyThread.created)

    load_res = client.post("/api/doors/load", json={"id": "PKG-A"})
    assert load_res.status_code == 200

    door = _get_door(app, "PKG-A")
    assert door.status == DoorStatus.FULL.value


def test_dispatch_writes_task_id(api_ctx):
    client = api_ctx["client"]
    app = api_ctx["app"]

    client.post("/api/doors/assign", json={"id": "PKG-B"})
    client.post("/api/doors/load", json={"id": "PKG-B"})

    dispatch_res = client.post(
        "/api/robot/dispatch",
        json={"point": "Unit-1201", "package_id": "PKG-B"},
    )

    assert dispatch_res.status_code == 200
    assert dispatch_res.json["task_id"] == "TASK-001"
    assert dispatch_res.json["polling"] is True

    door = _get_door(app, "PKG-B")
    assert door.task_id == "TASK-001"


def test_complete_flow_releases_door(api_ctx):
    client = api_ctx["client"]
    app = api_ctx["app"]

    with app.app_context():
        door = Door.query.filter_by(sn=app.config["ROBOT_SN"], door_number="H_01").first()
        door.package_id = "PKG-C"
        door.task_id = "TASK-C"
        door.status = DoorStatus.FULL.value
        db.session.commit()

    complete_res = client.post("/api/packages/PKG-C/complete")
    assert complete_res.status_code == 200
    assert complete_res.json["status"] == "success"

    with app.app_context():
        door = Door.query.filter_by(sn=app.config["ROBOT_SN"], door_number="H_01").first()
        assert door.status == DoorStatus.EMPTY.value
        assert door.package_id is None
        assert door.task_id is None


def test_cancel_flow_keeps_full_and_clears_task(api_ctx):
    client = api_ctx["client"]
    app = api_ctx["app"]

    with app.app_context():
        door = Door.query.filter_by(sn=app.config["ROBOT_SN"], door_number="H_02").first()
        door.package_id = "PKG-D"
        door.task_id = "TASK-D"
        door.status = DoorStatus.FULL.value
        db.session.commit()

    cancel_res = client.post("/api/packages/PKG-D/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json["status"] == "success"

    with app.app_context():
        door = Door.query.filter_by(sn=app.config["ROBOT_SN"], door_number="H_02").first()
        assert door.status == DoorStatus.FULL.value
        assert door.package_id == "PKG-D"
        assert door.task_id is None
