from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import create_app
from backend.config import Settings
from backend.db import Experience, ExperienceEvent
from tests.test_app import login


def review(client, experience_id, **body):
    return client.post(f"/api/admin/experiences/{experience_id}/review", json=body)


def test_draft_submit_review_publish_and_withdraw(tmp_path):
    # 不配置模型密钥：经验的提交、审核和浏览都不依赖模型。
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        alice = login(client, "alice")
        created = client.post("/api/experiences", json={
            "text": "  采购打印纸找行政要比价单  ", "tags": ["采购要求", " 办公用品 ", "采购要求"]})
        assert created.status_code == 201
        draft = created.json()
        path = f"/api/experiences/{draft['id']}"
        assert draft["status"] == "draft" and draft["text"] == "采购打印纸找行政要比价单"
        assert draft["tags"] == ["采购要求", "办公用品"]
        # 草稿只有本人能看到，管理员的工作台里也没有。
        login(client, "bob")
        assert client.get(path).status_code == 404
        assert client.get("/api/experiences/mine").json() == []
        assert client.put(path, json={"text": "改", "tags": ["x"]}).status_code == 404
        login(client)
        assert client.get("/api/admin/experiences").json() == []
        assert client.get(path).status_code == 404
        assert review(client, draft["id"], action="publish").status_code == 404

        login(client, "alice")
        edited = client.put(path, json={"text": "采购打印纸要先找行政要比价单", "tags": ["采购要求"],
                                        "submit": True}).json()
        assert edited["status"] == "pending" and edited["submitted_at"]
        # 提交后本人不能再改或删除；其他员工仍看不到。
        assert client.put(path, json={"text": "再改", "tags": ["采购要求"]}).status_code == 409
        assert client.delete(path).status_code == 409
        assert client.get("/api/admin/experiences").status_code == 403
        assert review(client, draft["id"], action="publish").status_code == 403
        login(client, "bob")
        assert client.get(path).status_code == 404
        assert client.get("/api/experiences").json() == {"items": [], "tags": []}

        login(client)
        pending, = client.get("/api/admin/experiences").json()
        assert pending["author"] == alice["name"] and pending["status"] == "pending"
        assert pending["content"] == pending["text"] == "采购打印纸要先找行政要比价单"
        assert [e["action"] for e in pending["events"]] == ["submit"]
        # 待核实：记录线下核实情况，可多次记录。
        assert review(client, draft["id"], action="verify", note="已电话询问行政").json()["status"] == "verifying"
        assert review(client, draft["id"], action="verify", note="行政确认属实").status_code == 200
        assert review(client, draft["id"], action="withdraw").status_code == 409
        # 管理员调整内容、标记与制度冲突并给提交人留言，然后发布。
        saved = review(client, draft["id"], action="save", content="采购打印纸须先向行政索取比价单，再提交申请。",
                       tags=["采购要求", "行政"], conflict=True, conflict_note="制度未要求比价单",
                       admin_note="已核实，感谢分享").json()
        assert saved["status"] == "verifying" and saved["conflict"]
        published = review(client, draft["id"], action="publish").json()
        assert published["status"] == "published" and published["published_at"]
        listed, = client.get("/api/admin/experiences").json()
        assert [e["action"] for e in listed["events"]] == ["submit", "verify", "verify", "save", "publish"]
        assert listed["events"][2]["note"] == "行政确认属实" and listed["text"] == "采购打印纸要先找行政要比价单"

        # 发布后全员可见调整后的内容，看不到提交人、原文和审核记录。
        login(client, "bob")
        library = client.get("/api/experiences").json()
        item, = library["items"]
        assert library["tags"] == ["行政", "采购要求"]
        assert item == client.get(path).json()
        assert item["content"] == "采购打印纸须先向行政索取比价单，再提交申请。"
        assert item["author"] == "某同事" and alice["name"] not in str(item) and alice["id"] not in str(item)
        assert item["conflict"] and item["conflict_note"] == "制度未要求比价单"
        assert not {"text", "events", "admin_note", "status"} & set(item)
        assert len(client.get("/api/experiences?q=比价单 行政").json()["items"]) == 1
        assert len(client.get("/api/experiences?q=采购要求").json()["items"]) == 1
        assert client.get("/api/experiences?q=报销").json()["items"] == []
        assert len(client.get("/api/experiences?tag=行政").json()["items"]) == 1
        assert client.get("/api/experiences?tag=行").json()["items"] == []
        # 提交人在我的记录里看到状态、留言和发布内容，但没有审核过程。
        login(client, "alice")
        own, = client.get("/api/experiences/mine").json()
        assert own["status"] == "published" and own["admin_note"] == "已核实，感谢分享"
        assert own["text"] == "采购打印纸要先找行政要比价单" and own["content"].startswith("采购打印纸须先")
        assert "events" not in own and "author" not in own

        # 下架后不再对其他人可见，可重新发布；取消冲突标记时清除说明。
        login(client)
        assert review(client, draft["id"], action="withdraw", note="行政流程已变").json()["status"] == "withdrawn"
        login(client, "bob")
        assert client.get(path).status_code == 404 and client.get("/api/experiences").json()["items"] == []
        login(client)
        again = review(client, draft["id"], action="publish", conflict=False).json()
        assert again["status"] == "published" and again["conflict_note"] == ""


def test_reject_delete_draft_and_validation(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/experiences").status_code == 401
        assert client.post("/api/experiences", json={"text": "x", "tags": ["y"]}).status_code == 401
        login(client, "bob")
        for body in [{"text": "  ", "tags": ["a"]}, {"text": "内容", "tags": []},
                     {"text": "内容", "tags": [" "]}, {"text": "内容", "tags": ["长" * 31]},
                     {"text": "内容" * 3000, "tags": ["a"]}, {"text": "内容", "tags": ["a"] * 9}]:
            assert client.post("/api/experiences", json=body).status_code == 422, body
        # 标签必填的提示能直接显示给用户。
        detail = client.post("/api/experiences", json={"text": "内容", "tags": []}).json()["detail"]
        assert "请至少填写一个标签" in detail[0]["msg"]

        draft = client.post("/api/experiences", json={"text": "先记一句", "tags": ["备忘"]}).json()
        assert client.delete(f"/api/experiences/{draft['id']}").json() == {"ok": True}
        assert client.get("/api/experiences/mine").json() == []
        sent = client.post("/api/experiences", json={"text": "报销要贴票", "tags": ["报销"], "submit": True}).json()
        assert sent["status"] == "pending"
        login(client)
        assert review(client, sent["id"], action="unknown").status_code == 422
        assert review(client, sent["id"], action="save", content=" ").status_code == 422
        rejected = review(client, sent["id"], action="reject", admin_note="与现行流程不符", note="已过时").json()
        assert rejected["status"] == "rejected"
        assert review(client, sent["id"], action="publish").status_code == 409
        assert review(client, "missing", action="publish").status_code == 404
        login(client, "bob")
        own, = client.get("/api/experiences/mine").json()
        assert own["status"] == "rejected" and own["admin_note"] == "与现行流程不符" and own["content"] is None
        assert client.get(f"/api/experiences/{sent['id']}").json()["status"] == "rejected"
        assert client.get("/api/experiences").json()["items"] == []

        async def counts():
            async with client.app.state.db.sessions() as session:
                return (await session.scalar(select(func.count()).select_from(Experience)),
                        await session.scalar(select(func.count()).select_from(ExperienceEvent)))
        assert client.portal.call(counts) == (1, 2)
