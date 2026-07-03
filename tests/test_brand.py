import pytest


BRAND_PAYLOAD = {
    "brand_name": "Acme Corp",
    "company_name": "Acme Corporation",
    "industry": "SaaS",
    "tone": "professional",
    "avoid": ["spam", "clickbait"],
    "audience_segments": [
        {
            "name": "SMB Founders",
            "description": "Small business owners",
            "pain_points": ["too much manual work"],
            "channels": ["LinkedIn"],
        }
    ],
    "goals": ["grow LinkedIn following", "launch new product"],
}


@pytest.mark.asyncio
async def test_upsert_then_get_brand(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Brand WS"})
    workspace_id = ws.json()["id"]

    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile", json=BRAND_PAYLOAD
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand_name"] == "Acme Corp"
    assert body["company_name"] == "Acme Corporation"
    assert body["industry"] == "SaaS"
    assert body["avoid"] == ["spam", "clickbait"]
    assert body["workspace_id"] == workspace_id
    assert len(body["audience_segments"]) == 1
    assert body["audience_segments"][0]["name"] == "SMB Founders"
    assert body["goals"] == ["grow LinkedIn following", "launch new product"]
    assert body["onboarding_status"] == "in_progress"

    resp2 = await test_client.get(f"/api/workspaces/{workspace_id}/brand-profile")
    assert resp2.status_code == 200
    assert resp2.json()["tone"] == "professional"


@pytest.mark.asyncio
async def test_second_put_updates_brand(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Brand WS 2"})
    workspace_id = ws.json()["id"]

    await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile", json=BRAND_PAYLOAD
    )

    updated = {**BRAND_PAYLOAD, "tone": "casual"}
    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile", json=updated
    )
    assert resp.status_code == 200
    assert resp.json()["tone"] == "casual"


@pytest.mark.asyncio
async def test_get_brand_not_set(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Empty WS"})
    workspace_id = ws.json()["id"]
    resp = await test_client.get(f"/api/workspaces/{workspace_id}/brand-profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_partial_update_preserves_existing_fields(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Partial WS"})
    workspace_id = ws.json()["id"]

    await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile", json=BRAND_PAYLOAD
    )

    # Only update tone — other fields should remain unchanged
    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile",
        json={"tone": "witty"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tone"] == "witty"
    assert body["brand_name"] == "Acme Corp"
    assert body["industry"] == "SaaS"


@pytest.mark.asyncio
async def test_onboarding_status_can_be_set(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Onboarding WS"})
    workspace_id = ws.json()["id"]

    await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile", json=BRAND_PAYLOAD
    )

    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand-profile",
        json={"onboarding_status": "active"},
    )
    assert resp.status_code == 200
    assert resp.json()["onboarding_status"] == "active"


@pytest.mark.asyncio
async def test_deprecated_brand_alias_still_works(test_client):
    ws = await test_client.post("/api/workspaces", json={"name": "Alias WS"})
    workspace_id = ws.json()["id"]

    resp = await test_client.put(
        f"/api/workspaces/{workspace_id}/brand",
        json={"brand_name": "Alias Brand", "tone": "bold"},
    )
    assert resp.status_code == 200
    assert resp.json()["brand_name"] == "Alias Brand"

    resp2 = await test_client.get(f"/api/workspaces/{workspace_id}/brand")
    assert resp2.status_code == 200
    assert resp2.json()["brand_name"] == "Alias Brand"
