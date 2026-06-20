import threading
import time
from app.batch_api.run_control import RunControl

def test_runs_by_default():
    c = RunControl()
    assert c.cancelled is False
    assert c.paused is False
    # wait_if_paused returns immediately when running
    c.wait_if_paused(timeout=0.1)

def test_pause_blocks_until_resume():
    c = RunControl()
    c.pause()
    assert c.paused is True
    released = []
    def worker():
        c.wait_if_paused()
        released.append(True)
    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    assert released == []          # still blocked
    c.resume()
    t.join(timeout=1.0)
    assert released == [True]

def test_cancel_unblocks_paused_waiter():
    c = RunControl()
    c.pause()
    c.cancel()
    assert c.cancelled is True
    c.wait_if_paused(timeout=1.0)  # must not hang
