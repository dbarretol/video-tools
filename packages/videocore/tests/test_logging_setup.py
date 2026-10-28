import logging

from videocore import configure_logging


def test_writes_to_rotating_file_and_is_idempotent(tmp_path):
    root = logging.getLogger()
    before = len(root.handlers)

    log_file = configure_logging("testapp", log_dir=tmp_path)
    configure_logging("testapp", log_dir=tmp_path)
    assert len(root.handlers) == before + 1

    logging.getLogger("videocore.test").warning("hello from test")
    for handler in root.handlers:
        handler.flush()
    assert log_file == tmp_path / "testapp.log"
    assert "hello from test" in log_file.read_text(encoding="utf-8")

    for handler in [h for h in root.handlers if getattr(h, "_videocore", False)]:
        root.removeHandler(handler)
        handler.close()
