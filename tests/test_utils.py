import pytest
from app.core.utils import get_resolution_bucket

def test_get_resolution_bucket():
    # Small: < 0.5 MP
    assert get_resolution_bucket(500, 500) == "small"  # 0.25 MP
    
    # Medium: 0.5 - 2.0 MP
    assert get_resolution_bucket(1000, 1000) == "medium"  # 1.0 MP
    
    # Large: 2.0 - 8.0 MP
    assert get_resolution_bucket(2000, 2000) == "large"  # 4.0 MP
    assert get_resolution_bucket(2828, 2828) == "large"  # ~7.99 MP
    
    # XLarge: > 8.0 MP
    assert get_resolution_bucket(4000, 3000) == "xlarge"  # 12.0 MP

def test_get_resolution_bucket_edge_cases():
    assert get_resolution_bucket(0, 0) == "unknown"
    assert get_resolution_bucket(100, 0) == "unknown"
    assert get_resolution_bucket(0, 100) == "unknown"
