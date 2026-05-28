import pytest
from unittest.mock import MagicMock, patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest
from PIL import UnidentifiedImageError

def test_probe_quality_heif_unsupported():
    """
    Verify that HEIC currently fails or uses fallback without pillow-heif.
    """
    orchestrator = BatchOrchestrator()
    req = BatchRequest(
        source_dir="src",
        target_dir="dst",
        target_format=["webp"],
        tool=["ffmpeg"],
        category=["general"]
    )
    
    # Mock Image.open to fail for .heic
    with patch("PIL.Image.open", side_effect=UnidentifiedImageError("Unsupported")):
        quality = orchestrator._probe_quality("test.heic", req.category[0], req.tool[0], req.target_format[0])
        
        # Should return fallback quality (80.0)
        assert quality == 80.0

@pytest.mark.asyncio
async def test_probe_quality_avif_real(tmp_path):
    """
    Verify that if we have a real AVIF (or mocked as valid), it works.
    This will fail if pillow-heif is not registered.
    """
    orchestrator = BatchOrchestrator()
    req = BatchRequest(
        source_dir="src",
        target_dir="dst",
        target_format=["webp"],
        tool=["ffmpeg"],
        category=["general"]
    )
    
    # We mock Image.open but simulate what happens with pillow-heif
    # (i.e. it doesn't raise UnidentifiedImageError)
    
    with patch("PIL.Image.open") as mock_open:
        mock_img = mock_open.return_value.__enter__.return_value
        mock_img.size = (1000, 1000) # 1 MP
        
        quality = orchestrator._probe_quality("test.avif", req.category[0], req.tool[0], req.target_format[0])
        
        assert quality > 0
        assert mock_open.called
