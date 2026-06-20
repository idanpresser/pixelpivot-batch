from app.core.toolcheck import ToolStatus, check_binary, check_sharp_daemon

def test_check_binary_missing(tmp_path):
    st = check_binary("nonexistent_ffmpeg", str(tmp_path / "nope.exe"))
    assert isinstance(st, ToolStatus)
    assert st.name == "nonexistent_ffmpeg"
    assert st.ok is False

def test_check_binary_present(tmp_path):
    fake = tmp_path / "magick.exe"
    fake.write_text("x")
    st = check_binary("magick", str(fake))
    assert st.ok is True
    assert st.detail and str(fake) in st.detail

def test_check_sharp_daemon_down_on_closed_port():
    # Port 1 is privileged/unused; connection must fail fast.
    st = check_sharp_daemon(port=1, timeout=0.2)
    assert st.name == "sharp"
    assert st.ok is False
