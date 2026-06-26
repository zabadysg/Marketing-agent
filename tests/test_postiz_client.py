import pytest
import respx
from httpx import Response

from app.clients.postiz import PostizAuthError, PostizClient, PostizError

POSTIZ_BASE_URL = "http://postiz-test:5000"
POSTIZ_API_KEY = "test-api-key-12345"
INTEGRATIONS_URL = f"{POSTIZ_BASE_URL}/public/v1/integrations"

MOCK_INTEGRATIONS = [
    {"id": "int-1", "name": "Twitter @acme", "type": "x"},
    {"id": "int-2", "name": "LinkedIn ACME Corp", "type": "linkedin"},
]


@pytest.fixture
def postiz_client():
    return PostizClient(base_url=POSTIZ_BASE_URL, api_key=POSTIZ_API_KEY)


@pytest.mark.asyncio
async def test_list_integrations_success(postiz_client):
    with respx.mock() as mock:
        mock.get(INTEGRATIONS_URL).mock(
            return_value=Response(200, json=MOCK_INTEGRATIONS)
        )
        integrations = await postiz_client.list_integrations()
        sent_headers = mock.calls.last.request.headers
        assert sent_headers["Authorization"] == POSTIZ_API_KEY
        assert "X-API-Key" not in sent_headers

    assert len(integrations) == 2
    assert integrations[0]["id"] == "int-1"


@pytest.mark.asyncio
async def test_list_integrations_auth_failure(postiz_client):
    with respx.mock() as mock:
        mock.get(INTEGRATIONS_URL).mock(
            return_value=Response(401, json={"error": "Unauthorized"})
        )
        with pytest.raises(PostizAuthError):
            await postiz_client.list_integrations()


@pytest.mark.asyncio
async def test_list_integrations_server_error(postiz_client):
    with respx.mock() as mock:
        mock.get(INTEGRATIONS_URL).mock(
            return_value=Response(500, text="Internal Server Error")
        )
        with pytest.raises(PostizError) as exc_info:
            await postiz_client.list_integrations()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_list_integrations_empty(postiz_client):
    with respx.mock() as mock:
        mock.get(INTEGRATIONS_URL).mock(return_value=Response(200, json=[]))
        integrations = await postiz_client.list_integrations()
    assert integrations == []
