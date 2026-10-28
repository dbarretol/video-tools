import av
import PIL

import videocore


def test_version_is_exposed():
    assert videocore.__version__ == "0.1.0"


def test_runtime_dependencies_import():
    assert av.__version__
    assert PIL.__version__
