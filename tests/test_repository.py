import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.horario import HorarioRepository


@pytest.mark.asyncio
async def test_repository_get_all_executes_query():
    """
    Validates that HorarioRepository.get_all properly utilizes
    the injected AsyncSession to execute a database query.
    """
    # 1. Arrange: Create a mock database session
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    # Configure the mock session execution chain to return an empty list safely
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    # 2. Act: Inject the mocked session into our repository
    repo = HorarioRepository(session=mock_session)
    result = await repo.get_all()

    # 3. Assert: Verify the repository interacted with the session correctly
    assert result == []
    mock_session.execute.assert_called_once()
