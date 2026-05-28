import sys
import pytest

def test_batch_repository_importable_without_pandas():
    # Remove pandas from sys.modules to prove the batch path doesn't need it
    # We also need to remove any module that might have already imported pandas
    # specifically app.core.db if it's already in sys.modules
    
    # Identify modules to purge
    to_purge = [k for k in sys.modules if k.startswith("pandas") or k == "app.core.db" or k.startswith("app.core.db.")]
    
    # Save them just in case, though we are in a test process
    saved_modules = {k: sys.modules[k] for k in to_purge if k in sys.modules}
    
    for m in to_purge:
        if m in sys.modules:
            del sys.modules[m]

    try:
        # This should NOT trigger a pandas import if we decoupled it
        from app.core.db.repositories.batch import BatchRepository
        
        # Check if pandas was re-imported
        assert "pandas" not in sys.modules, "BatchRepository must not depend on pandas (directly or indirectly via app.core.db)"
    finally:
        # Restore modules to avoid breaking other tests in the same run
        for k, v in saved_modules.items():
            sys.modules[k] = v

if __name__ == "__main__":
    test_batch_repository_importable_without_pandas()
