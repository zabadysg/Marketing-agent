import pytest


BRAND_PAYLOAD = {
    "name": "Acme Corp",
    "audience": "SMB owners",
    "tone": "professional",
    "language": "en",
    "avoid": ["spam", "clickbait"],
}


@pytest.mark.asyncio
async def test_upsert_then_get_brand(test_client):
    # Create workspace first
    ws = await test_client.post("/api/workspaces", json={"name": "Brand WS"})
    workspace_id = ws.json()["id"]

    # PUT brand profile
    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand", json=BRAND_PAYLOAD
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Acme Corp"
    assert body["avoid"] == ["spam", "clickbait"]
    assert body["workspace_id"] == workspace_id

    # GET returns same data
    resp2 = await test_client.get(f"/api/workspaces/{workspace_id}/brand")
    assert resp2.status_code == 200
    assert resp2.json()["tone"] == "professional"


@pytest.mark.asyncio
async def test_second_put_updates_brand(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Brand WS 2"})
    workspace_id = ws.json()["id"]

    await test_client.put(f"/api/workspaces/{workspace_id}/brand", json=BRAND_PAYLOAD)

    updated = {**BRAND_PAYLOAD, "tone": "casual"}
    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand", json=updated
    )
    assert resp.status_code == 200
    assert resp.json()["tone"] == "casual"


@pytest.mark.asyncio
async def test_get_brand_not_set(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Empty WS"})
    workspace_id = ws.json()["id"]
    resp = await test_client.get(f"/api/workspaces/{workspace_id}/brand")
    assert resp.status_code == 404
